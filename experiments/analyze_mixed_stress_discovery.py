"""Compact, deterministic analysis of guarded mixed-stress discovery pairs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from experiments.mixed_stress_campaign import FAMILIES, atomic_checkpoint


SECONDARY = (
    ("dl_overload", "overload_area_seconds", "dl"),
    ("ul_drop", "dropped_bytes", "ul"),
    ("dl_drop", "dropped_bytes", "dl"),
    ("ul_rejection", "rejected_bytes", "ul"),
    ("dl_rejection", "rejected_bytes", "dl"),
)


def _behavior(summary: dict) -> dict:
    return {
        key: summary[key] for key in (
            "offered_bytes", "carried_bytes", "dropped_bytes", "rejected_bytes",
            "overload_area_seconds", "overload_duration_seconds",
            "establishment_failures",
        )
    }


def _pair_gain(baseline: float, candidate: float) -> float:
    if math.isinf(baseline):
        return 0.0 if math.isinf(candidate) else 1.0
    if math.isinf(candidate):
        return -1.0
    if baseline > 0:
        return (baseline - candidate) / baseline
    return 0.0 if candidate == 0 else -1.0


def _paired_delta(candidate: float, baseline: float) -> float:
    if math.isinf(candidate) and math.isinf(baseline):
        return 0.0
    return candidate - baseline


def _bootstrap_lower(values: list[float], seed: int, replicates: int = 20_000) -> float:
    array = np.asarray(values, dtype=np.float64)
    rng = np.random.default_rng(seed)
    estimates = np.empty(replicates, dtype=np.float64)
    for start in range(0, replicates, 1000):
        size = min(1000, replicates - start)
        indices = rng.integers(0, len(array), size=(size, len(array)))
        estimates[start:start + size] = array[indices].mean(axis=1)
    return float(np.quantile(estimates, 0.025, method="lower"))


def analyze_arm(node: Path) -> dict:
    paths = sorted((node / "pairs").glob("*.json"))
    if len(paths) != 125:
        raise ValueError(f"{node.name}: expected 125 pairs, found {len(paths)}")
    arm = None
    pairs = []
    family_counts = Counter()
    decision_funnel = Counter()
    fallback_reasons = Counter()
    solver_failures = 0
    unexpected_fallbacks = 0
    invalid_slack = 0
    latencies = []
    total_churn = 0.0
    total_group_hours = 0.0
    exact_surprise = True
    nonfinite_pair_count = 0
    for path in paths:
        item = json.loads(path.read_text(encoding="utf-8"))
        arm = item["cell"]["arm"] if arm is None else arm
        if item["cell"]["arm"] != arm:
            raise ValueError(f"{node.name}: mixed candidate identities")
        family = item["cell"]["family"]
        family_counts[family] += 1
        baseline = item["static"]
        candidate = item["hybrid"]
        b_ul = float(baseline["overload_area_seconds"]["ul"])
        c_ul = float(candidate["overload_area_seconds"]["ul"])
        nonfinite_pair_count += int(not math.isfinite(b_ul) or not math.isfinite(c_ul))
        secondary = {
            name: _paired_delta(float(candidate[section][direction]), float(baseline[section][direction]))
            for name, section, direction in SECONDARY
        }
        secondary["establishment_failures"] = int(candidate["establishment_failures"]) - int(baseline["establishment_failures"])
        pairs.append({
            "family": family, "seed": int(item["cell"]["seed"]),
            "baseline_ul": b_ul, "candidate_ul": c_ul,
            "gain": _pair_gain(b_ul, c_ul), "secondary": secondary,
        })
        if family in {"surprise_demand", "surprise_outage"}:
            exact_surprise &= _behavior(baseline) == _behavior(candidate)
        metrics = item.get("control_metrics", {})
        decision_funnel.update(metrics.get("decision_funnel", {}))
        fallback_reasons.update(metrics.get("decision_reasons", {}))
        solver_failures += int(metrics.get("solver_timeout_count", 0))
        solver_failures += sum(
            int(count) for status, count in metrics.get("solver_statuses", {}).items()
            if status in {"timeout", "error", "infeasible"}
        )
        total_churn += float(metrics.get("routing_churn_l1", 0.0))
        total_group_hours += int(item["group_count"]) * int(candidate["steps"]) * int(item["step_seconds"]) / 3600
        for row in item.get("decision_diagnostics", []):
            latencies.append(float(row.get("decision_runtime_ms", 0)))
            disposition = str(row.get("disposition", ""))
            if disposition.startswith("static_fallback:") and not any(
                token in disposition for token in (
                    "no_known_reduction_in_lead_horizon", "no_known_future_capacity_event",
                    "exposure_guard:", "insufficient", "minimum_hold_period",
                )
            ):
                unexpected_fallbacks += 1
            slack = row.get("constraint_slack", {})
            if disposition == "accepted" and any(
                float(value) > 1e-7
                for category in slack.values() for value in category.values()
            ):
                invalid_slack += 1
    expected = {family: 25 for family in FAMILIES}
    if dict(family_counts) != expected:
        raise ValueError(f"{node.name}: unbalanced families {dict(family_counts)}")
    gains = [item["gain"] for item in pairs]
    arm_index = int(arm["index"])
    family = {}
    for name in FAMILIES:
        selected = [item for item in pairs if item["family"] == name]
        baseline_total = sum(item["baseline_ul"] for item in selected)
        candidate_total = sum(item["candidate_ul"] for item in selected)
        family[name] = {
            "mean_pair_gain": float(np.mean([item["gain"] for item in selected])),
            "bootstrap_95pct_lower": _bootstrap_lower(
                [item["gain"] for item in selected], 20260824 + arm_index * 10 + FAMILIES.index(name)
            ),
            "aggregate_gain": _pair_gain(baseline_total, candidate_total),
            "informative_static_pairs": sum(item["baseline_ul"] > 0 for item in selected),
        }
    baseline_total = sum(item["baseline_ul"] for item in pairs)
    candidate_total = sum(item["candidate_ul"] for item in pairs)
    finite_pairs = [
        item for item in pairs
        if math.isfinite(item["baseline_ul"]) and math.isfinite(item["candidate_ul"])
    ]
    finite_baseline_total = sum(item["baseline_ul"] for item in finite_pairs)
    finite_candidate_total = sum(item["candidate_ul"] for item in finite_pairs)
    secondary_totals = {
        name: sum(item["secondary"][name] for item in pairs)
        for name in (*[x[0] for x in SECONDARY], "establishment_failures")
    }
    worst = sorted(pairs, key=lambda item: item["gain"])[:5]
    mean_gain = float(np.mean(gains))
    lower = _bootstrap_lower(gains, 20260824 + arm_index)
    severity_gain = _pair_gain(finite_baseline_total, finite_candidate_total)
    decisions = max(1, sum(decision_funnel.values()))
    gates = {
        "mean_pair_ul_gain_at_least_10pct": mean_gain >= 0.10,
        "bootstrap_95pct_lower_above_zero": lower > 0,
        "severity_weighted_ul_gain_positive": severity_gain > 0,
        "no_family_aggregate_ul_regression": all(value["aggregate_gain"] >= 0 for value in family.values()),
        "no_secondary_aggregate_regression": all(value <= 0 for value in secondary_totals.values()),
        "worst_pair_above_minus_10pct": min(gains) > -0.10,
        "no_solver_timeout_or_error": solver_failures == 0,
        "no_invalid_capacity_slack": invalid_slack == 0,
        "unexpected_fallback_below_1pct": unexpected_fallbacks / decisions < 0.01,
        "churn_within_0_30_l1_per_group_hour": total_churn / total_group_hours <= 0.30,
        "decision_latency_within_120s": max(latencies, default=0) <= 120_000,
        "pure_surprise_exact_static": exact_surprise,
        "all_overload_metrics_finite": nonfinite_pair_count == 0,
    }
    return {
        "arm": arm, "pair_count": len(pairs), "mean_pair_ul_gain": mean_gain,
        "bootstrap_95pct_lower": lower, "severity_weighted_ul_gain": severity_gain,
        "family": family, "secondary_regressions": secondary_totals,
        "worst_pair_gain": min(gains), "worst_pairs": worst,
        "decision_funnel": dict(decision_funnel), "fallback_reasons": dict(fallback_reasons),
        "solver_failure_count": solver_failures, "invalid_slack_count": invalid_slack,
        "nonfinite_pair_count": nonfinite_pair_count,
        "unexpected_fallback_fraction": unexpected_fallbacks / decisions,
        "churn_l1_per_group_hour": total_churn / total_group_hours,
        "latency_max_ms": max(latencies, default=0),
        "latency_fraction_within_500ms": sum(value <= 500 for value in latencies) / max(1, len(latencies)),
        "gates": gates, "passed": all(gates.values()),
    }


def analyze(root: Path, workers: int) -> dict:
    nodes = sorted((root / "discovery").glob("node-*"), key=lambda p: int(p.name.split("-")[-1]))
    if len(nodes) != 160:
        raise ValueError(f"expected 160 arm directories, found {len(nodes)}")
    with ProcessPoolExecutor(max_workers=workers) as pool:
        arms = list(pool.map(analyze_arm, nodes))
    arms.sort(key=lambda item: int(item["arm"]["index"]))
    ranking = sorted(
        arms,
        key=lambda item: (
            item["passed"], item["bootstrap_95pct_lower"],
            item["mean_pair_ul_gain"], item["severity_weighted_ul_gain"],
        ), reverse=True,
    )
    digest = hashlib.sha256()
    for path in sorted(root.glob("discovery/node-*/pairs/*.json")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(str(path.stat().st_size).encode())
    return {
        "schema_version": "mixed-stress-discovery-analysis/1.0",
        "input_root": str(root.resolve()), "input_inventory_sha256": digest.hexdigest(),
        "arm_count": len(arms), "pair_count": sum(item["pair_count"] for item in arms),
        "passing_arm_count": sum(item["passed"] for item in arms),
        "passing_arm_indices": [int(item["arm"]["index"]) for item in arms if item["passed"]],
        "ranking": [int(item["arm"]["index"]) for item in ranking], "arms": arms,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    result = analyze(args.root, args.workers)
    atomic_checkpoint(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key != "arms"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
