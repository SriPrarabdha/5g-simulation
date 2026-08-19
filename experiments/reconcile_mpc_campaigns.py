"""Reconcile the 30-pair MPC promotion and 128-seed production contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ci(values: list[float], samples: int = 10_000) -> list[float]:
    stream = random.Random(20260819)
    estimates = sorted(mean(stream.choice(values) for _ in values) for _ in range(samples))
    return [estimates[int(0.025 * (samples - 1))], estimates[int(0.975 * (samples - 1))]]


def _production_pairs(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    by_seed: dict[int, dict[str, Any]] = defaultdict(dict)
    for shard in campaign["shards"]:
        by_seed[int(shard["seed"])][str(shard["controller"])] = shard
    rows: list[dict[str, Any]] = []
    for seed, controllers in sorted(by_seed.items()):
        if not {"static-capacity-v1", "cohort-mpc-v1"} <= set(controllers):
            raise ValueError(f"production seed {seed} is not paired")
        static = controllers["static-capacity-v1"]
        mpc = controllers["cohort-mpc-v1"]
        static_ul = float(static["overload_area_seconds"]["ul"])
        mpc_ul = float(mpc["overload_area_seconds"]["ul"])
        relative = (static_ul - mpc_ul) / static_ul if static_ul else 0.0
        rows.append({
            "seed": seed,
            "scenario_id": static["scenario_id"],
            "static_ul_overload_area_seconds": static_ul,
            "mpc_ul_overload_area_seconds": mpc_ul,
            "relative_improvement": relative,
            "absolute_difference_mpc_minus_static": mpc_ul - static_ul,
            "static_ul_dropped_bytes": float(static["dropped_bytes"]["ul"]),
            "mpc_ul_dropped_bytes": float(mpc["dropped_bytes"]["ul"]),
            "static_dl_dropped_bytes": float(static["dropped_bytes"]["dl"]),
            "mpc_dl_dropped_bytes": float(mpc["dropped_bytes"]["dl"]),
        })
    return rows


def reconcile(development_path: Path, production_path: Path) -> dict[str, Any]:
    development = json.loads(development_path.read_text(encoding="utf-8"))
    production = json.loads(production_path.read_text(encoding="utf-8"))
    prod_rows = _production_pairs(production)
    dev_rows = [{
        "seed": int(item["seed"]),
        "scenario_id": item["scenario_id"],
        "scenario_kind": item["scenario_kind"],
        "static_ul_overload_area_seconds": float(item["static"]["overload_area_seconds"]["ul"]),
        "mpc_ul_overload_area_seconds": float(item["mpc"]["overload_area_seconds"]["ul"]),
        "relative_improvement": float(item["relative_reduction"]["overload_area_seconds"]["ul"]),
    } for item in development["pairs"]]
    prod_improvement = [row["relative_improvement"] for row in prod_rows]
    prod_static_total = sum(row["static_ul_overload_area_seconds"] for row in prod_rows)
    prod_mpc_total = sum(row["mpc_ul_overload_area_seconds"] for row in prod_rows)
    development_profile = development["mpc_profile"]
    result = {
        "schema_version": "mpc-campaign-reconciliation/1.0",
        "authoritative_production_conclusion": (
            "Static remains the production winner. MPC is not promoted for the production contract."
        ),
        "directly_comparable": False,
        "reason": (
            "The controller settings, trained forecaster, one-day duration, and UL overload-area "
            "definition match, but the evaluation contracts do not: the 30-pair campaign was a "
            "development promotion set stratified across four injected stress types (seeds "
            "33001-33030), whereas production used 128 seeds of one fixed extreme scenario "
            "(49000-49127). The candidate was selected on the former, and its gain was driven by "
            "scheduled faults while unannounced/mixed cases already regressed. The larger production "
            "sample exposes a distribution shift and reverses the paired result."
        ),
        "common_contract": {
            "primary_metric": "overload_area_seconds.ul",
            "lower_is_better": True,
            "control_scope": "new_session_placement_only",
            "duration_steps": 2880,
            "forecaster_sha256": development["forecaster"]["sha256"],
            "forecaster_bundle_sha256": development["forecaster"]["bundle_sha256"],
            "mpc_candidate_sha256": development_profile["sha256"],
            "mpc_settings": development_profile["settings"],
        },
        "contract_differences": {
            "development": {
                "purpose": "candidate selection/promotion",
                "seeds": "33001-33030",
                "scenario_composition": development["by_scenario"],
                "aggregation": "unweighted mean-pair headline; severity-weighted total also reported",
            },
            "production": {
                "purpose": "production scale evidence",
                "seeds": "49000-49127",
                "scenario_composition": {production["scenarios"][0]: len(prod_rows)},
                "aggregation": "per-seed static=100 plots; raw paired values reconciled here",
            },
            "seed_overlap": [],
        },
        "development_30_pair": {
            "paired_seed_count": len(dev_rows),
            "mean_pair_relative_improvement": mean(row["relative_improvement"] for row in dev_rows),
            "bootstrap_ci95": development["mean_pair_ul_reduction_bootstrap_95_interval"],
            "severity_weighted_improvement": development["weighted_total_ul_overload_area_relative_reduction"],
            "worst_pair": min(row["relative_improvement"] for row in dev_rows),
            "pairs": dev_rows,
        },
        "production_128_seed": {
            "paired_seed_count": len(prod_rows),
            "mean_pair_relative_improvement": mean(prod_improvement),
            "bootstrap_ci95": _ci(prod_improvement),
            "severity_weighted_improvement": (
                (prod_static_total - prod_mpc_total) / prod_static_total if prod_static_total else 0.0
            ),
            "worst_pair": min(prod_improvement),
            "improved_pair_fraction": sum(value > 0 for value in prod_improvement) / len(prod_improvement),
            "pairs": prod_rows,
        },
        "source_artifacts": {
            "development": {"path": str(development_path), "sha256": _sha256(development_path)},
            "production": {"path": str(production_path), "sha256": _sha256(production_path)},
        },
        "presentation_rule": (
            "Present the 30-pair result only as development evidence under its four-stressor "
            "contract. Use the 128-seed result for production claims and do not call MPC promoted."
        ),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development", required=True, type=Path)
    parser.add_argument("--production", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = reconcile(args.development, args.production)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({key: value for key, value in result.items() if key not in {"development_30_pair", "production_128_seed"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
