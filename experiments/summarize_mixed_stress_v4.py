"""Compact console summary of a v4 mixed-stress analysis.

Prints the campaign verdict, the gate-failure census, and the leading arms, so
a run can be read without loading the full analysis JSON.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from experiments.analyze_mixed_stress_v4 import ACTIONABLE, PURE_SURPRISE
from experiments.mixed_stress_campaign_v4 import FAMILIES

SHORT = {name: name.replace("maintenance", "maint").replace("declared_", "") for name in FAMILIES}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()
    data = json.loads(args.analysis.read_text(encoding="utf-8"))
    arms = data["arms"]

    print(f"arms={data['arm_count']}  pairs={data['pair_count']}  "
          f"valid={data['valid_arm_count']}  PASSING={data['passing_arm_count']}")

    validity = Counter()
    performance = Counter()
    for arm in arms:
        validity.update(k for k, v in arm["validity"].items() if not v)
        performance.update(k for k, v in arm["performance_gates"].items() if not v)
    print("\nvalidity failures (data defects, not controller verdicts):")
    for key, count in validity.most_common() or [("none", 0)]:
        print(f"  {count:4d}/{len(arms)}  {key}")
    print("\nperformance gate failures:")
    for key, count in performance.most_common() or [("none", 0)]:
        print(f"  {count:4d}/{len(arms)}  {key}")

    clean = [a for a in arms
             if a["performance_gates"]["no_family_aggregate_regression"]
             and a["performance_gates"]["no_solver_timeout_or_error"]]
    print(f"\narms with no family regression and no solver failure: {len(clean)}/{len(arms)}")

    header = (f"{'idx':>4} {'ctl':>8} {'cad':>3} {'bl':>5} {'hz':>2} {'res':>4} {'env':>5} | "
              f"{'macro':>7} {'action':>7} {'boot':>7} {'harm':>6} {'churn':>6} {'lat_ms':>7} | " +
              " ".join(f"{SHORT[n][:9]:>9}" for n in FAMILIES))
    print("\n" + header)
    ranked = sorted(arms, key=lambda a: (a["passed"], a["macro_gain"]), reverse=True)
    for arm in ranked[:args.top]:
        a, f = arm["arm"], arm["family"]
        mark = "PASS" if arm["passed"] else "    "
        print(f"{a['index']:>4} {a['controller']:>8} {a['cadence_minutes']:>3} "
              f"{a['maximum_blend']:>5} {a['horizon_hours']:>2} {a['destination_reserve']:>4} "
              f"{a['surprise_capacity_factor']:>5} | {arm['macro_gain']:+7.4f} "
              f"{arm['actionable_macro_gain']:+7.4f} "
              f"{arm['pooled_informative_bootstrap_lower']:+7.4f} {arm['harm_ratio']:6.3f} "
              f"{arm['churn_l1_per_group_hour']:6.3f} {arm['latency_max_ms']:7.0f} | " +
              " ".join(f"{f[n]['aggregate_gain']:+9.4f}" for n in FAMILIES) + f" {mark}")

    best = ranked[0]
    print(f"\nleading arm {best['arm']['index']}  informative pairs="
          f"{best['informative_pair_count']}/{best['pair_count']}")
    for name in FAMILIES:
        row = best["family"][name]
        tag = "tie-by-design" if name in PURE_SURPRISE else ""
        print(f"  {name:26s} agg={row['aggregate_gain']:+.4f} "
              f"informative_mean={row['mean_informative_gain']:+.4f} "
              f"n_informative={row['informative_static_pairs']:2d} {tag}")
    print(f"  notice slices: {json.dumps(best['by_notice'], sort_keys=True)}")
    print(f"  overload removed={best['overload_removed']:.1f} "
          f"added={best['overload_added']:.1f} harm_ratio={best['harm_ratio']:.4f}")
    print(f"  actionable families: {', '.join(ACTIONABLE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
