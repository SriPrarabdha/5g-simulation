"""Untouched MPC release gate for the control-science campaign."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import mean
from typing import Any


RELEASE_SEEDS = frozenset(range(46301, 46331))


def _reduction(baseline: float, candidate: float) -> float:
    return (baseline - candidate) / baseline if baseline > 0 else 0.0


def _bootstrap_lower(values: list[float], samples: int = 10_000) -> float:
    rng = random.Random(20260819)
    estimates = sorted(mean(rng.choice(values) for _ in values) for _ in range(samples))
    return estimates[int(0.025 * (samples - 1))]


def evaluate_release(payload: dict[str, Any]) -> dict[str, Any]:
    pairs = list(payload.get("pairs", []))
    seeds = {int(item["seed"]) for item in pairs}
    if seeds != RELEASE_SEEDS or len(pairs) != 30:
        raise ValueError("release evaluation requires untouched seeds 46301-46330 exactly once")
    improvements = [
        _reduction(
            float(item["static"]["overload_area_seconds"]["ul"]),
            float(item["mpc"]["overload_area_seconds"]["ul"]),
        )
        for item in pairs
    ]
    static_total = sum(float(item["static"]["overload_area_seconds"]["ul"]) for item in pairs)
    mpc_total = sum(float(item["mpc"]["overload_area_seconds"]["ul"]) for item in pairs)
    stressed = [
        item for item in pairs
        if item.get("scenario_kind") in {"unannounced_outage", "mixed_stress"}
    ]
    stressed_static = sum(float(item["static"]["overload_area_seconds"]["ul"]) for item in stressed)
    stressed_mpc = sum(float(item["mpc"]["overload_area_seconds"]["ul"]) for item in stressed)
    lower = _bootstrap_lower(improvements)
    severity = _reduction(static_total, mpc_total)
    stressed_reduction = _reduction(stressed_static, stressed_mpc)
    no_guardrail_regression = all(
        float(item["mpc"][metric][direction]) <= float(item["static"][metric][direction]) + 1e-7
        for item in pairs
        for metric in ("dropped_bytes",)
        for direction in ("ul", "dl")
    ) and all(
        int(item["mpc"]["establishment_failures"])
        <= int(item["static"]["establishment_failures"])
        for item in pairs
    )
    solver_ok = all(int(item.get("solver_timeout_count", 0)) == 0 for item in pairs)
    churn_ok = all(
        float(item.get("mpc_routing_churn_l1", 0.0))
        <= float(item.get("baseline_routing_churn_l1", float("inf")))
        for item in pairs
    )
    empirical_robust = all(bool(item.get("imperfect_survival_guardrail_passed", False)) for item in pairs)
    gates = {
        "mean_pair_ul_reduction_at_least_10_percent": mean(improvements) >= 0.10,
        "bootstrap_lower_bound_above_zero": lower > 0,
        "positive_severity_weighted_ul_improvement": severity > 0,
        "unknown_outage_and_mixed_regression_at_most_2_percent": stressed_reduction >= -0.02,
        "worst_pair_better_than_minus_10_percent": min(improvements) > -0.10,
        "no_drop_or_establishment_failure_regression": no_guardrail_regression,
        "no_solver_timeout_regression": solver_ok,
        "routing_churn_reduced_or_unchanged": churn_ok,
        "imperfect_empirical_survival_guardrails_pass": empirical_robust,
    }
    return {
        "schema_version": "control-science-mpc-release/1.0",
        "untouched_release_seeds": sorted(seeds),
        "paired_runs": len(pairs),
        "mean_pair_ul_improvement": mean(improvements),
        "bootstrap_lower_bound": lower,
        "severity_weighted_ul_improvement": severity,
        "unknown_outage_mixed_improvement": stressed_reduction,
        "worst_pair_improvement": min(improvements),
        "gates": gates,
        "promoted": all(gates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate_release(json.loads(args.pairs.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["promoted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
