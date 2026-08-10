from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from forecasting import TrainedForecastBundle
from forecasting.bundle import TARGET_FIELDS, _sequence_training_rows
from simulator.macro.config import ScenarioConfig, load_scenario

from .train_forecaster import _bucket_sequence


SCHEMA_VERSION = "forecast-regime-evaluation/1.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regimes_by_group_and_bucket(
    manifest: dict[str, Any], config: ScenarioConfig
) -> dict[tuple[str, int], tuple[str, ...]]:
    events_by_step: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in manifest.get("events", []):
        events_by_step[int(event["step"])].append(event)
    surge_windows = manifest.get("corpus", {}).get("pilot_surge_windows", [])
    eligible = {group.key.selection_id: set(group.eligible_upfs) for group in config.groups}
    base_latency = {
        (upf.upf_id, zone): latency
        for upf in config.upfs
        for zone, latency in upf.path_latency_ms_by_zone.items()
    }
    capacity = {upf.upf_id: {"ul": 1.0, "dl": 1.0} for upf in config.upfs}
    health = {upf.upf_id: "healthy" for upf in config.upfs}
    latency = dict(base_latency)
    result: dict[tuple[str, int], tuple[str, ...]] = {}
    interval = config.decision_interval_steps
    for step in range(0, config.steps, interval):
        for event_step in range(max(0, step - interval + 1), step + 1):
            for event in events_by_step.get(event_step, ()):
                kind = event["event_type"]
                upf_id = event.get("upf_id")
                if kind == "capacity_factor" and upf_id:
                    if event.get("ul_factor") is not None:
                        capacity[upf_id]["ul"] = float(event["ul_factor"])
                    if event.get("dl_factor") is not None:
                        capacity[upf_id]["dl"] = float(event["dl_factor"])
                elif kind == "health" and upf_id:
                    health[upf_id] = str(event["health"])
                elif kind == "path_latency" and upf_id:
                    latency[(upf_id, str(event["zone"]))] = float(event["latency_ms"])
        hour = step * config.step_seconds / 3600
        for group in config.groups:
            group_id = group.key.selection_id
            group_upfs = eligible[group_id]
            flags: set[str] = set()
            if any(
                window["start_hour"] <= hour < window["end_hour"]
                and group_id in window["groups"]
                for window in surge_windows
            ):
                flags.add("surge")
            if any(
                health[upf_id] not in {"healthy", "degraded"}
                or min(capacity[upf_id].values()) <= 0.05
                for upf_id in group_upfs
            ):
                flags.add("outage")
            if any(
                0.05 < min(capacity[upf_id].values()) < 0.999999
                for upf_id in group_upfs
            ):
                flags.add("brownout")
            if any(
                latency[(upf_id, group.key.zone)]
                != base_latency[(upf_id, group.key.zone)]
                for upf_id in group_upfs
            ):
                flags.add("latency")
            result[(group_id, step // interval)] = tuple(sorted(flags)) or ("normal",)
    return result


def _summaries(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    categories = sorted({category for row in rows for category in row[key]})
    result: list[dict[str, Any]] = []
    for category in categories:
        selected = [row for row in rows if category in row[key]]
        actual = np.asarray([row["actual"] for row in selected], dtype=float)
        predicted = np.asarray([row["predicted"] for row in selected], dtype=float)
        absolute = np.abs(actual - predicted)
        group_wapes = []
        for group_id in sorted({row["group_id"] for row in selected}):
            grouped = [row for row in selected if row["group_id"] == group_id]
            denominator = sum(abs(row["actual"]) for row in grouped)
            if denominator:
                group_wapes.append(
                    sum(abs(row["actual"] - row["predicted"]) for row in grouped) / denominator
                )
        result.append({
            "regime": category,
            "rows": len(selected),
            "wape": float(np.sum(absolute) / np.sum(np.abs(actual))) if np.sum(np.abs(actual)) else 0.0,
            "macro_group_wape": statistics.fmean(group_wapes) if group_wapes else 0.0,
            "mae": float(np.mean(absolute)) if len(absolute) else 0.0,
            "coverage_p90": statistics.fmean(row["covered_p90"] for row in selected),
            "coverage_p95": statistics.fmean(row["covered_p95"] for row in selected),
        })
    return result


def evaluate(bundle_path: Path, manifest_path: Path, parquet_path: Path) -> dict[str, Any]:
    bundle = TrainedForecastBundle.load(bundle_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = load_scenario(manifest_path)
    bundle.validate_groups(group.key for group in config.groups)
    series = _bucket_sequence(parquet_path, config)
    regimes = _regimes_by_group_and_bucket(manifest, config)
    rows: list[dict[str, Any]] = []
    for group_id, observations in sorted(series.items()):
        for target in TARGET_FIELDS:
            for horizon in (1, 2):
                features, actual = _sequence_training_rows(observations, target, horizon)
                entry = bundle.payload["groups"][group_id]["targets"][target][str(horizon)]
                model = entry["model"]
                predicted = np.maximum(
                    0.0,
                    features @ np.asarray(model["coefficients"], dtype=float)
                    + float(model["median_bias"]),
                )
                p90_width = float(np.interp(
                    0.90, model["calibration_levels"], model["calibration_widths"]
                ))
                p95_width = float(np.interp(
                    0.95, model["calibration_levels"], model["calibration_widths"]
                ))
                for row_index, (actual_value, predicted_value) in enumerate(zip(actual, predicted)):
                    target_bucket = 5 + row_index + horizon
                    flags = regimes[(group_id, target_bucket)]
                    rows.append({
                        "group_id": group_id,
                        "target": target,
                        "horizon_minutes": horizon * 10,
                        "regimes": flags,
                        "exclusive_regime": ("+".join(flags),),
                        "actual": float(actual_value),
                        "predicted": float(predicted_value),
                        "covered_p90": float(actual_value <= predicted_value + p90_width),
                        "covered_p95": float(actual_value <= predicted_value + p95_width),
                    })
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "bundle": {
            "path": str(bundle_path),
            "bundle_sha256": bundle.metadata["bundle_sha256"],
            "file_sha256": _sha256(bundle_path),
        },
        "manifest": {
            "path": str(manifest_path),
            "file_sha256": _sha256(manifest_path),
            "scenario_id": config.scenario_id,
            "seed": config.seed,
        },
        "source_parquet": {"path": str(parquet_path), "file_sha256": _sha256(parquet_path)},
        "contract": {
            "horizons_minutes": [10, 20],
            "targets": list(TARGET_FIELDS),
            "minimum_history_buckets": 6,
            "regimes_are_multilabel": True,
            "normal_means_no_relevant_surge_or_fault_for_the_group": True,
        },
        "by_regime": _summaries(rows, "regimes"),
        "by_exclusive_regime": _summaries(rows, "exclusive_regime"),
        "by_horizon": [
            {
                "horizon_minutes": horizon,
                "by_regime": _summaries(
                    [row for row in rows if row["horizon_minutes"] == horizon], "regimes"
                ),
            }
            for horizon in (10, 20)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a frozen forecaster by event regime")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-parquet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite evaluation: {args.output}")
    result = evaluate(args.bundle, args.manifest, args.run_parquet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "by_regime": result["by_regime"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
