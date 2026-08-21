"""Resumable paired Phase 3.1 development matrix on a frozen fresh-seed pool."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import multiprocessing as mp
import os
import random
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json
from experiments.evaluate_cohort_mpc_pilot import SCENARIO_KINDS, build_pilot_scenario
from experiments.run_campaign_shard import source_fingerprint
from experiments.seed_policy import reject_protected_mpc_seeds
from forecasting import MovingAverageForecaster, load_forecaster_bundle
from optimization import (
    CohortMPCConfig, PreDrainFlowConfig,
    load_survival_guardrail_evidence, load_survival_tables,
)
from simulator.macro import Simulator, load_scenario
from simulator.macro.controllers import (
    CohortMPCController, PreDrainFlowController, StaticCapacityController,
)


EXPECTED_FALLBACK_PREFIXES = (
    "insufficient_multi_horizon_forecast_history", "minimum_hold_period",
    "solve_trigger_safe", "stale_or_insufficient_survival:",
    "no_known_future_capacity_event", "observed_unplanned_capacity_state",
    "telemetry_uncertainty", "same_state_static_certificate:",
    "no_known_reduction_in_lead_horizon",
    "predicted_overflow_exceeds_tolerance",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_sha256(path: Path) -> str:
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(bytes.fromhex(_sha256(item)))
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _single_thread() -> None:
    for name in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
    ):
        os.environ[name] = "1"


def _reduction(static: float, candidate: float) -> float:
    return (static - candidate) / static if static > 0 else 0.0


def _bootstrap(values: list[float], samples: int = 10_000) -> list[float]:
    rng = random.Random(20260820)
    estimates = sorted(
        sum(values[rng.randrange(len(values))] for _ in values) / len(values)
        for _ in range(samples)
    )
    return [estimates[round(.025 * (samples - 1))], estimates[round(.975 * (samples - 1))]]


def _resolve(relative: str | None) -> Path | None:
    return PROJECT_ROOT / relative if relative is not None else None


def _predicted_overflow_gate(
    records: list[dict[str, Any]], *, tolerance: float,
) -> tuple[bool, float]:
    maximum = max(
        (
            float(item.get("overflow", 0.0))
            for row in records for item in row["decision_diagnostics"]
        ),
        default=0.0,
    )
    return maximum <= tolerance, maximum


def _controller(candidate: dict[str, Any]):
    if candidate["controller"] == "predrain":
        return PreDrainFlowController(
            flow_config=PreDrainFlowConfig(**candidate["flow"])
        )
    profile_path = _resolve(candidate["profile"])
    bundle_path = _resolve(candidate.get("forecast_bundle"))
    assert profile_path is not None
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if "moving_average_history_windows" in candidate:
        forecaster = MovingAverageForecaster(
            int(candidate["moving_average_history_windows"])
        )
    else:
        assert bundle_path is not None
        forecaster = load_forecaster_bundle(bundle_path)
    survival_path = _resolve(candidate.get("survival_bundle"))
    survival = load_survival_tables(str(survival_path)) if survival_path else None
    evidence = (
        load_survival_guardrail_evidence(str(survival_path)) if survival_path else None
    )
    return CohortMPCController(
        forecaster=forecaster,
        mpc_config=CohortMPCConfig(**profile["mpc"]),
        survival_by_group=survival,
        survival_guardrail_evidence=evidence,
    )


def _preflight_candidate(candidate: dict[str, Any], *, decision_epochs: int) -> None:
    """Reject an MPC experiment that cannot possibly reach its first solve."""
    if candidate["controller"] != "mpc":
        return
    required = _controller(candidate).required_history_windows
    maximum_completed = max(0, decision_epochs - 1)
    if required > maximum_completed:
        raise ValueError(
            f"candidate {candidate['candidate_id']} requires {required} completed "
            f"forecast windows, but the experiment can provide only "
            f"{maximum_completed}; extend the horizon or use a causal short-history forecaster"
        )


def _validate_frozen_inputs(value: Any) -> None:
    if isinstance(value, dict) and set(value) >= {"path", "sha256"}:
        path = PROJECT_ROOT / value["path"]
        if not path.is_file() or _sha256(path) != value["sha256"]:
            raise ValueError(f"Phase 3.1 frozen input changed or is missing: {path}")
        return
    if isinstance(value, dict):
        for nested in value.values():
            _validate_frozen_inputs(nested)


def _evaluate_pair(
    manifest: str, candidate: dict[str, Any], kind: str, seed: int,
    steps: int, fingerprint: str,
) -> dict[str, Any]:
    _single_thread()
    base = load_scenario(manifest)
    horizon = (
        int(candidate["flow"]["lead_windows"])
        if candidate["controller"] == "predrain"
        else int(json.loads(_resolve(candidate["profile"]).read_text())["mpc"]["horizon_windows"])
    )
    scenario, events = build_pilot_scenario(
        base, kind=kind, seed=seed, steps=steps, horizon_windows=horizon,
    )
    started = time.monotonic()
    static_simulator = Simulator(scenario, StaticCapacityController())
    static = static_simulator.run(static_simulator.make_summary_sink()).summary
    static_control = static_simulator.control_metrics()
    controller = _controller(candidate)
    candidate_simulator = Simulator(scenario, controller)
    controlled = candidate_simulator.run(
        candidate_simulator.make_summary_sink()
    ).summary
    control = candidate_simulator.control_metrics()
    reductions = {
        metric: {
            direction: _reduction(static[metric][direction], controlled[metric][direction])
            for direction in ("ul", "dl")
        }
        for metric in ("overload_area_seconds", "dropped_bytes")
    }
    unexpected = sum(
        int(count) for reason, count in control["decision_reasons"].items()
        if reason != "applied"
        and not any(reason.startswith(prefix) for prefix in EXPECTED_FALLBACK_PREFIXES)
    )
    return {
        "schema_version": "phase3.1-candidate-pair/1.0",
        "work_fingerprint": fingerprint,
        "candidate_id": candidate["candidate_id"],
        "controller_type": candidate["controller"],
        "scenario_kind": kind,
        "scenario_id": scenario.scenario_id,
        "seed": seed,
        "events": events,
        "static": static,
        "candidate": controlled,
        "relative_reduction": reductions,
        "controller_decisions": controller.decision_count,
        "controller_groups": len(base.groups),
        "certified_decisions": controller.certified_decision_count,
        "baseline_routing_churn_l1": static_control["routing_churn_l1"],
        "candidate_routing_churn_l1": control["routing_churn_l1"],
        "decision_reasons": control["decision_reasons"],
        "solver_statuses": control["solver_statuses"],
        "solver_timeout_count": control["solver_timeout_count"],
        "solver_error_count": int(control["solver_statuses"].get("error", 0)),
        "unexpected_fallback_decision_count": unexpected,
        "decision_funnel": control["decision_funnel"],
        "decision_diagnostics": control["decision_diagnostics"],
        "survival_guardrail_evidence": control["survival_guardrail_evidence"],
        "imperfect_survival_guardrail_passed": control[
            "imperfect_survival_guardrail_passed"
        ],
        "wall_seconds": time.monotonic() - started,
    }


def _aggregate(
    candidate: dict[str, Any], records: list[dict[str, Any]], *, steps: int,
    campaign_schema: str = "phase3.1",
) -> dict[str, Any]:
    records.sort(key=lambda row: (SCENARIO_KINDS.index(row["scenario_kind"]), row["seed"]))
    totals = {
        controller: {
            metric: {
                direction: sum(row[controller][metric][direction] for row in records)
                for direction in ("ul", "dl")
            }
            for metric in ("overload_area_seconds", "dropped_bytes")
        }
        for controller in ("static", "candidate")
    }
    reductions = {
        metric: {
            direction: _reduction(
                totals["static"][metric][direction], totals["candidate"][metric][direction]
            )
            for direction in ("ul", "dl")
        }
        for metric in ("overload_area_seconds", "dropped_bytes")
    }
    pair_ul = [row["relative_reduction"]["overload_area_seconds"]["ul"] for row in records]
    confidence = _bootstrap(pair_ul)
    static_failures = sum(row["static"]["establishment_failures"] for row in records)
    candidate_failures = sum(row["candidate"]["establishment_failures"] for row in records)
    guardrails = {
        "no_dl_overload_regression": reductions["overload_area_seconds"]["dl"] >= 0,
        "no_ul_drop_regression": reductions["dropped_bytes"]["ul"] >= 0,
        "no_dl_drop_regression": reductions["dropped_bytes"]["dl"] >= 0,
        "no_session_failure_regression": candidate_failures <= static_failures,
    }
    stressed = [row for row in records if row["scenario_kind"] in {"unannounced_outage", "mixed_stress"}]
    stressed_reduction = _reduction(
        sum(row["static"]["overload_area_seconds"]["ul"] for row in stressed),
        sum(row["candidate"]["overload_area_seconds"]["ul"] for row in stressed),
    )
    timeouts = sum(row["solver_timeout_count"] for row in records)
    errors = sum(row["solver_error_count"] for row in records)
    unexpected = sum(row["unexpected_fallback_decision_count"] for row in records)
    decisions = sum(row["controller_decisions"] for row in records)
    skipped = sum(int(row["solver_statuses"].get("skipped", 0)) for row in records)
    churn_denominator = sum(row["controller_decisions"] * row["controller_groups"] for row in records)
    normalized_churn = sum(row["candidate_routing_churn_l1"] for row in records) / churn_denominator
    survival_gate = (
        candidate["controller"] == "predrain"
        or (
            not candidate.get("non_deployable_survival_diagnostic", False)
            and all(
                row["imperfect_survival_guardrail_passed"]
                and row["survival_guardrail_evidence"].get("measured", False)
                for row in records
            )
        )
    )
    decision_runtimes = [
        float(item.get("decision_runtime_ms", 0))
        for row in records for item in row["decision_diagnostics"]
    ]
    decision_deadline_ms = float(
        candidate.get("end_to_end_decision_deadline_ms", 2_000)
    )
    overflow_tolerance = float(candidate.get("flow", {}).get("overflow_tolerance", 1e-7))
    zero_predicted_overflow, max_predicted_overflow = _predicted_overflow_gate(
        records, tolerance=overflow_tolerance
    )
    gates = {
        "mean_pair_ul_improvement_at_least_10_percent": mean(pair_ul) >= .10,
        "bootstrap_lower_bound_above_zero": confidence[0] > 0,
        "positive_severity_weighted_improvement": reductions["overload_area_seconds"]["ul"] > 0,
        "unknown_mixed_regression_no_worse_than_minus_2_percent": stressed_reduction >= -.02,
        "worst_pair_better_than_minus_10_percent": min(pair_ul) > -.10,
        "no_dl_overload_drop_or_establishment_regression": all(guardrails.values()),
        "no_solver_timeout_or_error": timeouts == 0 and errors == 0,
        "unexpected_fallback_fraction_within_1_percent": unexpected <= max(1, int(.01 * decisions)),
        "skipped_decision_fraction_within_95_percent": skipped / decisions <= .95,
        "normalized_churn_within_0_05_l1_per_group_decision": normalized_churn <= .05,
        "measured_empirical_survival_robustness": survival_gate,
        "end_to_end_decision_latency_within_candidate_deadline": (
            max(decision_runtimes, default=0) <= decision_deadline_ms
        ),
        "zero_predicted_overflow": zero_predicted_overflow,
    }
    funnel = Counter()
    diagnostics = []
    for row in records:
        funnel.update(row["decision_funnel"])
        diagnostics.extend(row["decision_diagnostics"])
    runtimes = [item["solver_runtime_ms"] for item in diagnostics if item["solver_status"] != "skipped"]
    model_variables = [item["model_variables"] for item in diagnostics if item["model_variables"]]
    return {
        "schema_version": f"{campaign_schema}-candidate-evaluation/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evaluation_stage": "development",
        "development_only": True,
        "candidate": candidate,
        "paired_runs": len(records),
        "seeds": sorted(row["seed"] for row in records),
        "protected_seeds_consumed": False,
        "simulated_days_per_pair": steps * 30 / 86_400,
        "totals": totals,
        "aggregate_relative_reduction": reductions,
        "mean_pair_ul_overload_area_relative_reduction": mean(pair_ul),
        "mean_pair_ul_reduction_bootstrap_95_interval": confidence,
        "worst_pair_ul_overload_area_relative_reduction": min(pair_ul),
        "unknown_outage_mixed_ul_improvement": stressed_reduction,
        "aggregate_guardrails": guardrails,
        "operational": {
            "solver_timeouts": timeouts, "solver_errors": errors,
            "unexpected_fallbacks": unexpected,
            "skipped_decision_fraction": skipped / decisions,
            "normalized_churn_l1_per_group_decision": normalized_churn,
            "mean_solver_runtime_ms": mean(runtimes) if runtimes else 0,
            "max_solver_runtime_ms": max(runtimes, default=0),
            "end_to_end_decision_deadline_ms": decision_deadline_ms,
            "mean_decision_runtime_ms": mean(decision_runtimes) if decision_runtimes else 0,
            "max_decision_runtime_ms": max(decision_runtimes, default=0),
            "mean_model_variables": mean(model_variables) if model_variables else 0,
            "max_predicted_overflow": max_predicted_overflow,
            "predicted_overflow_tolerance": overflow_tolerance,
        },
        "decision_funnel": dict(sorted(funnel.items())),
        "development_gates": gates,
        "passes_all_development_gates": all(gates.values()),
        "decision": "eligible_for_validation_freeze" if all(gates.values()) else "retain_static",
        "by_scenario": {
            kind: {
                "pairs": sum(row["scenario_kind"] == kind for row in records),
                "mean_ul_reduction": mean(
                    row["relative_reduction"]["overload_area_seconds"]["ul"]
                    for row in records if row["scenario_kind"] == kind
                ),
            }
            for kind in SCENARIO_KINDS
        },
        "pairs": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidate-matrix", type=Path, required=True)
    parser.add_argument("--interface-freeze", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=125)
    parser.add_argument("--steps", type=int, default=2880)
    parser.add_argument("--seed-start", type=int, default=46401)
    parser.add_argument("--total-seeds", type=int, default=24)
    args = parser.parse_args()
    if not 1 <= args.workers <= 125:
        raise ValueError("workers must be in [1, 125]")
    base_scenario = load_scenario(args.manifest)
    if args.steps * base_scenario.step_seconds < 86_400:
        raise ValueError("Phase 3.1 pairs must cover at least one simulated day")
    seeds = list(range(args.seed_start, args.seed_start + args.total_seeds))
    matrix = json.loads(args.candidate_matrix.read_text(encoding="utf-8"))
    freeze = json.loads(args.interface_freeze.read_text(encoding="utf-8"))
    if freeze.get("schema_version") not in {
        "phase3.1-interface-freeze/1.0", "phase3.2-interface-freeze/1.0",
    } or not freeze.get("frozen"):
        raise ValueError("control-development interface freeze is invalid")
    campaign_schema = str(freeze.get("campaign_schema", "phase3.1"))
    frozen_matrix = PROJECT_ROOT / freeze["candidate_matrix"]["path"]
    if args.candidate_matrix.resolve() != frozen_matrix.resolve():
        raise ValueError("candidate matrix does not match the frozen matrix path")
    frozen_manifest = PROJECT_ROOT / freeze["immutable_inputs"]["manifest"]["path"]
    if args.manifest.resolve() != frozen_manifest.resolve():
        raise ValueError("scenario manifest does not match the frozen manifest path")
    if seeds != freeze.get("fresh_development_seeds"):
        raise ValueError("requested seeds do not exactly match the frozen development pool")
    reject_protected_mpc_seeds(seeds)
    for relative, expected in freeze["interfaces"].items():
        if _sha256(PROJECT_ROOT / relative) != expected:
            raise ValueError(f"Phase 3.1 frozen interface changed: {relative}")
    _validate_frozen_inputs(freeze["immutable_inputs"])
    candidates = matrix["candidates"]
    for candidate in candidates:
        for key in ("profile", "forecast_bundle", "survival_bundle"):
            path = _resolve(candidate.get(key))
            if path is not None and not path.is_file():
                raise FileNotFoundError(f"candidate input is missing: {path}")
        _preflight_candidate(
            candidate,
            decision_epochs=args.steps // base_scenario.decision_interval_steps,
        )
    tasks_by_seed = []
    base_count, remainder = divmod(len(seeds), len(SCENARIO_KINDS))
    cursor = 0
    for index, kind in enumerate(SCENARIO_KINDS):
        count = base_count + (1 if index < remainder else 0)
        tasks_by_seed.extend((kind, seed) for seed in seeds[cursor:cursor + count])
        cursor += count
    fingerprint = _canonical_sha256({
        "source": source_fingerprint(PROJECT_ROOT),
        "manifest": _sha256(args.manifest),
        "matrix": _sha256(args.candidate_matrix),
        "freeze": _sha256(args.interface_freeze),
        "steps": args.steps,
        "seeds": seeds,
    })
    tasks = [
        (candidate, kind, seed)
        for candidate in candidates for kind, seed in tasks_by_seed
    ]
    args.work_dir.mkdir(parents=True, exist_ok=True)
    completed: dict[tuple[str, str, int], dict[str, Any]] = {}
    pending = []
    for candidate, kind, seed in tasks:
        key = (candidate["candidate_id"], kind, seed)
        path = args.work_dir / f"{key[0]}-{kind}-{seed}.json"
        if path.is_file():
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("work_fingerprint") != fingerprint:
                raise ValueError(f"stale checkpoint: {path}")
            completed[key] = row
        else:
            pending.append((candidate, kind, seed, path))
    print(json.dumps({
        "tasks": len(tasks), "pending": len(pending), "resumed": len(completed),
        "workers": args.workers, "fingerprint": fingerprint,
    }, sort_keys=True), flush=True)
    _single_thread()
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers, mp_context=mp.get_context("spawn")
    ) as pool:
        futures = {
            pool.submit(
                _evaluate_pair, str(args.manifest), candidate, kind, seed,
                args.steps, fingerprint,
            ): (candidate["candidate_id"], kind, seed, path)
            for candidate, kind, seed, path in pending
        }
        for future in concurrent.futures.as_completed(futures):
            candidate_id, kind, seed, path = futures[future]
            row = future.result()
            atomic_json(path, row)
            completed[(candidate_id, kind, seed)] = row
            print(
                f"completed={len(completed)}/{len(tasks)} candidate={candidate_id} "
                f"kind={kind} seed={seed} wall={row['wall_seconds']:.1f}s",
                flush=True,
            )
    evaluations = []
    args.output_root.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        candidate_records = [
            completed[(candidate["candidate_id"], kind, seed)]
            for kind, seed in tasks_by_seed
        ]
        evaluation = _aggregate(
            candidate, candidate_records, steps=args.steps,
            campaign_schema=campaign_schema,
        )
        atomic_json(
            args.output_root / candidate["candidate_id"] / "evaluation.json",
            evaluation,
        )
        evaluations.append(evaluation)
    eligible = [
        item["candidate"]["candidate_id"] for item in evaluations
        if item["passes_all_development_gates"]
    ]
    decision = {
        "schema_version": f"{campaign_schema}-development-decision/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "paired_runs": len(tasks),
        "fresh_development_seeds": seeds,
        "protected_validation_seeds_consumed": False,
        "protected_release_seeds_consumed": False,
        "eligible_candidates": eligible,
        "decision": "freeze_for_validation" if eligible else "retain_static",
        "evaluations": {
            item["candidate"]["candidate_id"]: {
                "passes": item["passes_all_development_gates"],
                "mean_ul_reduction": item["mean_pair_ul_overload_area_relative_reduction"],
                "confidence": item["mean_pair_ul_reduction_bootstrap_95_interval"],
                "timeouts": item["operational"]["solver_timeouts"],
            }
            for item in evaluations
        },
    }
    atomic_json(args.output_root / "DEVELOPMENT_DECISION.json", decision)
    print(json.dumps(decision, sort_keys=True))


if __name__ == "__main__":
    main()
