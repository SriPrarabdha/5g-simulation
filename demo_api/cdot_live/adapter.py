from __future__ import annotations

import csv
import math
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .config import LiveConfig


RATE_RE = re.compile(r"^([0-9.]+)\s*([kM]?)p/s$")
CLASS_RE = re.compile(r"^upf=(upf-\d+):loc=(\d+):dnn=(\d+):dscp(\d+)$")


def parse_rate(value: str | float | int) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    match = RATE_RE.fullmatch(value.strip())
    if match:
        return float(match.group(1)) * {"": 1.0, "k": 1_000.0, "M": 1_000_000.0}[match.group(2)]
    return float(value)


def quantile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def counter_rates(samples: Iterable[tuple[float, float]], *, max_gap_seconds: float = 90.0) -> list[tuple[float, float]]:
    """Convert ordered counters to rates; a decrease is treated as a reset.

    The first post-reset value is the increment since reset. Non-finite,
    duplicate, backwards and overly separated samples are omitted.
    """
    rows = sorted((float(ts), float(value)) for ts, value in samples)
    result: list[tuple[float, float]] = []
    for (left_ts, left), (right_ts, right) in zip(rows, rows[1:]):
        elapsed = right_ts - left_ts
        if elapsed <= 0 or elapsed > max_gap_seconds or not all(map(math.isfinite, (left, right))):
            continue
        delta = right - left if right >= left else right
        if delta < 0:
            continue
        result.append((right_ts, delta / elapsed))
    return result


def _bucket_epoch(timestamp: float, bucket_seconds: int) -> int:
    return int(timestamp // bucket_seconds) * bucket_seconds


class CdotTelemetryAdapter:
    def __init__(self, config: LiveConfig) -> None:
        self.config = config

    def normalize_labels(self, labels: dict[str, Any]) -> tuple[str, int, str, int] | None:
        metric_upf = str(labels.get("upf") or labels.get("upf_id") or "")
        if metric_upf not in self.config.mappings:
            return None
        try:
            tac = int(labels.get("loc", labels.get("tac")))
            dnn_id = str(labels.get("dnn", labels.get("dnnid")))
            dscp = int(str(labels.get("dscp", "0")).removeprefix("dscp"))
        except (TypeError, ValueError):
            return None
        dnn = self.config.dnn.get(dnn_id)
        if dnn is None:
            return None
        return metric_upf, tac, dnn, dscp

    def aggregate_direction_results(
        self,
        result: Iterable[dict[str, Any]],
        direction: str,
        *,
        now: datetime,
        counters: bool = True,
    ) -> dict[int, dict[tuple[int, str, int, str], dict[str, float]]]:
        if direction not in {"ul", "dl"}:
            raise ValueError("direction must be ul or dl")
        grouped: dict[int, dict[tuple[int, str, int, str], list[float]]] = defaultdict(lambda: defaultdict(list))
        closed_before = now.timestamp() - self.config.watermark_seconds
        for series in result:
            key = self.normalize_labels(dict(series.get("metric", {})))
            if key is None:
                continue
            upf, tac, dnn, dscp = key
            raw = [(float(item[0]), float(item[1])) for item in series.get("values", [])]
            values = counter_rates(raw) if counters else raw
            for timestamp, value in values:
                bucket = _bucket_epoch(timestamp, self.config.bucket_seconds)
                if bucket + self.config.bucket_seconds > closed_before:
                    continue
                if math.isfinite(value) and value >= 0:
                    grouped[bucket][(tac, dnn, dscp, upf)].append(value)
        return {
            bucket: {key: {direction: statistics.fmean(values), f"{direction}_samples": len(values)}
                     for key, values in by_key.items() if values}
            for bucket, by_key in grouped.items()
        }

    def merge_buckets(
        self,
        ul: dict[int, dict[tuple[int, str, int, str], dict[str, float]]],
        dl: dict[int, dict[tuple[int, str, int, str], dict[str, float]]],
        *,
        expected_samples: int | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        expected_keys = {
            key for by_bucket in (ul, dl) for by_key in by_bucket.values() for key in by_key
        }
        for bucket in sorted(set(ul) & set(dl)):
            keys = set(ul[bucket]) | set(dl[bucket])
            tuples = []
            complete = keys == expected_keys
            for key in sorted(expected_keys):
                left, right = ul[bucket].get(key), dl[bucket].get(key)
                if left is None or right is None:
                    complete = False
                    continue
                if expected_samples is not None and (
                    left["ul_samples"] < expected_samples or right["dl_samples"] < expected_samples
                ):
                    complete = False
                    continue
                tac, dnn, dscp, upf = key
                tuples.append({
                    "tuple_id": f"tac-{tac}|{dnn}|dscp-{dscp}|{upf}",
                    "tac": tac, "dnn": dnn, "dscp": dscp, "upf": upf,
                    "ul_rate": round(left["ul"], 6), "dl_rate": round(right["dl"], 6),
                    "unit": self.config.units,
                })
            if complete and tuples:
                rows.append({
                    "start": datetime.fromtimestamp(bucket, timezone.utc).isoformat().replace("+00:00", "Z"),
                    "end": datetime.fromtimestamp(bucket + self.config.bucket_seconds, timezone.utc).isoformat().replace("+00:00", "Z"),
                    "complete": True,
                    "quality": ["closed", "counter_reset_guarded", "pps_proxy", "session_arrivals_unavailable"],
                    "tuples": tuples,
                })
        return rows


def load_v02_replay(root: str | Path, config: LiveConfig, timezone_name: str = "Asia/Kolkata") -> list[dict[str, Any]]:
    """Load the recorded C-DOT class-rate trace into closed causal buckets."""
    root = Path(root)
    zone = ZoneInfo(timezone_name)
    adapter = CdotTelemetryAdapter(config)

    def one(pattern: str) -> tuple[dict[int, dict[tuple[int, str, int, str], dict[str, float]]], float]:
        path = next(root.glob(pattern))
        with path.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.reader(stream))
        header, body = rows[0], rows[1:]
        columns: list[tuple[int, tuple[int, str, int, str]]] = []
        for index, value in enumerate(header[1:], start=1):
            match = CLASS_RE.fullmatch(value)
            if not match:
                continue
            upf, tac, dnn_id, dscp = match.groups()
            dnn = config.dnn.get(dnn_id)
            if upf in config.mappings and dnn:
                columns.append((index, (int(tac), dnn, int(dscp), upf)))
        samples: dict[int, dict[tuple[int, str, int, str], list[float]]] = defaultdict(lambda: defaultdict(list))
        times: list[float] = []
        for row in body:
            parsed = datetime.fromisoformat(row[0]).replace(tzinfo=zone).astimezone(timezone.utc).timestamp()
            times.append(parsed)
            bucket = _bucket_epoch(parsed, config.bucket_seconds)
            for index, key in columns:
                samples[bucket][key].append(parse_rate(row[index]))
        gaps = [right - left for left, right in zip(times, times[1:]) if right > left]
        cadence = statistics.median(gaps)
        direction = "ul" if "Uplink" in pattern else "dl"
        return ({bucket: {key: {direction: statistics.fmean(values), f"{direction}_samples": len(values)}
                           for key, values in by_key.items()}
                 for bucket, by_key in samples.items()}, cadence)

    ul, ul_cadence = one("UPF wise Uplink*.csv")
    dl, dl_cadence = one("UPF wise Downlink*.csv")
    expected = math.floor(config.bucket_seconds / max(ul_cadence, dl_cadence) * 0.9)
    return adapter.merge_buckets(ul, dl, expected_samples=expected)
