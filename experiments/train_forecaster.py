from __future__ import annotations

import argparse
import json
import resource
import gzip
import pickle
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from forecasting import (
    CalendarRidgeV2Forecaster, CausalRegimeEnsemble, DemandObservation,
    HistGradientBoostingQuantileForecaster, ResidualObservation,
    LightGBMQuantileCandidate,
    TelemetryQualityReplay, causal_observation_metadata, train_forecast_bundle,
    write_candidate_forecast_bundle, write_forecast_bundle,
)
from schemas import TimeWindow
from simulator.macro.config import ScenarioConfig, load_scenario
from experiments.artifacts import validate_published_shard
from experiments.seed_policy import require_forecast_seed


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
    telemetry_replay = TelemetryQualityReplay(config)

    def close_chunk(chunk: list[dict[str, Any]]) -> None:
        if any(
            chunk[index]["step"] + 1 != chunk[index + 1]["step"]
            for index in range(len(chunk) - 1)
        ):
            return
        arrivals: dict[str, int] = defaultdict(int)
        generated_ul: dict[str, float] = defaultdict(float)
        generated_dl: dict[str, float] = defaultdict(float)
        has_generated_labels = all(bool(row.get("group_generated_load_mbps")) for row in chunk)
        telemetry_flags_by_upf: dict[str, set[str]] = defaultdict(set)
        telemetry_age_by_upf: dict[str, float] = defaultdict(float)
        for row in chunk:
            for group_id, count in _group_counts(row["group_arrivals"]).items():
                arrivals[group_id] += count
            for item in row.get("group_generated_load_mbps", ()) or ():
                group_id = str(item["group_id"])
                generated_ul[group_id] += float(item["ul_mbps"])
                generated_dl[group_id] += float(item["dl_mbps"])
            for upf_id, (step_flags, step_age) in telemetry_replay.observe_step(
                int(row["step"])
            ).items():
                # Live control closes the bucket using the latest completed
                # scrape, so offline extraction must not union transient faults
                # from earlier scrapes in the bucket.
                telemetry_flags_by_upf[upf_id] = set(step_flags)
                telemetry_age_by_upf[upf_id] = step_age
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
        bucket_start_step = int(chunk[0]["step"])
        bucket_end_step = int(last["step"]) + 1
        for group in config.groups:
            count = arrivals[group.key.selection_id]
            group_id = group.key.selection_id
            metadata = causal_observation_metadata(
                config, group_id=group_id,
                bucket_start_step=bucket_start_step, bucket_end_step=bucket_end_step,
                arrivals_by_group=arrivals,
                prior_arrivals=[item.new_session_count for item in by_group[group_id]],
                telemetry_flags=tuple(sorted({
                    flag for upf_id in group.eligible_upfs
                    for flag in telemetry_flags_by_upf[upf_id]
                })),
                telemetry_age_seconds=max(
                    (telemetry_age_by_upf[upf_id] for upf_id in group.eligible_upfs),
                    default=0.0,
                ),
            )
            by_group[group.key.selection_id].append(DemandObservation(
                window=window, group=group.key, new_session_count=float(count),
                new_ul_mbps=(
                    generated_ul[group_id]
                    if has_generated_labels
                    else count * group.offered_ul_mbps_per_session
                ),
                new_dl_mbps=(
                    generated_dl[group_id]
                    if has_generated_labels
                    else count * group.offered_dl_mbps_per_session
                ),
                existing_load_by_upf=residual,
                quality_flags=(
                    "synthetic_training",
                    "actual_generated_rate_bin_load" if has_generated_labels
                    else "legacy_nominal_rate_label",
                    *metadata.pop("quality_flags"),
                ),
                **metadata,
            ))

    pending: list[dict[str, Any]] = []
    previous_step: int | None = None
    parquet = pq.ParquetFile(path)
    columns = ["step", "window_end", "group_arrivals", "upfs"]
    if "group_generated_load_mbps" in parquet.schema_arrow.names:
        columns.append("group_generated_load_mbps")
    for batch in parquet.iter_batches(
        batch_size=4096,
        columns=columns,
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
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for metadata_path in sorted(campaign_root.rglob("metadata.json")):
        metadata = validate_published_shard(metadata_path.parent)
        if metadata["retention"]["tier"] not in {"silver", "gold"}:
            continue
        detailed = next(
            (item for item in metadata["artifacts"] if item["kind"] == "detailed_steps"),
            None,
        )
        if detailed is not None:
            candidates.append((metadata_path.parent / detailed["path"], metadata))
    selected = [path for path, metadata in candidates if metadata["controller"] == controller]
    if not selected:
        selected = [path for path, _ in candidates]
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
    parser.add_argument("--campaign-root", type=Path)
    parser.add_argument("--series-cache", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controller", default="static-capacity-v1")
    parser.add_argument("--model-version", default="calendar-ridge-conformal/1.0")
    parser.add_argument(
        "--family",
        choices=("calendar-ridge", "ridge-v2", "hist-gradient-quantile", "regime-ensemble", "lightgbm-quantile"),
        default="calendar-ridge",
    )
    parser.add_argument("--group-index", type=int)
    parser.add_argument("--horizons", default="1,2,3,8")
    parser.add_argument("--hist-max-iter", type=int, default=150)
    args = parser.parse_args()
    started = time.monotonic()
    if (args.campaign_root is None) == (args.series_cache is None):
        parser.error("exactly one of --campaign-root or --series-cache is required")
    _log(f"phase=load status=started controller={args.controller}")
    config = load_scenario(args.manifest)
    require_forecast_seed(config.seed, "train")
    if args.series_cache is not None:
        cache = json.loads((args.series_cache / "index.json").read_text(encoding="utf-8"))
        if cache.get("schema_version") != "forecast-series-cache/1.0" or cache.get("purpose") != "train":
            parser.error("--series-cache must be a training cache")
        if int(cache["seed"]) != config.seed:
            parser.error("series cache seed does not match manifest")
        if args.group_index is None:
            parser.error("candidate training from --series-cache requires --group-index")
        try:
            entry = cache["groups"][args.group_index]
        except IndexError:
            parser.error("--group-index falls outside the series cache")
        with gzip.open(args.series_cache / entry["path"], "rb") as stream:
            sequences = pickle.load(stream)
        series = {entry["group_id"]: sequences}
    else:
        assert args.campaign_root is not None
        series = collect_training_series(args.campaign_root, config, controller=args.controller)
        if args.group_index is not None:
            group_ids = sorted(series)
            if not 0 <= args.group_index < len(group_ids):
                parser.error(f"--group-index must be in [0, {len(group_ids) - 1}]")
            selected_group = group_ids[args.group_index]
            series = {selected_group: series[selected_group]}
    observations = sum(len(sequence) for sequences in series.values() for sequence in sequences)
    peak_rss_gib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
    _log(
        f"phase=load status=complete groups={len(series)} observations={observations} "
        f"elapsed={_duration(time.monotonic() - started)} peak_rss_gib={peak_rss_gib:.2f}"
    )
    training_started = time.monotonic()
    _log(f"phase=train status=started family={args.family} targets=3")

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

    source = {
        "campaign_root": str(args.campaign_root.resolve()) if args.campaign_root else None,
        "series_cache": str(args.series_cache.resolve()) if args.series_cache else None,
        "manifest": str(args.manifest.resolve()),
        "manifest_seed": config.seed,
        "controller_filter": args.controller,
        "synthetic": True,
        "sequence_count": sum(len(items) for items in series.values()),
        "causal_metadata": "causal_metadata_v1",
    }
    if args.family == "calendar-ridge":
        if args.group_index is not None:
            parser.error("calendar-ridge produces one legacy bundle and does not support --group-index")
        payload = train_forecast_bundle(
            series, model_version=args.model_version, source=source,
            progress_callback=training_progress,
        )
    else:
        horizons = tuple(int(item) for item in args.horizons.split(",") if item)
        if not horizons or any(item not in {1, 2, 3, 8} for item in horizons):
            parser.error("--horizons must select from 1,2,3,8")
        version = args.model_version
        if version == "calendar-ridge-conformal/1.0":
            version = f"{args.family}/control-science-v1"
        if args.family == "ridge-v2":
            model = CalendarRidgeV2Forecaster(version, horizons=horizons).fit(series)
        elif args.family == "hist-gradient-quantile":
            model = HistGradientBoostingQuantileForecaster(
                version, horizons=horizons, max_iter=args.hist_max_iter
            ).fit(series)
        elif args.family == "regime-ensemble":
            normal = CalendarRidgeV2Forecaster(version, horizons=horizons).fit(series)
            model = CausalRegimeEnsemble(normal)
        else:
            model = LightGBMQuantileCandidate(version, horizons=horizons).fit(series)
        payload = write_candidate_forecast_bundle(args.output, model, source=source)
    _log(f"phase=train status=complete elapsed={_duration(time.monotonic() - training_started)}")
    _log(f"phase=publish status=started output={args.output}")
    if args.family == "calendar-ridge":
        write_forecast_bundle(args.output, payload)
    _log(
        f"phase=complete status=published elapsed={_duration(time.monotonic() - started)} "
        f"output={args.output} sha256={payload['bundle_sha256']} "
        f"family={args.family}"
    )
    print(json.dumps({
        "output": str(args.output), "model_version": payload["model_version"],
        "sha256": payload["bundle_sha256"], "groups": len(payload["groups"]),
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
