from __future__ import annotations

import argparse
import json
import resource
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from forecasting import DemandObservation, ResidualObservation, train_forecast_bundle, write_forecast_bundle
from schemas import TimeWindow
from simulator.macro.config import ScenarioConfig, load_scenario


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    print(f"[{timestamp}] {message}", flush=True)


def _group_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {str(item["group_id"]): int(item["count"]) for item in items}


def _bucket_sequence(path: Path, config: ScenarioConfig) -> dict[str, list[DemandObservation]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("PyArrow is required to train from campaign Parquet") from error
    interval = config.decision_interval_steps
    duration = config.step_seconds * interval
    by_group: dict[str, list[DemandObservation]] = defaultdict(list)

    def close_chunk(chunk: list[dict[str, Any]]) -> None:
        if any(
            chunk[index]["step"] + 1 != chunk[index + 1]["step"]
            for index in range(len(chunk) - 1)
        ):
            return
        arrivals: dict[str, int] = defaultdict(int)
        for row in chunk:
            for group_id, count in _group_counts(row["group_arrivals"]).items():
                arrivals[group_id] += count
        # These are duration means across the complete decision bucket, not a
        # single sample from its final 30-second tick.  That matches the live
        # telemetry contract and prevents one noisy scrape from becoming the
        # residual-load feature for every traffic group.
        upf_totals: dict[str, dict[str, float]] = defaultdict(
            lambda: {"active_sessions": 0.0, "ul_bytes": 0.0, "dl_bytes": 0.0, "samples": 0.0}
        )
        for row in chunk:
            for item in row["upfs"]:
                totals = upf_totals[str(item["upf_id"])]
                totals["active_sessions"] += float(item["active_sessions"])
                totals["ul_bytes"] += float(item["ul"]["offered_bytes"])
                totals["dl_bytes"] += float(item["dl"]["offered_bytes"])
                totals["samples"] += 1.0
        bucket_seconds = config.step_seconds * len(chunk)
        residual = {
            upf_id: ResidualObservation(
                totals["active_sessions"] / totals["samples"],
                totals["ul_bytes"] * 8 / bucket_seconds / 1_000_000,
                totals["dl_bytes"] * 8 / bucket_seconds / 1_000_000,
            )
            for upf_id, totals in upf_totals.items()
        }
        last = chunk[-1]
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

    pending: list[dict[str, Any]] = []
    previous_step: int | None = None
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(
        batch_size=4096,
        columns=["step", "window_end", "group_arrivals", "upfs"],
    ):
        for row in batch.to_pylist():
            step = int(row["step"])
            if previous_step is not None and step <= previous_step:
                raise ValueError(f"run.parquet steps are not strictly ordered: {path}")
            previous_step = step
            pending.append(row)
            if len(pending) == interval:
                close_chunk(pending)
                pending = []
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
    started = time.monotonic()
    _log(
        f"phase=load status=started campaign_root={args.campaign_root} "
        f"controller={args.controller}"
    )
    config = load_scenario(args.manifest)
    series = collect_training_series(args.campaign_root, config, controller=args.controller)
    observations = sum(len(sequence) for sequences in series.values() for sequence in sequences)
    peak_rss_gib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
    _log(
        f"phase=load status=complete groups={len(series)} observations={observations} "
        f"elapsed={_duration(time.monotonic() - started)} peak_rss_gib={peak_rss_gib:.2f}"
    )
    training_started = time.monotonic()
    _log("phase=train status=started targets=3 horizons=8")

    def training_progress(completed: int, total: int, group_id: str) -> None:
        elapsed = time.monotonic() - training_started
        rate = completed / elapsed if elapsed > 0 else 0.0
        eta = (total - completed) / rate if rate > 0 else None
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
        _log(
            f"phase=train status=running progress={completed / total * 100:.2f}% "
            f"group={completed}/{total} group_id={group_id} elapsed={_duration(elapsed)} "
            f"eta={_duration(eta)} peak_rss_gib={peak:.2f}"
        )

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
        progress_callback=training_progress,
    )
    _log(f"phase=train status=complete elapsed={_duration(time.monotonic() - training_started)}")
    _log(f"phase=publish status=started output={args.output}")
    write_forecast_bundle(args.output, payload)
    _log(
        f"phase=complete status=published elapsed={_duration(time.monotonic() - started)} "
        f"output={args.output} sha256={payload['bundle_sha256']} "
        f"mean_test_wape_pct={payload['summary_metrics']['mean_test_wape_p50'] * 100:.3f}"
    )
    print(json.dumps({
        "output": str(args.output), "model_version": payload["model_version"],
        "sha256": payload["bundle_sha256"], "groups": len(payload["groups"]),
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
