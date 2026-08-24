"""Pre-registered paired gates for guarded mixed-stress results."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

from experiments.mixed_stress_campaign import FAMILIES, atomic_checkpoint
from experiments.mixed_stress_campaign import churn_l1_per_group_hour


def _improvement(pair):
    baseline = float(pair["static"]["overload_area_seconds"]["ul"])
    candidate = float(pair["hybrid"]["overload_area_seconds"]["ul"])
    return 0.0 if baseline == 0 and candidate == 0 else (baseline - candidate) / max(baseline, 1e-12)


def _bootstrap_lower(values, *, seed=20260824, replicates=10_000):
    if not values:
        raise ValueError("cannot bootstrap an empty campaign")
    rng = random.Random(seed)
    estimates = sorted(mean(rng.choice(values) for _ in values) for _ in range(replicates))
    return estimates[int(0.025 * replicates)]


def aggregate(root: Path, *, expected_pairs: int) -> dict:
    paths = sorted(root.rglob("pairs/*.json"))
    if len(paths) != expected_pairs:
        raise ValueError(f"expected {expected_pairs} complete pair shards, found {len(paths)}")
    pairs = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    identities = {(item["cell"]["family"], int(item["cell"]["seed"])) for item in pairs}
    if len(identities) != len(pairs):
        raise ValueError("duplicate family/seed pair shards")
    by_family = defaultdict(list)
    for pair in pairs:
        by_family[pair["cell"]["family"]].append(pair)
    missing = set(FAMILIES) - set(by_family)
    if missing:
        raise ValueError(f"missing stress families: {sorted(missing)}")
    improvements = [_improvement(item) for item in pairs]
    severity_numerator = sum(
        float(item["static"]["overload_area_seconds"]["ul"]) - float(item["hybrid"]["overload_area_seconds"]["ul"])
        for item in pairs
    )
    severity_denominator = sum(float(item["static"]["overload_area_seconds"]["ul"]) for item in pairs)
    family_gain = {family: mean(_improvement(item) for item in items) for family, items in by_family.items()}
    regressions = {}
    for metric, section, direction in (
        ("dl_overload", "overload_area_seconds", "dl"),
        ("ul_drop", "dropped_bytes", "ul"), ("dl_drop", "dropped_bytes", "dl"),
        ("ul_rejection", "rejected_bytes", "ul"), ("dl_rejection", "rejected_bytes", "dl"),
    ):
        regressions[metric] = sum(float(item["hybrid"][section][direction]) - float(item["static"][section][direction]) for item in pairs)
    regressions["establishment_failures"] = sum(int(item["hybrid"]["establishment_failures"]) - int(item["static"]["establishment_failures"]) for item in pairs)
    latencies = [float(row.get("decision_runtime_ms", 0)) for item in pairs for row in item.get("decision_diagnostics", [])]
    churn = [
        churn_l1_per_group_hour(
            float(item.get("control_metrics", {}).get("routing_churn_l1", 0.0)),
            groups=int(item["group_count"]),
            steps=int(item["hybrid"]["steps"]),
            step_seconds=int(item["step_seconds"]),
        ) for item in pairs
    ]
    solver_failures = sum(
        int(item.get("control_metrics", {}).get("solver_timeout_count", 0))
        + sum(count for status, count in item.get("control_metrics", {}).get("solver_statuses", {}).items() if status in {"error", "infeasible"})
        for item in pairs
    )
    pure_surprise = [item for item in pairs if item["cell"]["family"] in {"surprise_demand", "surprise_outage"}]
    exact_surprise = all(item["static"] == {**item["hybrid"], "controller": item["static"]["controller"]} for item in pure_surprise)
    lower = _bootstrap_lower(improvements)
    weighted = severity_numerator / max(severity_denominator, 1e-12)
    gates = {
        "mean_ul_improvement_at_least_10pct": mean(improvements) >= 0.10,
        "bootstrap_95pct_lower_above_zero": lower > 0,
        "positive_severity_weighted_gain": weighted > 0,
        "no_family_ul_regression": all(value >= 0 for value in family_gain.values()),
        "no_aggregate_secondary_regression": all(value <= 0 for value in regressions.values()),
        "worst_pair_above_minus_10pct": min(improvements) > -0.10,
        "two_minute_latency_within_120s": all(value <= 120_000 for value in latencies),
        "pure_surprise_exact_static": exact_surprise,
        "churn_within_0_30_l1_per_group_hour": max(churn, default=0) <= 0.30,
        "no_solver_timeout_or_error": solver_failures == 0,
    }
    return {
        "schema_version": "mixed-stress-evaluation/1.0", "pair_count": len(pairs),
        "mean_ul_improvement": mean(improvements), "bootstrap_95pct_lower": lower,
        "severity_weighted_ul_improvement": weighted, "family_mean_ul_improvement": family_gain,
        "worst_pair_improvement": min(improvements), "secondary_regressions": regressions,
        "latency": {"maximum_ms": max(latencies, default=0), "fraction_within_500ms": sum(value <= 500 for value in latencies) / max(1, len(latencies))},
        "maximum_churn_l1_per_group_hour": max(churn, default=0),
        "solver_failure_count": solver_failures,
        "gates": gates, "passed": all(gates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-pairs", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    atomic_checkpoint(args.output, aggregate(args.root, expected_pairs=args.expected_pairs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
