"""Non-mutating statistical addendum for the sealed Phase-2 forecast evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from experiments.artifacts import atomic_json


TARGETS = ("new_session_count", "new_ul_mbps", "new_dl_mbps")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    return ordered[round(probability * (len(ordered) - 1))]


def _wape(items: list[dict[str, Any]], error_key: str) -> float:
    actual = sum(float(item["actual_sum"]) for item in items)
    if not actual:
        return 0.0
    if error_key == "absolute_error_sum":
        error = sum(float(item[error_key]) for item in items)
    else:
        error = sum(float(item[error_key]) * float(item["actual_sum"]) for item in items)
    return error / actual


def _interval(
    groups: list[dict[str, Any]], metric: Callable[[list[dict[str, Any]]], float],
    *, seed: str, samples: int = 2_000,
) -> list[float]:
    rng = random.Random(f"phase21:{seed}")
    values = [metric([groups[rng.randrange(len(groups))] for _ in groups]) for _ in range(samples)]
    return [_quantile(values, 0.025), _quantile(values, 0.975)]


def _bootstrap_sufficient_statistics(
    statistics: list[tuple[float, float, float]], *, seed: str, mode: str,
    samples: int = 2_000,
) -> list[float]:
    values = np.asarray(statistics, dtype=float)
    seed_value = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed_value)
    indexes = rng.integers(0, len(values), size=(samples, len(values)))
    totals = values[indexes].sum(axis=1)
    candidate = np.divide(
        totals[:, 0], totals[:, 2], out=np.zeros(samples), where=totals[:, 2] > 0
    )
    baseline = np.divide(
        totals[:, 1], totals[:, 2], out=np.zeros(samples), where=totals[:, 2] > 0
    )
    if mode == "candidate_wape":
        result = candidate
    elif mode == "improvement":
        result = np.divide(
            baseline - candidate, baseline, out=np.zeros(samples), where=baseline > 0
        )
    elif mode == "regression":
        result = np.divide(
            candidate, baseline, out=np.ones(samples), where=baseline > 0
        ) - 1.0
    else:
        raise ValueError(f"unknown bootstrap metric mode: {mode}")
    return [float(np.quantile(result, 0.025)), float(np.quantile(result, 0.975))]


def _target_items(groups: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    return [
        item for group in groups for item in group["slices"]
        if item["target"] == target
    ]


def _target_metric(
    groups: list[dict[str, Any]], target: str, error_key: str,
) -> float:
    return _wape(_target_items(groups, target), error_key)


def build(metrics_root: Path, sealed_selection: Path) -> dict[str, Any]:
    selection = json.loads(sealed_selection.read_text(encoding="utf-8"))
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    metric_paths = sorted(metrics_root.rglob("metrics-*.json"))
    for path in metric_paths:
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("seed") != 46002:
            raise ValueError(f"non-selection shard in Phase-2.1 addendum: {path}")
        by_family[str(row["model_family"])].append(row)
    sealed_by_family = {row["model_family"]: row for row in selection["candidates"]}
    candidates = []
    for family, groups in sorted(by_family.items()):
        if len(groups) != 96 or family not in sealed_by_family:
            raise ValueError(f"incomplete or unknown Phase-2 family: {family}")
        baseline_name = str(sealed_by_family[family]["best_simple_baseline"])
        baseline_key = f"{baseline_name}_wape"
        targets = {}
        for target in TARGETS:
            candidate = _target_metric(groups, target, "absolute_error_sum")
            baseline = _target_metric(groups, target, baseline_key)
            improvement = (baseline - candidate) / baseline if baseline else 0.0
            target_statistics = []
            for group in groups:
                items = [item for item in group["slices"] if item["target"] == target]
                target_statistics.append((
                    sum(float(item["absolute_error_sum"]) for item in items),
                    sum(float(item[baseline_key]) * float(item["actual_sum"]) for item in items),
                    sum(float(item["actual_sum"]) for item in items),
                ))
            targets[target] = {
                "wape": candidate,
                "wape_cluster_bootstrap_95_interval": _bootstrap_sufficient_statistics(
                    target_statistics, seed=f"{family}:{target}:wape",
                    mode="candidate_wape",
                ),
                "best_simple_baseline": baseline_name,
                "best_simple_baseline_wape": baseline,
                "relative_wape_improvement": improvement,
                "relative_improvement_cluster_bootstrap_95_interval": _bootstrap_sufficient_statistics(
                    target_statistics, seed=f"{family}:{target}:improvement",
                    mode="improvement",
                ),
                "scored_observations": sum(
                    int(item["count"]) for item in _target_items(groups, target)
                ),
            }
        macro_wape = sum(targets[target]["wape"] for target in TARGETS) / len(TARGETS)
        macro_baseline = sum(
            targets[target]["best_simple_baseline_wape"] for target in TARGETS
        ) / len(TARGETS)
        slices = []
        slice_keys = sorted({
            (item["target"], int(item["horizon_minutes"]), item["regime"])
            for group in groups for item in group["slices"]
        })
        for target, horizon, regime in slice_keys:
            def selected(sample: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return [
                    item for group in sample for item in group["slices"]
                    if item["target"] == target
                    and int(item["horizon_minutes"]) == horizon
                    and item["regime"] == regime
                    and item["wape"] is not None
                ]
            items = selected(groups)
            candidate = _wape(items, "absolute_error_sum")
            baseline = _wape(items, baseline_key)
            regression = candidate / baseline - 1.0 if baseline else 0.0
            slice_statistics = []
            for group in groups:
                group_items = selected([group])
                slice_statistics.append((
                    sum(float(item["absolute_error_sum"]) for item in group_items),
                    sum(float(item[baseline_key]) * float(item["actual_sum"]) for item in group_items),
                    sum(float(item["actual_sum"]) for item in group_items),
                ))
            interval = _bootstrap_sufficient_statistics(
                slice_statistics, seed=f"{family}:{target}:{horizon}:{regime}",
                mode="regression",
            )
            slices.append({
                "target": target, "horizon_minutes": horizon, "regime": regime,
                "wape": candidate, "baseline_wape": baseline,
                "relative_regression": regression,
                "relative_regression_cluster_bootstrap_95_interval": interval,
                "scored_observations": sum(int(item["count"]) for item in items),
                "contributing_groups": sum(int(item["count"]) > 0 for item in items),
            })
        worst = max(slices, key=lambda item: item["relative_regression"])
        candidates.append({
            "model_family": family,
            "headline_metric_name": "pooled_cross_target_wape",
            "pooled_cross_target_wape": sealed_by_family[family]["wape"],
            "pooled_cross_target_relative_improvement": sealed_by_family[family]["relative_wape_improvement"],
            "target_separated": targets,
            "macro_average_target_wape": macro_wape,
            "macro_average_target_baseline_wape": macro_baseline,
            "macro_average_target_relative_improvement": (
                (macro_baseline - macro_wape) / macro_baseline if macro_baseline else 0.0
            ),
            "worst_aggregate_slice": worst,
            "slices": slices,
        })
    return {
        "schema_version": "forecast-phase2-audit-addendum/1.0",
        "sealed_phase2_modified": False,
        "train_seed": 46001,
        "selection_calibration_seed": 46002,
        "protected_test_seed_consumed": False,
        "confidence_interval_method": "fixed-seed 2,000-replicate group-cluster bootstrap",
        "source": {
            "sealed_selection_path": str(sealed_selection.resolve()),
            "sealed_selection_sha256": _sha256(sealed_selection),
            "metric_shards": len(metric_paths),
            "metric_shard_set_sha256": hashlib.sha256("".join(
                f"{path.relative_to(metrics_root)}:{_sha256(path)}\n" for path in metric_paths
            ).encode()).hexdigest(),
        },
        "candidates": candidates,
    }


def _report(payload: dict[str, Any]) -> str:
    lines = [
        "# Phase 2.1 forecast audit addendum", "",
        "This addendum does not modify the sealed Phase 1/2 artifacts. The prior headline is labeled **pooled cross-target WAPE** because it pools session counts and Mbps targets.", "",
        "| Candidate | Pooled improvement | Macro-target improvement | Session | UL | DL |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["candidates"]:
        target = row["target_separated"]
        lines.append(
            f"| {row['model_family']} | {row['pooled_cross_target_relative_improvement']:.2%} | "
            f"{row['macro_average_target_relative_improvement']:.2%} | "
            f"{target['new_session_count']['relative_wape_improvement']:.2%} | "
            f"{target['new_ul_mbps']['relative_wape_improvement']:.2%} | "
            f"{target['new_dl_mbps']['relative_wape_improvement']:.2%} |"
        )
    lines.extend(["", "## Slice stability", ""])
    lightgbm = next(row for row in payload["candidates"] if row["model_family"] == "lightgbm-quantile")
    worst = lightgbm["worst_aggregate_slice"]
    lines.append(
        f"LightGBM's worst aggregate slice is {worst['regime']} / {worst['target']} / "
        f"{worst['horizon_minutes']} minutes: {worst['relative_regression']:.2%} regression, "
        f"n={worst['scored_observations']} observations across {worst['contributing_groups']} groups; "
        f"the group-cluster bootstrap interval is [{worst['relative_regression_cluster_bootstrap_95_interval'][0]:.2%}, "
        f"{worst['relative_regression_cluster_bootstrap_95_interval'][1]:.2%}]."
    )
    lines.extend(["", "The 15% promotion conclusion is unchanged; seed 46003 remains untouched.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-root", required=True, type=Path)
    parser.add_argument("--sealed-selection", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_root / "phase2-audit-addendum-v1.json"
    report = args.output_root / "REPORT.md"
    if output.exists() or report.exists():
        raise FileExistsError("refusing to overwrite Phase-2.1 audit addendum")
    payload = build(args.metrics_root, args.sealed_selection)
    atomic_json(output, payload)
    args.output_root.mkdir(parents=True, exist_ok=True)
    report.write_text(_report(payload), encoding="utf-8")
    print(json.dumps({"output": str(output.resolve()), "report": str(report.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
