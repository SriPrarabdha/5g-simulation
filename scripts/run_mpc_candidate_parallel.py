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
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json
from experiments.evaluate_cohort_mpc_candidate import SCHEMA_VERSION
from experiments.evaluate_cohort_mpc_pilot import (
    RESERVED_SEEDS,
    SCENARIO_KINDS,
    ScenarioKind,
    build_pilot_scenario,
)
from experiments.run_campaign_shard import source_fingerprint
from forecasting import load_forecaster_bundle
from optimization import (
    CohortMPCConfig, load_survival_guardrail_evidence, load_survival_tables,
)
from simulator.macro import Simulator, load_scenario
from simulator.macro.controllers import CohortMPCController, StaticCapacityController


CONTROL_SCIENCE_SEED_SPLITS = {
    "development": tuple(range(46101, 46113)),
    "validation": tuple(range(46201, 46217)),
}
UNTOUCHED_RELEASE_SEEDS = frozenset(range(46301, 46331))
EXPECTED_STATIC_REASON_PREFIXES = (
    "insufficient_multi_horizon_forecast_history",
    "minimum_hold_period",
    "solve_trigger_safe",
    "stale_or_insufficient_survival:",
    "no_known_future_capacity_event",
    "observed_unplanned_capacity_state",
    "telemetry_uncertainty",
    "same_state_static_certificate:no_known_future_capacity_event",
    "same_state_static_certificate:observed_unplanned_capacity_state",
    "same_state_static_certificate:candidate_",
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
        digest.update(str(item.relative_to(path)).encode())
        digest.update(bytes.fromhex(_sha256(item)))
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _verify_interface_freeze(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "phase3-interface-freeze/1.0" or not payload.get("frozen"):
        raise ValueError("invalid or unsealed Phase-3 interface freeze")
    for relative, expected in payload.get("interfaces", {}).items():
        candidate = PROJECT_ROOT / relative
        if not candidate.is_file() or _sha256(candidate) != expected:
            raise ValueError(f"Phase-3 interface changed after freeze: {relative}")
    return _sha256(path)


def _single_thread() -> None:
    for name in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
    ):
        os.environ[name] = "1"


def _reduction(static: float, candidate: float) -> float:
    return (static - candidate) / static if static > 0 else 0.0


def _bootstrap_mean_interval(values: list[float], samples: int = 10_000) -> list[float]:
    rng = random.Random(20260807)
    estimates = sorted(
        sum(values[rng.randrange(len(values))] for _ in values) / len(values)
        for _ in range(samples)
    )
    return [estimates[round(0.025 * (samples - 1))], estimates[round(0.975 * (samples - 1))]]


def _evaluate_pair(
    manifest: str, profile_path: str, bundle_path: str, kind: ScenarioKind,
    seed: int, steps: int, fingerprint: str, survival_bundle: str | None,
) -> dict[str, Any]:
    _single_thread()
    base = load_scenario(Path(manifest))
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    settings = CohortMPCConfig(**profile.get("mpc", {}))
    forecaster = load_forecaster_bundle(Path(bundle_path))
    forecaster.validate_groups(group.key for group in base.groups)
    scenario, events = build_pilot_scenario(
        base, kind=kind, seed=seed, steps=steps,
        horizon_windows=settings.horizon_windows,
    )
    started = time.monotonic()
    static_simulator = Simulator(scenario, StaticCapacityController())
    static = static_simulator.run(static_simulator.make_summary_sink()).summary
    static_control = static_simulator.control_metrics()
    survival = load_survival_tables(survival_bundle) if survival_bundle else None
    survival_evidence = (
        load_survival_guardrail_evidence(survival_bundle)
        if survival_bundle else None
    )
    controller = CohortMPCController(
        forecaster=forecaster, mpc_config=settings, survival_by_group=survival,
        survival_guardrail_evidence=survival_evidence,
    )
    mpc_simulator = Simulator(scenario, controller)
    mpc = mpc_simulator.run(mpc_simulator.make_summary_sink()).summary
    mpc_control = mpc_simulator.control_metrics()
    fallback_decisions = sum(
        int(count) for reason, count in mpc_control["decision_reasons"].items()
        if reason != "applied"
    )
    unexpected_fallbacks = sum(
        int(count) for reason, count in mpc_control["decision_reasons"].items()
        if reason != "applied"
        and not any(reason.startswith(prefix) for prefix in EXPECTED_STATIC_REASON_PREFIXES)
    )
    reductions = {
        metric: {
            direction: _reduction(static[metric][direction], mpc[metric][direction])
            for direction in ("ul", "dl")
        }
        for metric in ("overload_area_seconds", "dropped_bytes")
    }
    return {
        "schema_version": "cohort-mpc-candidate-pair/1.0",
        "work_fingerprint": fingerprint,
        "scenario_kind": kind,
        "scenario_id": scenario.scenario_id,
        "seed": seed,
        "events": events,
        "static": static,
        "mpc": mpc,
        "relative_reduction": reductions,
        "controller_decisions": controller.decision_count,
        "controller_groups": len(base.groups),
        "certified_decisions": controller.certified_decision_count,
        "baseline_routing_churn_l1": static_control["routing_churn_l1"],
        "mpc_routing_churn_l1": mpc_control["routing_churn_l1"],
        "decision_reasons": mpc_control["decision_reasons"],
        "solver_statuses": mpc_control["solver_statuses"],
        "solver_timeout_count": mpc_control["solver_timeout_count"],
        "solver_error_count": int(mpc_control["solver_statuses"].get("error", 0)),
        "solver_skipped_count": int(mpc_control["solver_statuses"].get("skipped", 0)),
        "fallback_decision_count": fallback_decisions,
        "unexpected_fallback_decision_count": unexpected_fallbacks,
        "survival": mpc_control["survival"],
        "survival_guardrail_evidence": mpc_control["survival_guardrail_evidence"],
        "imperfect_survival_guardrail_passed": mpc_control[
            "imperfect_survival_guardrail_passed"
        ],
        "wall_seconds": time.monotonic() - started,
    }


def aggregate(
    records: list[dict[str, Any]], *, manifest: Path, profile_path: Path,
    bundle_path: Path, steps: int, settings: CohortMPCConfig,
    evaluation_stage: str = "promotion",
) -> dict[str, Any]:
    records = sorted(records, key=lambda row: (SCENARIO_KINDS.index(row["scenario_kind"]), row["seed"]))
    totals = {
        controller: {
            metric: {
                direction: sum(item[controller][metric][direction] for item in records)
                for direction in ("ul", "dl")
            }
            for metric in ("overload_area_seconds", "dropped_bytes")
        }
        for controller in ("static", "mpc")
    }
    aggregate_reductions = {
        metric: {
            direction: _reduction(totals["static"][metric][direction], totals["mpc"][metric][direction])
            for direction in ("ul", "dl")
        }
        for metric in ("overload_area_seconds", "dropped_bytes")
    }
    static_failures = sum(item["static"]["establishment_failures"] for item in records)
    mpc_failures = sum(item["mpc"]["establishment_failures"] for item in records)
    guardrails = {
        "no_dl_overload_regression": aggregate_reductions["overload_area_seconds"]["dl"] >= 0,
        "no_ul_drop_regression": aggregate_reductions["dropped_bytes"]["ul"] >= 0,
        "no_dl_drop_regression": aggregate_reductions["dropped_bytes"]["dl"] >= 0,
        "no_session_failure_regression": mpc_failures <= static_failures,
    }
    pair_ul = [item["relative_reduction"]["overload_area_seconds"]["ul"] for item in records]
    mean_pair_ul = sum(pair_ul) / len(pair_ul)
    confidence = _bootstrap_mean_interval(pair_ul)
    passes = mean_pair_ul >= 0.10 and confidence[0] > 0 and all(guardrails.values())
    stressed = [
        item for item in records
        if item["scenario_kind"] in {"unannounced_outage", "mixed_stress"}
    ]
    stressed_static = sum(item["static"]["overload_area_seconds"]["ul"] for item in stressed)
    stressed_mpc = sum(item["mpc"]["overload_area_seconds"]["ul"] for item in stressed)
    stressed_reduction = _reduction(stressed_static, stressed_mpc)
    solver_status_available = all("solver_statuses" in item for item in records)
    solver_ok = solver_status_available and all(
        int(item.get("solver_timeout_count", 0)) == 0
        and int(item.get("solver_error_count", 0)) == 0
        for item in records
    )
    unexpected_fallbacks = sum(
        int(item.get("unexpected_fallback_decision_count", 0)) for item in records
    )
    decisions = sum(int(item.get("controller_decisions", 0)) for item in records)
    fallback_ok = solver_status_available and unexpected_fallbacks <= max(1, int(0.01 * decisions))
    skipped_decisions = sum(
        int(item.get("solver_statuses", {}).get("skipped", 0)) for item in records
    )
    skipped_fraction = skipped_decisions / decisions if decisions else 1.0
    churn_denominator = sum(
        int(item.get("controller_decisions", 0)) * int(item.get("controller_groups", 0))
        for item in records
    )
    normalized_churn = (
        sum(float(item.get("mpc_routing_churn_l1", 0.0)) for item in records)
        / churn_denominator if churn_denominator else float("inf")
    )
    survival_measured = all(
        bool(item.get("imperfect_survival_guardrail_passed", False))
        and bool(item.get("survival_guardrail_evidence", {}).get("measured", False))
        for item in records
    )
    development_gates = {
        "mean_pair_ul_improvement_at_least_10_percent": mean_pair_ul >= 0.10,
        "bootstrap_lower_bound_above_zero": confidence[0] > 0,
        "positive_severity_weighted_improvement": aggregate_reductions["overload_area_seconds"]["ul"] > 0,
        "unknown_mixed_regression_no_worse_than_minus_2_percent": stressed_reduction >= -0.02,
        "worst_pair_better_than_minus_10_percent": min(pair_ul) > -0.10,
        "no_dl_overload_drop_or_establishment_regression": all(guardrails.values()),
        "no_solver_timeout_or_error": solver_ok,
        "unexpected_fallback_fraction_within_1_percent": fallback_ok,
        "skipped_decision_fraction_within_95_percent": skipped_fraction <= 0.95,
        "normalized_churn_within_0_05_l1_per_group_decision": normalized_churn <= 0.05,
        "measured_empirical_survival_robustness": survival_measured,
    }
    by_scenario = {}
    for kind in SCENARIO_KINDS:
        selected = [item for item in records if item["scenario_kind"] == kind]
        static_ul = sum(item["static"]["overload_area_seconds"]["ul"] for item in selected)
        mpc_ul = sum(item["mpc"]["overload_area_seconds"]["ul"] for item in selected)
        by_scenario[kind] = {
            "pairs": len(selected),
            "aggregate_ul_overload_area_relative_reduction": _reduction(static_ul, mpc_ul),
            "worst_pair_ul_overload_area_relative_reduction": min(
                item["relative_reduction"]["overload_area_seconds"]["ul"] for item in selected
            ),
        }
    base = load_scenario(manifest)
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evaluation_stage": evaluation_stage,
        "development_only": evaluation_stage == "development",
        "validation_only": evaluation_stage == "validation",
        "reserved_seeds_consumed": False,
        "manifest": {"path": str(manifest.resolve()), "sha256": _sha256(manifest)},
        "mpc_profile": {
            "path": str(profile_path.resolve()), "sha256": _sha256(profile_path),
            "profile_id": json.loads(profile_path.read_text(encoding="utf-8"))["profile_id"],
            "settings": asdict(settings),
        },
        "forecaster": {
            "type": "trained_bundle", "path": str(bundle_path.resolve()),
            "sha256": _artifact_sha256(bundle_path),
            "bundle_sha256": load_forecaster_bundle(bundle_path).payload["bundle_sha256"],
        },
        "evaluator": {
            "path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__)),
            "simulation_source_fingerprint": source_fingerprint(PROJECT_ROOT),
            "parallel_workers_are_evaluation_only": True,
        },
        "paired_runs": len(records),
        "simulated_days_per_pair": steps * base.step_seconds / 86_400,
        "totals": totals,
        "aggregate_relative_reduction": aggregate_reductions,
        "mean_pair_ul_overload_area_relative_reduction": mean_pair_ul,
        "mean_pair_ul_reduction_bootstrap_95_interval": confidence,
        "weighted_total_ul_overload_area_relative_reduction": aggregate_reductions["overload_area_seconds"]["ul"],
        "worst_pair_ul_overload_area_relative_reduction": min(pair_ul),
        "unknown_outage_mixed_ul_improvement": stressed_reduction,
        "normalized_mpc_churn_l1_per_group_decision": normalized_churn,
        "unexpected_fallback_decisions": unexpected_fallbacks,
        "skipped_decision_fraction": skipped_fraction,
        "development_gates": development_gates,
        "passes_day5_development_gate": all(development_gates.values()),
        "aggregate_guardrails": guardrails,
        "static_establishment_failures": static_failures,
        "mpc_establishment_failures": mpc_failures,
        "reaches_10_percent_gate": passes,
        "decision": (
            "eligible_for_next_stage" if passes else "stop_or_retune_without_release_seeds"
        ) if evaluation_stage in {"development", "validation"} else (
            "advance_to_full_campaign" if passes else "stop_before_full_campaign"
        ),
        "by_scenario": by_scenario,
        "pairs": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parallel resumable held-out MPC candidate evaluation")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--mpc-profile", required=True, type=Path)
    parser.add_argument("--forecast-bundle", required=True, type=Path)
    parser.add_argument("--survival-bundle", type=Path)
    parser.add_argument("--interface-freeze", type=Path)
    parser.add_argument("--seed-start", type=int, default=33001)
    parser.add_argument("--total-seeds", type=int, default=30)
    parser.add_argument("--steps", type=int, default=2880)
    parser.add_argument("--workers", required=True, type=int)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--evaluation-stage", choices=("promotion", "development", "validation"),
        default="promotion",
    )
    args = parser.parse_args()
    if args.evaluation_stage == "promotion" and args.total_seeds < 30:
        parser.error("production promotion evaluation requires at least 30 pairs")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite candidate evaluation: {args.output}")
    base = load_scenario(args.manifest)
    if args.steps * base.step_seconds < 86_400:
        parser.error("production promotion evaluation requires at least one simulated day per pair")
    seeds = list(range(args.seed_start, args.seed_start + args.total_seeds))
    if args.evaluation_stage in CONTROL_SCIENCE_SEED_SPLITS:
        expected = list(CONTROL_SCIENCE_SEED_SPLITS[args.evaluation_stage])
        if seeds != expected:
            parser.error(
                f"{args.evaluation_stage} evaluation must use the frozen seed split "
                f"{expected[0]}-{expected[-1]} exactly"
            )
    if UNTOUCHED_RELEASE_SEEDS.intersection(seeds):
        parser.error("untouched release seeds require the separately frozen release evaluator")
    if RESERVED_SEEDS.intersection(seeds):
        parser.error("requested seeds intersect the reserved validation set")
    tasks: list[tuple[ScenarioKind, int]] = []
    base_count, remainder = divmod(len(seeds), len(SCENARIO_KINDS))
    cursor = 0
    for index, kind in enumerate(SCENARIO_KINDS):
        count = base_count + (1 if index < remainder else 0)
        tasks.extend((kind, seed) for seed in seeds[cursor:cursor + count])
        cursor += count
    profile = json.loads(args.mpc_profile.read_text(encoding="utf-8"))
    settings = CohortMPCConfig(**profile.get("mpc", {}))
    interface_freeze_sha256 = (
        _verify_interface_freeze(args.interface_freeze)
        if args.interface_freeze else None
    )
    fingerprint_payload = {
        "manifest_sha256": _sha256(args.manifest),
        "profile_sha256": _sha256(args.mpc_profile),
        "bundle_sha256": _artifact_sha256(args.forecast_bundle),
        "survival_bundle_sha256": _sha256(args.survival_bundle) if args.survival_bundle else None,
        "source_fingerprint": source_fingerprint(PROJECT_ROOT),
        "evaluator_sha256": _sha256(Path(__file__)),
        "steps": args.steps,
        "tasks": tasks,
        "evaluation_stage": args.evaluation_stage,
        "interface_freeze_sha256": interface_freeze_sha256,
    }
    fingerprint = _canonical_sha256(fingerprint_payload)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    completed: dict[tuple[str, int], dict[str, Any]] = {}
    pending = []
    for kind, seed in tasks:
        path = args.work_dir / f"pair-{kind}-{seed:06d}.json"
        if path.exists():
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("work_fingerprint") != fingerprint:
                raise ValueError(f"stale pair checkpoint does not match frozen evaluation: {path}")
            completed[(kind, seed)] = row
        else:
            pending.append((kind, seed, path))
    print(json.dumps({"pairs": len(tasks), "resumed": len(completed), "pending": len(pending),
                      "workers": args.workers, "work_fingerprint": fingerprint}, sort_keys=True), flush=True)
    _single_thread()
    context = mp.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers, mp_context=context) as pool:
        futures = {
            pool.submit(
                _evaluate_pair, str(args.manifest), str(args.mpc_profile),
                str(args.forecast_bundle), kind, seed, args.steps, fingerprint,
                str(args.survival_bundle) if args.survival_bundle else None,
            ): (kind, seed, path)
            for kind, seed, path in pending
        }
        try:
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                kind, seed, path = futures[future]
                row = future.result()
                atomic_json(path, row)
                completed[(kind, seed)] = row
                reduction = row["relative_reduction"]["overload_area_seconds"]["ul"]
                print(f"pair={len(completed)}/{len(tasks)} kind={kind} seed={seed} "
                      f"ul_reduction={reduction:.4f} wall={row['wall_seconds']:.1f}s", flush=True)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    records = [completed[(kind, seed)] for kind, seed in tasks]
    result = aggregate(
        records, manifest=args.manifest, profile_path=args.mpc_profile,
        bundle_path=args.forecast_bundle, steps=args.steps, settings=settings,
        evaluation_stage=args.evaluation_stage,
    )
    result["interface_freeze"] = (
        {
            "path": str(args.interface_freeze.resolve()),
            "sha256": interface_freeze_sha256,
        }
        if args.interface_freeze else None
    )
    atomic_json(args.output, result)
    print(json.dumps({
        "output": str(args.output), "decision": result["decision"],
        "mean_pair_ul_reduction": result["mean_pair_ul_overload_area_relative_reduction"],
        "confidence": result["mean_pair_ul_reduction_bootstrap_95_interval"],
        "guardrails": result["aggregate_guardrails"],
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
