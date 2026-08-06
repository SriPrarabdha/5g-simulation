from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from forecasting import TrainedForecastBundle
from forecasting.bundle import TARGET_FIELDS, _sequence_training_rows
from simulator.macro.config import load_scenario

from .train_forecaster import collect_training_series


SCHEMA_VERSION = "forecast-baseline-evaluation/1.0"


def _log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    print(f"[{stamp}] {message}", flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float | int]:
    absolute = np.abs(actual - predicted)
    denominator = float(np.sum(np.abs(actual)))
    return {
        "rows": int(len(actual)),
        "mae": float(np.mean(absolute)) if len(absolute) else 0.0,
        "wape": float(np.sum(absolute) / denominator) if denominator else 0.0,
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    methods = ("calendar_ridge", "seasonal_naive_daily", "moving_average_6")

    def summarize(selected: list[dict[str, Any]]) -> dict[str, Any]:
        result = {
            method: {
                "macro_wape": statistics.fmean(row[method]["wape"] for row in selected),
                "mean_mae": statistics.fmean(row[method]["mae"] for row in selected),
            }
            for method in methods
        }
        ridge = result["calendar_ridge"]["macro_wape"]
        for baseline in ("seasonal_naive_daily", "moving_average_6"):
            baseline_wape = result[baseline]["macro_wape"]
            result["calendar_ridge"][f"relative_wape_reduction_vs_{baseline}"] = (
                (baseline_wape - ridge) / baseline_wape if baseline_wape else 0.0
            )
        return result

    return {
        "overall": summarize(rows),
        "by_horizon": [
            {"horizon_minutes": horizon, **summarize([
                row for row in rows if row["horizon_minutes"] == horizon
            ])}
            for horizon in range(10, 81, 10)
        ],
        "by_target": [
            {"target": target, **summarize([row for row in rows if row["target"] == target])}
            for target in TARGET_FIELDS
        ],
    }


def evaluate(
    bundle_path: Path,
    campaign_root: Path,
    manifest_path: Path,
    *,
    controller: str,
) -> dict[str, Any]:
    started = time.monotonic()
    bundle = TrainedForecastBundle.load(bundle_path)
    config = load_scenario(manifest_path)
    _log(f"phase=load status=started campaign_root={campaign_root}")
    series_by_group = collect_training_series(campaign_root, config, controller=controller)
    _log(
        f"phase=load status=complete groups={len(series_by_group)} "
        f"elapsed_seconds={time.monotonic() - started:.1f}"
    )
    if set(series_by_group) != set(bundle.payload["groups"]):
        raise ValueError("training corpus groups do not match the frozen model")

    rows: list[dict[str, Any]] = []
    ordered_groups = sorted(series_by_group.items())
    for group_number, (group_id, sequences) in enumerate(ordered_groups, start=1):
        for target in TARGET_FIELDS:
            for horizon in range(1, 9):
                feature_blocks: list[np.ndarray] = []
                actual_blocks: list[np.ndarray] = []
                seasonal_blocks: list[np.ndarray] = []
                for observations in sequences:
                    features, actual = _sequence_training_rows(observations, target, horizon)
                    if not len(features):
                        continue
                    values = np.fromiter(
                        (float(getattr(item, target)) for item in observations),
                        dtype=np.float64,
                        count=len(observations),
                    )
                    origins = np.arange(5, len(observations) - horizon, dtype=np.int64)
                    seasonal_indices = origins + horizon - 144
                    seasonal = values[origins].copy()
                    available = seasonal_indices >= 0
                    seasonal[available] = values[seasonal_indices[available]]
                    feature_blocks.append(features)
                    actual_blocks.append(actual)
                    seasonal_blocks.append(seasonal)

                features = np.concatenate(feature_blocks, axis=0)
                actual = np.concatenate(actual_blocks)
                seasonal = np.concatenate(seasonal_blocks)
                row_count = len(actual)
                train_end = max(12, int(row_count * 0.70))
                calibration_end = min(max(train_end + 6, int(row_count * 0.85)), row_count - 1)
                model_entry = bundle.payload["groups"][group_id]["targets"][target][str(horizon)]
                model = model_entry["model"]
                coefficients = np.asarray(model["coefficients"], dtype=np.float64)
                ridge_predictions = np.maximum(
                    0.0,
                    features[calibration_end:] @ coefficients + float(model["median_bias"]),
                )
                test_actual = actual[calibration_end:]
                ridge_metrics = _metrics(test_actual, ridge_predictions)
                stored_wape = float(model_entry["metrics"]["wape_p50"])
                if not np.isclose(ridge_metrics["wape"], stored_wape, rtol=1e-10, atol=1e-12):
                    raise ValueError(
                        f"recomputed metric differs from bundle for {group_id} {target} h={horizon}"
                    )
                rows.append({
                    "group_id": group_id,
                    "target": target,
                    "horizon_minutes": horizon * 10,
                    "calendar_ridge": ridge_metrics,
                    "seasonal_naive_daily": _metrics(
                        test_actual, seasonal[calibration_end:]
                    ),
                    "moving_average_6": _metrics(
                        test_actual, features[calibration_end:, 2]
                    ),
                })
        _log(
            f"phase=evaluate status=running groups={group_number}/{len(ordered_groups)} "
            f"progress={group_number / len(ordered_groups) * 100:.1f}% group_id={group_id}"
        )

    aggregate = _aggregate(rows)
    reduction = aggregate["overall"]["calendar_ridge"][
        "relative_wape_reduction_vs_seasonal_naive_daily"
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model": {
            "path": str(bundle_path),
            "file_sha256": _sha256(bundle_path),
            "bundle_sha256": bundle.payload["bundle_sha256"],
            "model_version": bundle.payload["model_version"],
        },
        "corpus": {
            "campaign_root": str(campaign_root),
            "manifest": str(manifest_path),
            "manifest_file_sha256": _sha256(manifest_path),
            "controller_filter": controller,
        },
        "comparison_contract": {
            "test_rows": "exact ordered final 15% used by each frozen direct model",
            "seasonal_naive_daily": "same traffic group and target, 144 ten-minute buckets earlier",
            "moving_average_6": "mean of the six ten-minute buckets available at forecast origin",
            "aggregation": "unweighted macro mean of per-group/target/horizon WAPE",
            "leakage": "all predictions use values available at their forecast origin only",
        },
        "aggregate": aggregate,
        "gate": {
            "name": "all-held-out WAPE improves at least 10% over daily seasonal naive",
            "required_relative_reduction": 0.10,
            "observed_relative_reduction": reduction,
            "passed": reduction >= 0.10,
            "scope_warning": "The release gate calls for non-event and event-stratified results; this is the all-held-out baseline component only.",
        },
        "per_model": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a frozen forecast bundle against causal baselines")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--controller", default="static-capacity-v1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite evaluation: {args.output}")
    result = evaluate(
        args.bundle,
        args.campaign_root,
        args.manifest,
        controller=args.controller,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    ridge = result["aggregate"]["overall"]["calendar_ridge"]
    print(json.dumps({
        "output": str(args.output),
        "gate_passed": result["gate"]["passed"],
        "ridge_macro_wape": ridge["macro_wape"],
        "seasonal_naive_macro_wape": result["aggregate"]["overall"]["seasonal_naive_daily"]["macro_wape"],
        "relative_reduction": result["gate"]["observed_relative_reduction"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
