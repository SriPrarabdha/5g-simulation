from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _weighted(rows: list[dict[str, Any]], metric: str) -> float:
    total = sum(float(row["actual_sum"]) for row in rows)
    return (
        sum(float(row[metric]) * float(row["actual_sum"]) for row in rows) / total
        if total else 0.0
    )


def aggregate(root: Path) -> dict[str, Any]:
    by_family: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(root.rglob("metrics-*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("schema_version") != "forecast-selection-group/1.0" or row.get("seed") != 46002:
            raise ValueError(f"invalid selection metric shard: {path}")
        by_family.setdefault(row["model_family"], []).append(row)
    if not by_family:
        raise ValueError("no forecast selection metrics found")
    candidates = []
    for family, groups in sorted(by_family.items()):
        if len(groups) != 96 or len({row["group_id"] for row in groups}) != 96:
            raise ValueError(f"family {family} does not contain all 96 groups exactly once")
        overall = [row["overall"] for row in groups]
        wape = _weighted(overall, "wape")
        moving = _weighted(overall, "moving_average_wape")
        seasonal = _weighted(overall, "seasonal_naive_wape")
        best_name, best_wape = min(
            (("moving_average", moving), ("seasonal_naive", seasonal)), key=lambda item: item[1]
        )
        coverage = sum(
            row["coverage_p90"] * row["count"] for row in overall
        ) / sum(row["count"] for row in overall)
        episodes = [
            episode for group in groups for episode in group["event_episodes"]
            if episode["regime"] in {"scheduled_event", "detected_surge"}
        ]
        candidate_peak = sum(row["peak_underprediction"] for row in episodes)
        baseline_peak_key = f"{best_name}_peak_underprediction"
        baseline_peak = sum(row[baseline_peak_key] for row in episodes)
        slice_groups: defaultdict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
        group_slice_regressions = []
        for group in groups:
            for item in group["slices"]:
                baseline = item[f"{best_name}_wape"]
                if baseline is not None and item["wape"] is not None and baseline > 0:
                    group_slice_regressions.append(item["wape"] / baseline - 1.0)
                slice_groups[(item["target"], item["horizon_minutes"], item["regime"])].append(item)
        slice_regressions = []
        for items in slice_groups.values():
            candidate_slice = _weighted(items, "wape")
            baseline_slice = _weighted(items, f"{best_name}_wape")
            if baseline_slice > 0:
                slice_regressions.append(candidate_slice / baseline_slice - 1.0)
        gates = {
            "wape_improves_15_percent_over_best_simple_baseline": wape <= 0.85 * best_wape,
            "scheduled_detected_peak_underprediction_improves_20_percent": (
                baseline_peak > 0 and candidate_peak <= 0.80 * baseline_peak
            ),
            "p90_coverage_between_88_and_95_percent": 0.88 <= coverage <= 0.95,
            "no_regime_or_horizon_worsens_over_5_percent": (
                bool(slice_regressions) and max(slice_regressions) <= 0.05
            ),
            "unknown_surge_scored_only_after_observation": all(
                item["regime"] != "unknown_surge"
                for group in groups for item in group["slices"]
            ),
        }
        candidates.append({
            "model_family": family, "groups": len(groups), "wape": wape,
            "best_simple_baseline": best_name, "best_simple_baseline_wape": best_wape,
            "relative_wape_improvement": (best_wape - wape) / best_wape if best_wape else 0.0,
            "coverage_p90": coverage, "event_peak_underprediction": candidate_peak,
            "baseline_event_peak_underprediction": baseline_peak,
            "max_slice_regression": max(slice_regressions, default=None),
            "max_group_slice_regression_diagnostic": max(
                group_slice_regressions, default=None
            ),
            "gates": gates, "eligible": all(gates.values()),
            "bundle_sha256_by_group": {
                row["group_id"]: row["bundle_sha256"] for row in groups
            },
            "excluded_pre_observation_unknown_surge_rows": sum(
                row["excluded_pre_observation_unknown_surge_rows"] for row in groups
            ),
            "excluded_pre_observation_unknown_surge_rows": sum(
                row["excluded_pre_observation_unknown_surge_rows"] for row in groups
            ),
        })
    eligible = [row for row in candidates if row["eligible"]]
    selected = min(eligible, key=lambda row: row["wape"]) if eligible else None
    return {
        "schema_version": "forecast-selection/1.0", "train_seed": 46001,
        "selection_calibration_seed": 46002, "protected_test_seed_consumed": False,
        "candidates": candidates,
        "selected_model_family": selected["model_family"] if selected else None,
        "eligible_for_seed_46003_test": selected is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate fail-closed Phase-2 forecast selection")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite forecast selection: {args.output}")
    result = aggregate(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output), "selected": result["selected_model_family"],
        "eligible_for_test": result["eligible_for_seed_46003_test"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
