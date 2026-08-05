from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from forecasting import DemandObservation, ResidualObservation, train_forecast_bundle, write_forecast_bundle
from schemas import TimeWindow
from simulator.macro.config import ScenarioConfig, load_scenario


def _group_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {str(item["group_id"]): int(item["count"]) for item in items}


def _bucket_sequence(path: Path, config: ScenarioConfig) -> dict[str, list[DemandObservation]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("PyArrow is required to train from campaign Parquet") from error
    rows = pq.read_table(path).to_pylist()
    rows.sort(key=lambda row: row["step"])
    interval = config.decision_interval_steps
    duration = config.step_seconds * interval
    by_group: dict[str, list[DemandObservation]] = defaultdict(list)
    for offset in range(0, len(rows) - interval + 1, interval):
        chunk = rows[offset:offset + interval]
        if any(chunk[index]["step"] + 1 != chunk[index + 1]["step"] for index in range(len(chunk) - 1)):
            continue
        arrivals: dict[str, int] = defaultdict(int)
        for row in chunk:
            for group_id, count in _group_counts(row["group_arrivals"]).items():
                arrivals[group_id] += count
        last = chunk[-1]
        residual = {
            str(item["upf_id"]): ResidualObservation(
                float(item["active_sessions"]),
                float(item["ul"]["offered_bytes"]) * 8 / config.step_seconds / 1_000_000,
                float(item["dl"]["offered_bytes"]) * 8 / config.step_seconds / 1_000_000,
            )
            for item in last["upfs"]
        }
        window = TimeWindow(last["window_end"] - timedelta(seconds=duration), last["window_end"])
        for group in config.groups:
            count = arrivals[group.key.selection_id]
            by_group[group.key.selection_id].append(DemandObservation(
                window=window, group=group.key, new_session_count=float(count),
                new_ul_mbps=count * group.offered_ul_mbps_per_session,
                new_dl_mbps=count * group.offered_dl_mbps_per_session,
                existing_load_by_upf=residual,
                quality_flags=("synthetic_training",),
            ))
    return by_group


def collect_training_series(
    campaign_root: Path,
    config: ScenarioConfig,
    *,
    controller: str,
) -> dict[str, list[list[DemandObservation]]]:
    result: dict[str, list[list[DemandObservation]]] = defaultdict(list)
    candidates = sorted(campaign_root.rglob("run.parquet"))
    selected = [path for path in candidates if f"controller={controller}" in str(path)]
    if not selected:
        selected = candidates
    for path in selected:
        sequence = _bucket_sequence(path, config)
        for group_id, observations in sequence.items():
            if observations:
                result[group_id].append(observations)
    if not result:
        raise ValueError(f"no readable run.parquet shards under {campaign_root}")
    return dict(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train and freeze the offline 10–80 minute forecast bundle")
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controller", default="static-capacity-v1")
    parser.add_argument("--model-version", default="calendar-ridge-conformal/1.0")
    args = parser.parse_args()
    config = load_scenario(args.manifest)
    series = collect_training_series(args.campaign_root, config, controller=args.controller)
    payload = train_forecast_bundle(
        series,
        model_version=args.model_version,
        source={
            "campaign_root": str(args.campaign_root.resolve()),
            "manifest": str(args.manifest.resolve()),
            "controller_filter": args.controller,
            "synthetic": True,
            "sequence_count": sum(len(items) for items in series.values()),
        },
    )
    write_forecast_bundle(args.output, payload)
    print(json.dumps({
        "output": str(args.output), "model_version": payload["model_version"],
        "sha256": payload["bundle_sha256"], "groups": len(payload["groups"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
