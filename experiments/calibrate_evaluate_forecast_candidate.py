from __future__ import annotations

import argparse
import gzip
import json
import math
import pickle
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from forecasting import CandidateForecastBundle, load_forecaster_bundle, write_candidate_forecast_bundle
from forecasting.candidates import TARGET_FIELDS
from schemas import TimeWindow


def _load(cache_root: Path, group_index: int):
    index = json.loads((cache_root / "index.json").read_text(encoding="utf-8"))
    if index.get("purpose") != "selection" or int(index.get("seed", -1)) != 46002:
        raise ValueError("calibration/evaluation requires the frozen seed-46002 selection cache")
    entry = index["groups"][group_index]
    with gzip.open(cache_root / entry["path"], "rb") as stream:
        sequences = pickle.load(stream)
    if len(sequences) != 1:
        raise ValueError("selection cache must contain exactly one sequence per group")
    return entry, sequences[0]


def _field(forecast: Any, target: str):
    return {
        "new_session_count": forecast.new_session_count,
        "new_ul_mbps": forecast.new_load_ul_mbps,
        "new_dl_mbps": forecast.new_load_dl_mbps,
    }[target]


def _episodes(sequence: list[Any]) -> list[str]:
    result = []
    counts: defaultdict[str, int] = defaultdict(int)
    previous = "normal"
    current = "normal:0"
    for item in sequence:
        regime = item.regime
        if regime != previous:
            counts[regime] += 1
            current = f"{regime}:{counts[regime]}"
        result.append(current if regime != "normal" else "normal:0")
        previous = regime
    return result


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actual_sum = sum(row["actual"] for row in rows)
    return {
        "count": len(rows), "actual_sum": actual_sum,
        "absolute_error_sum": sum(abs(row["actual"] - row["p50"]) for row in rows),
        "wape": (
            sum(abs(row["actual"] - row["p50"]) for row in rows) / actual_sum
            if actual_sum else None
        ),
        "coverage_p90": sum(row["actual"] <= row["p90"] for row in rows) / len(rows),
        "coverage_p95": sum(row["actual"] <= row["p95"] for row in rows) / len(rows),
        "peak_underprediction": max((max(0.0, row["actual"] - row["p50"]) for row in rows), default=0.0),
        "moving_average_peak_underprediction": max(
            (max(0.0, row["actual"] - row["moving_average"]) for row in rows), default=0.0
        ),
        "seasonal_naive_peak_underprediction": max(
            (max(0.0, row["actual"] - row["seasonal_naive"]) for row in rows), default=0.0
        ),
        "moving_average_peak_underprediction": max(
            (max(0.0, row["actual"] - row["moving_average"]) for row in rows), default=0.0
        ),
        "seasonal_naive_peak_underprediction": max(
            (max(0.0, row["actual"] - row["seasonal_naive"]) for row in rows), default=0.0
        ),
        "moving_average_wape": (
            sum(abs(row["actual"] - row["moving_average"]) for row in rows) / actual_sum
            if actual_sum else None
        ),
        "seasonal_naive_wape": (
            sum(abs(row["actual"] - row["seasonal_naive"]) for row in rows) / actual_sum
            if actual_sum else None
        ),
    }


def calibrate_evaluate(
    input_bundle: Path, selection_cache: Path, group_index: int,
    output_bundle: Path | None,
) -> dict[str, Any]:
    entry, sequence = _load(selection_cache, group_index)
    candidate_bundle = input_bundle.is_dir()
    bundle = load_forecaster_bundle(input_bundle)
    bundle.validate_groups([sequence[0].group])
    model = bundle.model if candidate_bundle else bundle
    base = model.normal_model if hasattr(model, "normal_model") else model
    required = model.required_history_windows
    split = len(sequence) // 2
    horizons = list(bundle.manifest["horizons"]) if candidate_bundle else [1, 2, 3, 8]
    residuals: defaultdict[tuple[str, int], list[float]] = defaultdict(list)
    if candidate_bundle:
        for horizon in horizons:
            for origin in range(required - 1, split - int(horizon)):
                target_index = origin + int(horizon)
                target = sequence[target_index]
                forecast = model.predict(
                    sequence[:origin + 1], issued_at=sequence[origin].window.end,
                    target_window=target.window, horizon_steps=int(horizon),
                )
                for field in TARGET_FIELDS:
                    residuals[(field, int(horizon))].append(
                        abs(float(getattr(target, field)) - _field(forecast, field).p50)
                    )
    group_id = entry["group_id"]
    if candidate_bundle:
        for (field, horizon), values in residuals.items():
            base.calibration_widths[(group_id, field, horizon)] = (
                float(np.quantile(values, 0.90)), float(np.quantile(values, 0.95))
            )
        if output_bundle is None:
            raise ValueError("causal candidates require --output-bundle")
        calibrated_manifest = write_candidate_forecast_bundle(
            output_bundle, model,
            source={
                **bundle.manifest["source"], "calibration_seed": 46002,
                "calibration_fraction": 0.5, "selection_fraction": 0.5,
                "parent_bundle_sha256": bundle.manifest["bundle_sha256"],
            },
        )
        model_family = calibrated_manifest["model_family"]
        bundle_sha256 = calibrated_manifest["bundle_sha256"]
    else:
        model_family = "calendar-ridge"
        bundle_sha256 = bundle.metadata["bundle_sha256"]

    episodes = _episodes(sequence)
    values_by_field = {
        field: [float(getattr(item, field)) for item in sequence] for field in TARGET_FIELDS
    }
    rows: list[dict[str, Any]] = []
    excluded_pre_observation_unknown_surge_rows = 0
    excluded_pre_observation_unknown_surge_rows = 0
    for horizon in horizons:
        horizon = int(horizon)
        for origin in range(max(required - 1, split - horizon), len(sequence) - horizon):
            target_index = origin + horizon
            if target_index < split:
                continue
            target = sequence[target_index]
            if (
                target.regime in {"unknown_surge", "detected_surge"}
                and sequence[origin].regime != "detected_surge"
            ):
                excluded_pre_observation_unknown_surge_rows += len(TARGET_FIELDS)
                continue
            if (
                target.regime in {"unknown_surge", "detected_surge"}
                and sequence[origin].regime != "detected_surge"
            ):
                excluded_pre_observation_unknown_surge_rows += len(TARGET_FIELDS)
                continue
            forecast = model.predict(
                sequence[:origin + 1], issued_at=sequence[origin].window.end,
                target_window=target.window, horizon_steps=horizon,
            )
            for field in TARGET_FIELDS:
                values = values_by_field[field]
                quantiles = _field(forecast, field)
                recent = values[max(0, origin - 5):origin + 1]
                seasonal_index = origin + horizon - 144
                rows.append({
                    "target": field, "horizon_minutes": horizon * 10,
                    "regime": target.regime, "episode": episodes[target_index],
                    "actual": values[target_index], "p50": quantiles.p50,
                    "p90": quantiles.p90 if quantiles.p90 is not None else quantiles.p95,
                    "p95": quantiles.p95,
                    "moving_average": statistics.median(recent),
                    "seasonal_naive": values[seasonal_index] if seasonal_index >= 0 else values[origin],
                })
    keys = sorted({(row["target"], row["horizon_minutes"], row["regime"]) for row in rows})
    episode_keys = sorted({
        (row["target"], row["horizon_minutes"], row["regime"], row["episode"])
        for row in rows if row["regime"] != "normal"
    })
    return {
        "schema_version": "forecast-selection-group/1.0", "seed": 46002,
        "group_id": group_id, "group_index": group_index,
        "model_family": model_family,
        "bundle_sha256": bundle_sha256,
        "excluded_pre_observation_unknown_surge_rows": (
            excluded_pre_observation_unknown_surge_rows
        ),
        "excluded_pre_observation_unknown_surge_rows": (
            excluded_pre_observation_unknown_surge_rows
        ),
        "overall": _summary(rows),
        "slices": [
            {"target": target, "horizon_minutes": horizon, "regime": regime,
             **_summary([row for row in rows if (row["target"], row["horizon_minutes"], row["regime"]) == key])}
            for key in keys for target, horizon, regime in [key]
        ],
        "event_episodes": [
            {"target": target, "horizon_minutes": horizon, "regime": regime, "episode": episode,
             **_summary([row for row in rows if (row["target"], row["horizon_minutes"], row["regime"], row["episode"]) == key])}
            for key in episode_keys for target, horizon, regime, episode in [key]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate and evaluate one causal forecast group")
    parser.add_argument("--input-bundle", required=True, type=Path)
    parser.add_argument("--selection-cache", required=True, type=Path)
    parser.add_argument("--group-index", required=True, type=int)
    parser.add_argument("--output-bundle", type=Path)
    parser.add_argument("--output-metrics", required=True, type=Path)
    args = parser.parse_args()
    if args.output_metrics.exists():
        raise FileExistsError(f"refusing to overwrite metrics: {args.output_metrics}")
    result = calibrate_evaluate(
        args.input_bundle, args.selection_cache, args.group_index, args.output_bundle
    )
    args.output_metrics.parent.mkdir(parents=True, exist_ok=True)
    args.output_metrics.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output_metrics), "group": result["group_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
