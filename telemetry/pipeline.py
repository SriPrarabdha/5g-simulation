from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from schemas import TelemetrySample, TimeWindow
from schemas.common import parse_utc


@dataclass(frozen=True, order=True, slots=True)
class SeriesKey:
    """Bounded counter identity; dimensions must match across a valid delta."""

    source_type: str
    source_id: str
    metric: str
    unit: str
    upf_id: str | None
    zone: str | None
    dnn: str | None
    snssai: str | None
    five_qi: int | None
    site: str | None
    interface: str | None
    direction: str | None

    @classmethod
    def from_sample(cls, sample: TelemetrySample) -> "SeriesKey":
        return cls(
            source_type=sample.source_type,
            source_id=sample.source_id,
            metric=sample.metric,
            unit=sample.unit,
            upf_id=sample.upf_id,
            zone=sample.zone,
            dnn=sample.dnn,
            snssai=sample.snssai,
            five_qi=sample.five_qi,
            site=sample.site,
            interface=sample.interface,
            direction=sample.direction,
        )


@dataclass(frozen=True, slots=True)
class CounterInterval:
    series: SeriesKey
    start: datetime
    end: datetime
    delta: float | None
    rate_per_second: float | None
    valid: bool
    flags: tuple[str, ...] = ()
    reset: bool = False
    restart: bool = False
    late: bool = False

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass(frozen=True, slots=True)
class MetricBucket:
    series: SeriesKey
    window: TimeWindow
    total: float | None
    mean_rate_per_second: float | None
    p95_rate_per_second: float | None
    max_rate_per_second: float | None
    covered_duration_seconds: float
    expected_duration_seconds: float
    missing_fraction: float
    reset_count: int
    restart_count: int
    restarted: bool
    late_sample_count: int
    validity_flags: tuple[str, ...] = field(default_factory=tuple)


def _deduplicate(samples: Iterable[TelemetrySample]) -> list[TelemetrySample]:
    """Deduplicate IDs and event-time collisions deterministically.

    The earliest received copy wins for a duplicate sample ID. For two distinct
    samples at the same source event time, the last received sample wins; that
    mirrors an exporter correction while making the choice reproducible.
    """

    by_id: dict[str, TelemetrySample] = {}
    for sample in samples:
        current = by_id.get(sample.sample_id)
        if current is None or (sample.received_time, sample.sample_id) < (
            current.received_time,
            current.sample_id,
        ):
            by_id[sample.sample_id] = sample

    by_event: dict[tuple[SeriesKey, datetime], TelemetrySample] = {}
    for sample in by_id.values():
        key = (SeriesKey.from_sample(sample), sample.event_time)
        current = by_event.get(key)
        if current is None or (sample.received_time, sample.sample_id) > (
            current.received_time,
            current.sample_id,
        ):
            by_event[key] = sample
    return sorted(
        by_event.values(),
        key=lambda item: (repr(SeriesKey.from_sample(item)), item.event_time, item.sample_id),
    )


def reconstruct_counter_intervals(
    samples: Iterable[TelemetrySample],
    *,
    expected_scrape_seconds: float = 30.0,
    max_gap_factor: float = 1.5,
    watermark: datetime | None = None,
) -> list[CounterInterval]:
    """Reconstruct counter rates without crossing gaps, resets, or restarts."""

    if expected_scrape_seconds <= 0:
        raise ValueError("expected_scrape_seconds must be positive")
    if max_gap_factor < 1:
        raise ValueError("max_gap_factor must be at least one")
    cutoff = parse_utc(watermark) if watermark is not None else None
    grouped: dict[SeriesKey, list[TelemetrySample]] = defaultdict(list)
    for sample in _deduplicate(samples):
        if not sample.is_counter:
            continue
        grouped[SeriesKey.from_sample(sample)].append(sample)

    result: list[CounterInterval] = []
    max_gap = expected_scrape_seconds * max_gap_factor
    for series, ordered in sorted(grouped.items(), key=lambda item: repr(item[0])):
        for previous, current in zip(ordered, ordered[1:]):
            flags: list[str] = []
            elapsed = (current.event_time - previous.event_time).total_seconds()
            reset = previous.reset_epoch != current.reset_epoch
            restart = previous.restart_id != current.restart_id
            late = cutoff is not None and current.received_time > cutoff
            if elapsed <= 0:
                flags.append("invalid_elapsed")
            if elapsed > max_gap:
                flags.append("missing_scrape")
            if reset:
                flags.append("counter_reset")
            if restart:
                flags.append("source_restart")
            if not previous.valid or not current.valid:
                flags.append("invalid_sample")
            if previous.value is None or current.value is None:
                flags.append("missing_value")
                delta = None
            else:
                delta = current.value - previous.value
                if delta < 0:
                    flags.append("negative_delta")
            if late:
                flags.append("late_sample")

            valid = not flags
            rate = delta / elapsed if valid and delta is not None else None
            result.append(CounterInterval(
                series=series,
                start=previous.event_time,
                end=current.event_time,
                delta=delta if valid else None,
                rate_per_second=rate,
                valid=valid,
                flags=tuple(flags),
                reset=reset,
                restart=restart,
                late=late,
            ))
    return result


def _weighted_quantile(items: list[tuple[float, float]], quantile: float) -> float | None:
    if not items:
        return None
    ordered = sorted(items, key=lambda item: item[0])
    total_weight = sum(weight for _, weight in ordered)
    threshold = total_weight * quantile
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def aggregate_counter_buckets(
    samples: Iterable[TelemetrySample],
    windows: Iterable[TimeWindow],
    *,
    expected_scrape_seconds: float = 30.0,
    max_gap_factor: float = 1.5,
    watermark: datetime | None = None,
) -> list[MetricBucket]:
    """Assign reconstructed rates to half-open event-time buckets by duration."""

    windows = sorted(windows, key=lambda item: item.start)
    intervals = reconstruct_counter_intervals(
        samples,
        expected_scrape_seconds=expected_scrape_seconds,
        max_gap_factor=max_gap_factor,
        watermark=watermark,
    )
    series_set = sorted({interval.series for interval in intervals}, key=repr)
    buckets: list[MetricBucket] = []
    for series in series_set:
        series_intervals = [item for item in intervals if item.series == series]
        for window in windows:
            expected = (window.end - window.start).total_seconds()
            covered = 0.0
            total = 0.0
            weighted_rates: list[tuple[float, float]] = []
            reset_count = 0
            restart_count = 0
            late_ids: set[datetime] = set()
            flags: set[str] = set()
            for interval in series_intervals:
                overlap_start = max(window.start, interval.start)
                overlap_end = min(window.end, interval.end)
                overlap = (overlap_end - overlap_start).total_seconds()
                if overlap <= 0:
                    continue
                flags.update(interval.flags)
                reset_count += int(interval.reset)
                restart_count += int(interval.restart)
                if interval.late:
                    late_ids.add(interval.end)
                if interval.valid and interval.rate_per_second is not None:
                    covered += overlap
                    total += interval.rate_per_second * overlap
                    weighted_rates.append((interval.rate_per_second, overlap))
            mean = total / covered if covered else None
            maximum = max((value for value, _ in weighted_rates), default=None)
            missing = max(0.0, min(1.0, 1.0 - covered / expected))
            if missing > 0:
                flags.add("incomplete_coverage")
            buckets.append(MetricBucket(
                series=series,
                window=window,
                total=total if covered else None,
                mean_rate_per_second=mean,
                p95_rate_per_second=_weighted_quantile(weighted_rates, 0.95),
                max_rate_per_second=maximum,
                covered_duration_seconds=covered,
                expected_duration_seconds=expected,
                missing_fraction=missing,
                reset_count=reset_count,
                restart_count=restart_count,
                restarted=restart_count > 0,
                late_sample_count=len(late_ids),
                validity_flags=tuple(sorted(flags)),
            ))
    return buckets
