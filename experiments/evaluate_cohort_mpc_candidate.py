from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecasting import MovingAverageForecaster, load_forecaster_bundle
from optimization import (
    CohortMPCConfig, load_survival_guardrail_evidence, load_survival_tables,
)
from simulator.macro import Simulator, load_scenario
from simulator.macro.controllers import CohortMPCController, StaticCapacityController

from .evaluate_cohort_mpc_pilot import (
    RESERVED_SEEDS,
    SCENARIO_KINDS,
    ScenarioKind,
    build_pilot_scenario,
)
from .seed_policy import reject_protected_mpc_seeds


SCHEMA_VERSION = "cohort-mpc-10pct-candidate-evaluation/1.0"


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


def _reduction(static: float, candidate: float) -> float:
    return (static - candidate) / static if static > 0 else 0.0


def _bootstrap_mean_interval(values: list[float], samples: int = 10_000) -> list[float]:
    rng = random.Random(20260807)
    estimates = sorted(
        sum(values[rng.randrange(len(values))] for _ in values) / len(values)
        for _ in range(samples)
    )
    return [
        estimates[round(0.025 * (samples - 1))],
        estimates[round(0.975 * (samples - 1))],
    ]


def evaluate_candidate(
    manifest: Path,
    profile_path: Path,
    seeds_by_scenario: dict[ScenarioKind, list[int]],
    *,
    steps: int,
    forecast_bundle: Path | None = None,
    survival_bundle: Path | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    seeds = [seed for values in seeds_by_scenario.values() for seed in values]
    if set(seeds_by_scenario) != set(SCENARIO_KINDS):
        raise ValueError("candidate evaluation requires all four scenario kinds")
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("candidate seeds must be non-empty and unique")
    if RESERVED_SEEDS.intersection(seeds):
        raise ValueError("reserved validation seeds cannot be consumed")
    reject_protected_mpc_seeds(seeds)
    base = load_scenario(manifest)
    if steps < 24 * 3600 // base.step_seconds:
        raise ValueError("candidate scenarios must cover a full simulated day")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile.get("schema_version") != "cohort-mpc-profile/1.0":
        raise ValueError("unsupported cohort MPC profile schema")
    settings = CohortMPCConfig(**profile.get("mpc", {}))
    forecaster_spec = profile.get("forecaster", {})
    if forecast_bundle is not None:
        forecaster = load_forecaster_bundle(forecast_bundle)
        forecaster.validate_groups(group.key for group in base.groups)
        forecaster_identity: dict[str, Any] = {
            "type": "trained_bundle",
            "path": str(forecast_bundle.resolve()),
            "sha256": _artifact_sha256(forecast_bundle),
            "bundle_sha256": forecaster.payload["bundle_sha256"],
        }
    else:
        if forecaster_spec.get("type") != "moving_average":
            raise ValueError("profile must declare a moving-average forecaster")
        history_windows = int(forecaster_spec.get("history_windows", 6))
        forecaster = MovingAverageForecaster(history_windows)
        forecaster_identity = {
            "type": "moving_average",
            "history_windows": history_windows,
            "model_version": forecaster.model_version,
        }

    survival_tables = load_survival_tables(str(survival_bundle)) if survival_bundle else None
    survival_evidence = (
        load_survival_guardrail_evidence(str(survival_bundle))
        if survival_bundle else None
    )
    if survival_tables is not None and set(survival_tables) != {
        group.key.selection_id for group in base.groups
    }:
        raise ValueError("survival bundle groups must exactly match the scenario")
    records = []
    total_pairs = len(seeds)
    completed = 0
    for kind in SCENARIO_KINDS:
        for seed in seeds_by_scenario[kind]:
            scenario, events = build_pilot_scenario(
                base,
                kind=kind,
                seed=seed,
                steps=steps,
                horizon_windows=settings.horizon_windows,
            )
            if progress:
                print(f"candidate {completed + 1}/{total_pairs} {kind} seed={seed} static", flush=True)
            static_simulator = Simulator(scenario, StaticCapacityController())
            static = static_simulator.run(static_simulator.make_summary_sink()).summary
            static_control = static_simulator.control_metrics()
            controller = CohortMPCController(
                forecaster=forecaster,
                mpc_config=settings,
                survival_by_group=survival_tables,
                survival_guardrail_evidence=survival_evidence,
            )
            if progress:
                print(f"candidate {completed + 1}/{total_pairs} {kind} seed={seed} mpc", flush=True)
            mpc_simulator = Simulator(scenario, controller)
            mpc = mpc_simulator.run(mpc_simulator.make_summary_sink()).summary
            mpc_control = mpc_simulator.control_metrics()
            reductions = {
                metric: {
                    direction: _reduction(static[metric][direction], mpc[metric][direction])
                    for direction in ("ul", "dl")
                }
                for metric in ("overload_area_seconds", "dropped_bytes")
            }
            records.append({
                "scenario_kind": kind,
                "scenario_id": scenario.scenario_id,
                "seed": seed,
                "events": events,
                "static": static,
                "mpc": mpc,
                "relative_reduction": reductions,
                "controller_decisions": controller.decision_count,
                "certified_decisions": controller.certified_decision_count,
                "baseline_routing_churn_l1": static_control["routing_churn_l1"],
                "mpc_routing_churn_l1": mpc_control["routing_churn_l1"],
                "decision_reasons": mpc_control["decision_reasons"],
                "solver_statuses": mpc_control["solver_statuses"],
                "solver_timeout_count": mpc_control["solver_timeout_count"],
                "survival": mpc_control["survival"],
                "survival_guardrail_evidence": mpc_control["survival_guardrail_evidence"],
                "imperfect_survival_guardrail_passed": mpc_control[
                    "imperfect_survival_guardrail_passed"
                ],
            })
            completed += 1
            if progress:
                print(
                    f"candidate result {kind} seed={seed} "
                    f"ul={reductions['overload_area_seconds']['ul']:.6f}",
                    flush=True,
                )

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
            direction: _reduction(
                totals["static"][metric][direction],
                totals["mpc"][metric][direction],
            )
            for direction in ("ul", "dl")
        }
        for metric in ("overload_area_seconds", "dropped_bytes")
    }
    static_failures = sum(item["static"]["establishment_failures"] for item in records)
    mpc_failures = sum(item["mpc"]["establishment_failures"] for item in records)
    aggregate_guardrails = {
        "no_dl_overload_regression": aggregate_reductions["overload_area_seconds"]["dl"] >= 0,
        "no_ul_drop_regression": aggregate_reductions["dropped_bytes"]["ul"] >= 0,
        "no_dl_drop_regression": aggregate_reductions["dropped_bytes"]["dl"] >= 0,
        "no_session_failure_regression": mpc_failures <= static_failures,
    }
    weighted_ul_reduction = aggregate_reductions["overload_area_seconds"]["ul"]
    mean_pair_ul_reduction = sum(
        item["relative_reduction"]["overload_area_seconds"]["ul"]
        for item in records
    ) / len(records)
    pair_ul_reductions = [
        item["relative_reduction"]["overload_area_seconds"]["ul"]
        for item in records
    ]
    confidence_interval = _bootstrap_mean_interval(pair_ul_reductions)
    passes = (
        mean_pair_ul_reduction >= 0.10
        and confidence_interval[0] > 0
        and all(aggregate_guardrails.values())
    )
    by_scenario = {}
    for kind in SCENARIO_KINDS:
        selected = [item for item in records if item["scenario_kind"] == kind]
        static_ul = sum(item["static"]["overload_area_seconds"]["ul"] for item in selected)
        mpc_ul = sum(item["mpc"]["overload_area_seconds"]["ul"] for item in selected)
        by_scenario[kind] = {
            "pairs": len(selected),
            "aggregate_ul_overload_area_relative_reduction": _reduction(static_ul, mpc_ul),
            "worst_pair_ul_overload_area_relative_reduction": min(
                item["relative_reduction"]["overload_area_seconds"]["ul"]
                for item in selected
            ),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "development_only": True,
        "reserved_seeds_consumed": False,
        "manifest": {"path": str(manifest.resolve()), "sha256": _sha256(manifest)},
        "mpc_profile": {
            "path": str(profile_path.resolve()),
            "sha256": _sha256(profile_path),
            "profile_id": profile["profile_id"],
            "settings": asdict(settings),
        },
        "forecaster": forecaster_identity,
        "survival_bundle": (
            {"path": str(survival_bundle.resolve()), "sha256": _sha256(survival_bundle)}
            if survival_bundle else None
        ),
        "paired_runs": len(records),
        "simulated_days_per_pair": steps * base.step_seconds / 86_400,
        "totals": totals,
        "aggregate_relative_reduction": aggregate_reductions,
        "mean_pair_ul_overload_area_relative_reduction": mean_pair_ul_reduction,
        "mean_pair_ul_reduction_bootstrap_95_interval": confidence_interval,
        "weighted_total_ul_overload_area_relative_reduction": weighted_ul_reduction,
        "worst_pair_ul_overload_area_relative_reduction": min(
            item["relative_reduction"]["overload_area_seconds"]["ul"]
            for item in records
        ),
        "aggregate_guardrails": aggregate_guardrails,
        "static_establishment_failures": static_failures,
        "mpc_establishment_failures": mpc_failures,
        "reaches_10_percent_gate": passes,
        "decision": "advance_to_full_campaign" if passes else "stop_before_full_campaign",
        "by_scenario": by_scenario,
        "pairs": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a 10% cohort MPC candidate on fresh seeds")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mpc-profile", type=Path, required=True)
    parser.add_argument("--forecast-bundle", type=Path)
    parser.add_argument("--survival-bundle", type=Path)
    parser.add_argument("--seed-start", type=int, default=33001)
    parser.add_argument("--total-seeds", type=int, default=30)
    parser.add_argument("--steps", type=int, default=2880)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.total_seeds < len(SCENARIO_KINDS):
        parser.error("--total-seeds must cover all four scenarios")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite candidate evaluation: {args.output}")
    seeds_by_scenario: dict[ScenarioKind, list[int]] = {}
    cursor = args.seed_start
    base_count, remainder = divmod(args.total_seeds, len(SCENARIO_KINDS))
    for index, kind in enumerate(SCENARIO_KINDS):
        count = base_count + (1 if index < remainder else 0)
        seeds_by_scenario[kind] = list(range(cursor, cursor + count))
        cursor += count
    payload = evaluate_candidate(
        args.manifest,
        args.mpc_profile,
        seeds_by_scenario,
        steps=args.steps,
        forecast_bundle=args.forecast_bundle,
        survival_bundle=args.survival_bundle,
        progress=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "decision": payload["decision"],
        "ul_reduction": payload["aggregate_relative_reduction"]["overload_area_seconds"]["ul"],
        "mean_pair_ul_reduction": payload["mean_pair_ul_overload_area_relative_reduction"],
        "guardrails": payload["aggregate_guardrails"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
