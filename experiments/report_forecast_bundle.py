from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from forecasting import TrainedForecastBundle


TARGETS = ("new_session_count", "new_ul_mbps", "new_dl_mbps")


def _mean(items: list[dict[str, float]], field: str) -> float:
    return statistics.fmean(item[field] for item in items)


def build_report(path: Path) -> dict[str, Any]:
    bundle = TrainedForecastBundle.load(path)
    payload = bundle.payload
    groups = payload["groups"]
    all_metrics: list[dict[str, float]] = []
    by_horizon: list[dict[str, float | int]] = []
    by_target: list[dict[str, float | str]] = []

    for horizon in range(1, 9):
        metrics = [
            group["targets"][target][str(horizon)]["metrics"]
            for group in groups.values()
            for target in TARGETS
        ]
        all_metrics.extend(metrics)
        by_horizon.append({
            "horizon_minutes": horizon * 10,
            "macro_wape": _mean(metrics, "wape_p50"),
            "mean_coverage_p90": _mean(metrics, "coverage_p90"),
            "mean_coverage_p95": _mean(metrics, "coverage_p95"),
        })

    for target in TARGETS:
        metrics = [
            group["targets"][target][str(horizon)]["metrics"]
            for group in groups.values()
            for horizon in range(1, 9)
        ]
        by_target.append({
            "target": target,
            "macro_wape": _mean(metrics, "wape_p50"),
            "mean_mae": _mean(metrics, "mae_p50"),
            "mean_coverage_p90": _mean(metrics, "coverage_p90"),
            "mean_coverage_p95": _mean(metrics, "coverage_p95"),
        })

    session_models = sorted(
        (
            {
                "group_id": group_id,
                "horizon_minutes": horizon * 10,
                **group["targets"]["new_session_count"][str(horizon)]["metrics"],
            }
            for group_id, group in groups.items()
            for horizon in range(1, 9)
        ),
        key=lambda item: (-item["wape_p50"], item["group_id"], item["horizon_minutes"]),
    )
    wapes = sorted(item["wape_p50"] for item in all_metrics)
    return {
        "bundle": str(path),
        "model_version": payload["model_version"],
        "bundle_sha256": payload["bundle_sha256"],
        "synthetic": payload["synthetic"],
        "split": payload["split"],
        "groups": len(groups),
        "fitted_models": len(all_metrics),
        "overall": {
            "macro_wape": statistics.fmean(wapes),
            "wape_based_score": 1.0 - statistics.fmean(wapes),
            "median_model_wape": statistics.median(wapes),
            "p90_model_wape": wapes[round((len(wapes) - 1) * 0.90)],
            "worst_model_wape": max(wapes),
            "mean_coverage_p90": _mean(all_metrics, "coverage_p90"),
            "mean_coverage_p95": _mean(all_metrics, "coverage_p95"),
        },
        "by_horizon": by_horizon,
        "by_target": by_target,
        "worst_session_models": session_models[:10],
    }


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def print_text(report: dict[str, Any]) -> None:
    overall = report["overall"]
    print(f"Model: {report['model_version']}")
    print(f"SHA-256: {report['bundle_sha256']}")
    print(f"Groups / fitted models: {report['groups']} / {report['fitted_models']}")
    print(f"Ordered split: {report['split']['train']:.0%} train / {report['split']['calibration']:.0%} calibration / {report['split']['test']:.0%} test")
    print(f"Macro WAPE: {_percent(overall['macro_wape'])}")
    print(f"WAPE-based score (1-WAPE): {_percent(overall['wape_based_score'])}")
    print(f"Mean p90 / p95 upper-bound coverage: {_percent(overall['mean_coverage_p90'])} / {_percent(overall['mean_coverage_p95'])}")
    print("\nHorizon | Macro WAPE | p90 coverage | p95 coverage")
    print("---:|---:|---:|---:")
    for row in report["by_horizon"]:
        print(
            f"{row['horizon_minutes']} min | {_percent(row['macro_wape'])} | "
            f"{_percent(row['mean_coverage_p90'])} | {_percent(row['mean_coverage_p95'])}"
        )
    print("\nTarget | Macro WAPE | Mean MAE | p90 coverage | p95 coverage")
    print("---|---:|---:|---:|---:")
    for row in report["by_target"]:
        print(
            f"{row['target']} | {_percent(row['macro_wape'])} | {row['mean_mae']:.3f} | "
            f"{_percent(row['mean_coverage_p90'])} | {_percent(row['mean_coverage_p95'])}"
        )
    print("\nWorst session-count models")
    for row in report["worst_session_models"][:5]:
        print(f"- {row['group_id']} @ {row['horizon_minutes']} min: {_percent(row['wape_p50'])} WAPE")


def main() -> int:
    parser = argparse.ArgumentParser(description="Report held-out metrics from a trained forecast bundle")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    report = build_report(args.bundle)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
