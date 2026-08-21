"""Untouched MPC release gate for the control-science campaign."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import mean
from typing import Any


RELEASE_SEEDS = frozenset(range(46301, 46331))
MANDATORY_PAIR_FIELDS = (
    "seed", "scenario_kind", "static", "mpc", "solver_timeout_count",
    "mpc_routing_churn_l1", "baseline_routing_churn_l1", "decision_reasons",
    "solver_statuses", "survival", "imperfect_survival_guardrail_passed",
    "survival_guardrail_evidence", "controller_decisions", "controller_groups",
    "certified_decisions", "unexpected_fallback_decision_count",
)
MAX_UNEXPECTED_FALLBACK_FRACTION = 0.01
MAX_SKIPPED_DECISION_FRACTION = 0.95
MAX_NORMALIZED_CHURN_L1 = 0.05


def _validate_pair(item: dict[str, Any], index: int) -> None:
    missing = [field for field in MANDATORY_PAIR_FIELDS if field not in item]
    if missing:
        raise ValueError(f"release pair {index} is missing mandatory evidence: {missing}")
    for controller in ("static", "mpc"):
        payload = item[controller]
        for field in ("overload_area_seconds", "dropped_bytes", "establishment_failures"):
            if field not in payload:
                raise ValueError(f"release pair {index} {controller} is missing {field}")
        for metric in ("overload_area_seconds", "dropped_bytes"):
            if set(payload[metric]) < {"ul", "dl"}:
                raise ValueError(f"release pair {index} {controller}.{metric} lacks UL/DL")
    if not isinstance(item["decision_reasons"], dict) or not item["decision_reasons"]:
        raise ValueError(f"release pair {index} lacks decision-reason counts")
    if not isinstance(item["solver_statuses"], dict) or not item["solver_statuses"]:
        raise ValueError(f"release pair {index} lacks solver-status counts")
    if not isinstance(item["survival"], dict) or not item["survival"]:
        raise ValueError(f"release pair {index} lacks survival provenance")
    for group_id, audit in item["survival"].items():
        required = {"source", "age_seconds", "sample_count", "confidence", "stale"}
        if not isinstance(audit, dict) or not required.issubset(audit):
            raise ValueError(f"release pair {index} survival audit for {group_id} is incomplete")
    evidence = item["survival_guardrail_evidence"]
    if not isinstance(evidence, dict) or not {
        "measured", "passed", "comparison_sha256", "criteria",
    }.issubset(evidence):
        raise ValueError(f"release pair {index} lacks measured survival evidence")
    if int(item["controller_decisions"]) < 1 or int(item["controller_groups"]) < 1:
        raise ValueError(f"release pair {index} has invalid controller dimensions")


def _reduction(baseline: float, candidate: float) -> float:
    return (baseline - candidate) / baseline if baseline > 0 else 0.0


def _bootstrap_lower(values: list[float], samples: int = 10_000) -> float:
    rng = random.Random(20260819)
    estimates = sorted(mean(rng.choice(values) for _ in values) for _ in range(samples))
    return estimates[int(0.025 * (samples - 1))]


def evaluate_release(payload: dict[str, Any]) -> dict[str, Any]:
    pairs = list(payload.get("pairs", []))
    for index, item in enumerate(pairs):
        _validate_pair(item, index)
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
    dl_static = sum(float(item["static"]["overload_area_seconds"]["dl"]) for item in pairs)
    dl_mpc = sum(float(item["mpc"]["overload_area_seconds"]["dl"]) for item in pairs)
    no_dl_overload_regression = dl_mpc <= dl_static + 1e-7 * max(1.0, dl_static)
    solver_ok = all(
        int(item["solver_timeout_count"]) == 0
        and sum(int(item["solver_statuses"].get(status, 0)) for status in ("timeout", "error", "infeasible")) == 0
        for item in pairs
    )
    total_decisions = sum(int(item["controller_decisions"]) for item in pairs)
    total_group_decisions = sum(
        int(item["controller_decisions"]) * int(item["controller_groups"])
        for item in pairs
    )
    unexpected_fallback_fraction = (
        sum(int(item["unexpected_fallback_decision_count"]) for item in pairs)
        / total_decisions
    )
    skipped_fraction = (
        sum(int(item["solver_statuses"].get("skipped", 0)) for item in pairs)
        / total_decisions
    )
    normalized_churn = (
        sum(float(item["mpc_routing_churn_l1"]) for item in pairs)
        / total_group_decisions
    )
    fallback_ok = (
        unexpected_fallback_fraction <= MAX_UNEXPECTED_FALLBACK_FRACTION
        and skipped_fraction <= MAX_SKIPPED_DECISION_FRACTION
    )
    churn_ok = normalized_churn <= MAX_NORMALIZED_CHURN_L1
    empirical_robust = all(
        bool(item["imperfect_survival_guardrail_passed"])
        and bool(item["survival_guardrail_evidence"]["measured"])
        and bool(item["survival_guardrail_evidence"]["passed"])
        and bool(item["survival_guardrail_evidence"]["comparison_sha256"])
        for item in pairs
    )
    gates = {
        "mean_pair_ul_reduction_at_least_10_percent": mean(improvements) >= 0.10,
        "bootstrap_lower_bound_above_zero": lower > 0,
        "positive_severity_weighted_ul_improvement": severity > 0,
        "unknown_outage_and_mixed_regression_at_most_2_percent": stressed_reduction >= -0.02,
        "worst_pair_better_than_minus_10_percent": min(improvements) > -0.10,
        "no_dl_overload_regression": no_dl_overload_regression,
        "no_drop_or_establishment_failure_regression": no_guardrail_regression,
        "no_solver_timeout_error_or_infeasibility": solver_ok,
        "fallback_and_skipped_decisions_within_budget": fallback_ok,
        "explicit_normalized_routing_churn_budget": churn_ok,
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
        "dl_overload_improvement": _reduction(dl_static, dl_mpc),
        "unexpected_fallback_fraction": unexpected_fallback_fraction,
        "skipped_decision_fraction": skipped_fraction,
        "normalized_churn_l1_per_group_decision": normalized_churn,
        "budgets": {
            "max_unexpected_fallback_fraction": MAX_UNEXPECTED_FALLBACK_FRACTION,
            "max_skipped_decision_fraction": MAX_SKIPPED_DECISION_FRACTION,
            "max_normalized_churn_l1_per_group_decision": MAX_NORMALIZED_CHURN_L1,
        },
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
