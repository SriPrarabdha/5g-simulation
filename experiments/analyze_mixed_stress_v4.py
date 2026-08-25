"""Deterministic analysis of guarded mixed-stress discovery pairs, v4.

The v3 analysis conflated three separable things, and the combination made its
pre-registered contract unpassable regardless of controller quality.

* ``all_overload_metrics_finite`` was scored as a performance gate.  It failed
  on 160 of 160 arms because one stress family drove safe capacity to zero and
  produced ``inf`` for *both* controllers -- an exact tie.  Here validity
  preconditions are reported separately from performance gates, so a data
  defect can never masquerade as a controller verdict.

* Cross-family severity weighting summed raw relative overload areas.  Relative
  overload scales with the reciprocal of remaining capacity, so the family
  containing a 1% brownout carried roughly 275x the weight of every other
  family and decided the aggregate on its own.  Here each family is normalised
  within itself and the families are then weighted equally.

* Tail risk was gated on the worst single pair-ratio out of 125.  Ratios on
  near-zero baselines are unbounded below while gains cap at +1.0, so one
  immaterial pair could cancel one and a half perfect ones.  Here the tail is
  bounded in the scored unit: overload-seconds added must stay small relative
  to overload-seconds removed.

Per-pair means over sets that are majority zero-baseline are reported, for
continuity with v3, alongside the informative-pair means that actually carry
signal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from experiments.mixed_stress_campaign import atomic_checkpoint
from experiments.mixed_stress_campaign_v4 import FAMILIES

SECONDARY = (
    ("dl_overload", "overload_area_seconds", "dl"),
    ("ul_drop", "dropped_bytes", "ul"),
    ("dl_drop", "dropped_bytes", "dl"),
    ("ul_rejection", "rejected_bytes", "ul"),
    ("dl_rejection", "rejected_bytes", "dl"),
)
PURE_SURPRISE = ("surprise_demand", "surprise_brownout")
# Families where a declared event exists and the controller is permitted to
# act.  The pure-surprise families are designed ties, so a mean across all
# five understates the effect where action is possible and a mean across
# only these overstates campaign-wide impact.  Report both.
ACTIONABLE = tuple(name for name in FAMILIES if name not in PURE_SURPRISE)
EXPECTED_FALLBACKS = (
    "no_known_reduction_in_lead_horizon", "no_known_future_capacity_event",
    "exposure_guard:", "insufficient", "minimum_hold_period", "solve_trigger_safe",
)
# Overload-seconds a candidate may add, as a fraction of what it removes.
HARM_RATIO_LIMIT = 0.25
MACRO_GAIN_TARGET = 0.10


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
    if not values:
        return 0.0
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
    if not paths:
        raise ValueError(f"{node.name}: no pairs found")
    arm = None
    pairs: list[dict] = []
    family_counts: Counter[str] = Counter()
    decision_funnel: Counter[str] = Counter()
    fallback_reasons: Counter[str] = Counter()
    solver_failures = 0
    solver_timeouts = 0
    solver_infeasible = 0
    solver_errors = 0
    unexpected_fallbacks = 0
    invalid_slack = 0
    latencies: list[float] = []
    total_churn = 0.0
    total_group_hours = 0.0
    exact_surprise = True
    nonfinite_pair_count = 0
    sizing = None
    for path in paths:
        item = json.loads(path.read_text(encoding="utf-8"))
        arm = item["cell"]["arm"] if arm is None else arm
        sizing = item.get("sizing") if sizing is None else sizing
        if item["cell"]["arm"] != arm:
            raise ValueError(f"{node.name}: mixed candidate identities")
        family = item["cell"]["family"]
        family_counts[family] += 1
        baseline, candidate = item["static"], item["hybrid"]
        b_ul = float(baseline["overload_area_seconds"]["ul"])
        c_ul = float(candidate["overload_area_seconds"]["ul"])
        nonfinite_pair_count += int(not math.isfinite(b_ul) or not math.isfinite(c_ul))
        secondary = {
            name: _paired_delta(float(candidate[section][direction]), float(baseline[section][direction]))
            for name, section, direction in SECONDARY
        }
        secondary["establishment_failures"] = (
            int(candidate["establishment_failures"]) - int(baseline["establishment_failures"])
        )
        pairs.append({
            "family": family, "seed": int(item["cell"]["seed"]),
            "notice_hours": float(item["cell"].get("notice_hours", 0.0)),
            "baseline_ul": b_ul, "candidate_ul": c_ul,
            "informative": math.isfinite(b_ul) and b_ul > 0,
            "gain": _pair_gain(b_ul, c_ul),
            "delta": _paired_delta(c_ul, b_ul), "secondary": secondary,
        })
        if family in PURE_SURPRISE:
            exact_surprise &= _behavior(baseline) == _behavior(candidate)
        metrics = item.get("control_metrics", {})
        decision_funnel.update(metrics.get("decision_funnel", {}))
        fallback_reasons.update(metrics.get("decision_reasons", {}))
        # Reported apart because they mean different things: a timeout is a
        # compute-budget failure (v3's defect, where the MPC horizon scaled
        # with cadence and blew a 2 s budget), while an infeasible LP is a
        # formulation limit that still falls back safely to Static.  The gate
        # below counts both, unchanged; only the reporting is finer.
        timeouts = int(metrics.get("solver_timeout_count", 0))
        statuses = metrics.get("solver_statuses", {})
        timeouts += int(statuses.get("timeout", 0))
        infeasible = int(statuses.get("infeasible", 0))
        errors = int(statuses.get("error", 0))
        solver_timeouts += timeouts
        solver_infeasible += infeasible
        solver_errors += errors
        solver_failures += timeouts + infeasible + errors
        total_churn += float(metrics.get("routing_churn_l1", 0.0))
        total_group_hours += (
            int(item["group_count"]) * int(candidate["steps"]) * int(item["step_seconds"]) / 3600
        )
        for row in item.get("decision_diagnostics", []):
            latencies.append(float(row.get("decision_runtime_ms", 0)))
            disposition = str(row.get("disposition", ""))
            if disposition.startswith("static_fallback:") and not any(
                token in disposition for token in EXPECTED_FALLBACKS
            ):
                unexpected_fallbacks += 1
            slack = row.get("constraint_slack", {})
            if disposition == "accepted" and any(
                float(value) > 1e-7
                for category in slack.values() for value in category.values()
            ):
                invalid_slack += 1

    arm_index = int(arm["index"])
    family: dict[str, dict] = {}
    for position, name in enumerate(FAMILIES):
        selected = [item for item in pairs if item["family"] == name]
        informative = [item for item in selected if item["informative"]]
        finite = [item for item in selected if math.isfinite(item["baseline_ul"])
                  and math.isfinite(item["candidate_ul"])]
        baseline_total = sum(item["baseline_ul"] for item in finite)
        candidate_total = sum(item["candidate_ul"] for item in finite)
        family[name] = {
            "pair_count": len(selected),
            "informative_static_pairs": len(informative),
            # Severity weighting normalised inside the family, so a family whose
            # capacity denominator is small cannot dominate the campaign.
            "aggregate_gain": _pair_gain(baseline_total, candidate_total),
            "mean_informative_gain": float(np.mean([item["gain"] for item in informative]))
            if informative else 0.0,
            "mean_pair_gain": float(np.mean([item["gain"] for item in selected]))
            if selected else 0.0,
            "bootstrap_95pct_lower": _bootstrap_lower(
                [item["gain"] for item in informative], 20260824 + arm_index * 10 + position
            ),
            "worst_pair_gain": min((item["gain"] for item in selected), default=0.0),
            "overload_removed": sum(max(0.0, -item["delta"]) for item in finite),
            "overload_added": sum(max(0.0, item["delta"]) for item in finite),
        }

    informative_gains = [item["gain"] for item in pairs if item["informative"]]
    finite_pairs = [item for item in pairs
                    if math.isfinite(item["baseline_ul"]) and math.isfinite(item["candidate_ul"])]
    removed = sum(max(0.0, -item["delta"]) for item in finite_pairs)
    added = sum(max(0.0, item["delta"]) for item in finite_pairs)
    harm_ratio = added / removed if removed > 0 else (math.inf if added > 0 else 0.0)
    macro_gain = float(np.mean([family[name]["aggregate_gain"] for name in FAMILIES]))
    actionable_macro_gain = float(np.mean([family[name]["aggregate_gain"] for name in ACTIONABLE]))
    pooled_lower = _bootstrap_lower(informative_gains, 20260824 + arm_index)
    by_notice: dict[str, dict] = {}
    for value in sorted({item["notice_hours"] for item in pairs}):
        chosen = [item for item in pairs if item["notice_hours"] == value and item["informative"]]
        if not chosen:
            continue
        base = sum(item["baseline_ul"] for item in chosen)
        cand = sum(item["candidate_ul"] for item in chosen)
        by_notice[f"{value:g}h"] = {
            "informative_pairs": len(chosen), "aggregate_gain": _pair_gain(base, cand),
        }
    secondary_totals = {
        name: sum(item["secondary"][name] for item in pairs)
        for name in (*[entry[0] for entry in SECONDARY], "establishment_failures")
    }
    decisions = max(1, sum(decision_funnel.values()))
    churn = total_churn / total_group_hours if total_group_hours else 0.0

    validity = {
        "all_overload_metrics_finite": nonfinite_pair_count == 0,
        "balanced_families": all(family[name]["pair_count"] == family[FAMILIES[0]]["pair_count"]
                                 for name in FAMILIES),
        "every_family_has_informative_seeds": all(
            family[name]["informative_static_pairs"] > 0
            for name in FAMILIES if name not in PURE_SURPRISE
        ),
        "no_invalid_capacity_slack": invalid_slack == 0,
    }
    performance = {
        "macro_gain_at_least_10pct": macro_gain >= MACRO_GAIN_TARGET,
        "bootstrap_95pct_lower_above_zero": pooled_lower > 0,
        "no_family_aggregate_regression": all(
            family[name]["aggregate_gain"] >= 0 for name in FAMILIES
        ),
        "harm_ratio_within_limit": harm_ratio <= HARM_RATIO_LIMIT,
        "no_secondary_aggregate_regression": all(value <= 0 for value in secondary_totals.values()),
        "no_solver_timeout_or_error": solver_failures == 0,
        "unexpected_fallback_below_1pct": unexpected_fallbacks / decisions < 0.01,
        "churn_within_0_30_l1_per_group_hour": churn <= 0.30,
        "decision_latency_within_120s": max(latencies, default=0) <= 120_000,
        "pure_surprise_exact_static": exact_surprise,
    }
    return {
        "arm": arm, "sizing": sizing, "pair_count": len(pairs),
        "macro_gain": macro_gain, "actionable_macro_gain": actionable_macro_gain,
        "pooled_informative_bootstrap_lower": pooled_lower,
        "mean_pair_ul_gain": float(np.mean([item["gain"] for item in pairs])),
        "mean_informative_gain": float(np.mean(informative_gains)) if informative_gains else 0.0,
        "informative_pair_count": len(informative_gains),
        "overload_removed": removed, "overload_added": added, "harm_ratio": harm_ratio,
        "family": family, "by_notice": by_notice,
        "secondary_regressions": secondary_totals,
        "worst_pair_gain": min((item["gain"] for item in pairs), default=0.0),
        "worst_pairs": sorted(pairs, key=lambda item: item["gain"])[:5],
        "decision_funnel": dict(decision_funnel), "fallback_reasons": dict(fallback_reasons),
        "solver_failure_count": solver_failures,
        "solver_timeout_count": solver_timeouts,
        "solver_infeasible_count": solver_infeasible,
        "solver_error_count": solver_errors,
        "invalid_slack_count": invalid_slack,
        "nonfinite_pair_count": nonfinite_pair_count,
        "unexpected_fallback_fraction": unexpected_fallbacks / decisions,
        "churn_l1_per_group_hour": churn,
        "latency_max_ms": max(latencies, default=0),
        "latency_fraction_within_500ms": sum(value <= 500 for value in latencies) / max(1, len(latencies)),
        "validity": validity, "performance_gates": performance,
        "valid": all(validity.values()), "passed": all(validity.values()) and all(performance.values()),
    }


def analyze(root: Path, workers: int, expected_arms: int, cells: int = 125,
            stage_dir: str = "discovery", prefix: str = "node") -> dict:
    """Analyse only complete arms; report the incomplete ones loudly.

    A node that died part-way leaves a partial, family-unbalanced pair set.
    Scoring it would quietly bias the arm, so incomplete arms are excluded and
    named instead -- an unattended overnight run must not silently downgrade
    its own evidence.
    """
    found = sorted((root / stage_dir).glob(f"{prefix}-*"),
                   key=lambda p: int(p.name.split("-")[-1]))
    nodes, incomplete = [], []
    for node in found:
        count = len(list((node / "pairs").glob("*.json"))) if (node / "pairs").is_dir() else 0
        (nodes if count == cells else incomplete).append(
            node if count == cells else {"arm": int(node.name.split("-")[-1]), "pairs": count}
        )
    if incomplete:
        print(f"WARNING: {len(incomplete)} incomplete arms excluded: "
              f"{json.dumps(incomplete[:20], sort_keys=True)}")
    if expected_arms and len(nodes) != expected_arms:
        print(f"WARNING: expected {expected_arms} complete arms, analysing {len(nodes)}")
    if not nodes:
        raise SystemExit("no complete arm produced pairs; nothing to analyse")
    with ProcessPoolExecutor(max_workers=workers) as pool:
        arms = list(pool.map(analyze_arm, nodes))
    arms.sort(key=lambda item: int(item["arm"]["index"]))
    ranking = sorted(
        arms,
        key=lambda item: (
            item["passed"], item["performance_gates"]["no_family_aggregate_regression"],
            item["macro_gain"], item["pooled_informative_bootstrap_lower"],
        ), reverse=True,
    )
    digest = hashlib.sha256()
    for path in sorted(root.glob("discovery/node-*/pairs/*.json")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(str(path.stat().st_size).encode())
    return {
        "schema_version": "mixed-stress-discovery-analysis/4.0", "stage": stage_dir,
        "input_root": str(root.resolve()), "input_inventory_sha256": digest.hexdigest(),
        "arm_count": len(arms), "pair_count": sum(item["pair_count"] for item in arms),
        "incomplete_arms": incomplete,
        "valid_arm_count": sum(item["valid"] for item in arms),
        "passing_arm_count": sum(item["passed"] for item in arms),
        "passing_arm_indices": [int(item["arm"]["index"]) for item in arms if item["passed"]],
        "ranking": [int(item["arm"]["index"]) for item in ranking], "arms": arms,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--expected-arms", type=int, default=160)
    parser.add_argument("--cells", type=int, default=125)
    parser.add_argument("--stage-dir", default="discovery")
    parser.add_argument("--prefix", default="node")
    args = parser.parse_args()
    result = analyze(args.root, args.workers, args.expected_arms, args.cells,
                     args.stage_dir, args.prefix)
    atomic_checkpoint(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key != "arms"},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
