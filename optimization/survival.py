"""Causal session-survival estimates for cohort planning.

The controller consumes this small, distribution-agnostic contract.  Curves can
come from simulator truth, censored lifecycle records, a pooled service class,
or a conservative static prior without changing the optimizer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

import numpy as np


@dataclass(frozen=True, slots=True)
class SessionLifecycle:
    """One completed or right-censored lifecycle observation."""

    group_id: str
    duration_steps: int
    completed: bool = True
    service_class: str | None = None

    def __post_init__(self) -> None:
        if not self.group_id or self.duration_steps < 1:
            raise ValueError("lifecycle observations require a group and positive duration")


@dataclass(frozen=True, slots=True)
class SurvivalTable:
    """Expected active fraction at successive decision-bucket lags."""

    probabilities: tuple[float, ...]
    source: str
    generated_at: datetime
    sample_count: int
    confidence: str
    upper_confidence: bool = False
    stale: bool = False

    def __post_init__(self) -> None:
        if not self.probabilities:
            raise ValueError("a survival table cannot be empty")
        if self.sample_count < 0:
            raise ValueError("survival sample_count must be non-negative")
        if self.confidence not in {"low", "medium", "high", "oracle"}:
            raise ValueError("unsupported survival confidence")
        previous = 1.0
        for probability in self.probabilities:
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("survival probabilities must be finite and in [0, 1]")
            if probability > previous + 1e-12:
                raise ValueError("survival probabilities must be non-increasing")
            previous = probability

    def values(self, count: int) -> np.ndarray:
        if count < 1:
            raise ValueError("survival horizon must be positive")
        values = list(self.probabilities[:count])
        values.extend([values[-1]] * (count - len(values)))
        return np.asarray(values, dtype=float)

    def audit(self, *, now: datetime | None = None) -> dict[str, object]:
        current = now or datetime.now(timezone.utc)
        generated = self.generated_at
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        return {
            "source": self.source,
            "age_seconds": max(0.0, (current - generated).total_seconds()),
            "sample_count": self.sample_count,
            "confidence": self.confidence,
            "upper_confidence": self.upper_confidence,
            "stale": self.stale,
        }


def kaplan_meier_table(
    observations: Iterable[SessionLifecycle],
    *,
    bucket_steps: int,
    bucket_count: int,
    source: str = "empirical-kaplan-meier",
    generated_at: datetime | None = None,
    conservative_upper: bool = True,
    confidence_z: float = 1.645,
) -> SurvivalTable:
    """Estimate bucket survival while retaining right-censored observations.

    A Greenwood log-log interval would be needlessly fragile for the tiny
    fallback samples that matter operationally.  The one-sided Greenwood-style
    normal bound below is monotonicized and deliberately conservative.
    """

    rows = tuple(observations)
    if bucket_steps < 1 or bucket_count < 1:
        raise ValueError("bucket_steps and bucket_count must be positive")
    if not rows:
        raise ValueError("Kaplan-Meier estimation needs lifecycle observations")
    event_by_time: dict[int, int] = {}
    censored_by_time: dict[int, int] = {}
    for row in rows:
        target = event_by_time if row.completed else censored_by_time
        target[row.duration_steps] = target.get(row.duration_steps, 0) + 1
    at_risk = len(rows)
    survival = 1.0
    greenwood = 0.0
    curve: dict[int, tuple[float, float]] = {0: (1.0, 0.0)}
    for step in sorted(set(event_by_time) | set(censored_by_time)):
        events = event_by_time.get(step, 0)
        if events and at_risk:
            survival *= max(0.0, 1.0 - events / at_risk)
            if at_risk > events:
                greenwood += events / (at_risk * (at_risk - events))
        standard_error = survival * math.sqrt(max(0.0, greenwood))
        curve[step] = (survival, standard_error)
        at_risk -= events + censored_by_time.get(step, 0)

    event_times = sorted(curve)
    probabilities: list[float] = []
    for lag in range(bucket_count):
        # The lag-zero cohort is active during its admission bucket.  Later
        # lags query survival at the start of that bucket.
        step = lag * bucket_steps
        eligible = [item for item in event_times if item <= step]
        estimate, error = curve[max(eligible)] if eligible else (1.0, 0.0)
        probabilities.append(min(1.0, estimate + confidence_z * error) if conservative_upper else estimate)
    probabilities = np.minimum.accumulate(np.asarray(probabilities, dtype=float)).tolist()
    count = len(rows)
    confidence = "high" if count >= 10_000 else "medium" if count >= 1_000 else "low"
    return SurvivalTable(
        tuple(float(item) for item in probabilities), source,
        generated_at or datetime.now(timezone.utc), count, confidence,
        conservative_upper, False,
    )


def static_survival_table(
    *,
    bucket_count: int,
    persistence: float = 0.90,
    generated_at: datetime | None = None,
) -> SurvivalTable:
    """Conservative bounded prior when lifecycle telemetry is unavailable."""

    if bucket_count < 1 or not 0 < persistence <= 1:
        raise ValueError("invalid static survival prior")
    probabilities = tuple(persistence ** lag for lag in range(bucket_count))
    return SurvivalTable(
        probabilities, "static-conservative-prior",
        generated_at or datetime.now(timezone.utc), 0, "low", True, False,
    )


class EmpiricalSurvivalProvider:
    """Build group curves, shrink sparse groups to pools, and mark stale data."""

    def __init__(
        self,
        observations: Iterable[SessionLifecycle],
        *,
        bucket_steps: int,
        bucket_count: int,
        minimum_group_samples: int = 100,
        stale_after_seconds: float = 172_800,
        generated_at: datetime | None = None,
    ) -> None:
        if minimum_group_samples < 1 or stale_after_seconds <= 0:
            raise ValueError("invalid survival-provider thresholds")
        self.rows = tuple(observations)
        self.bucket_steps = bucket_steps
        self.bucket_count = bucket_count
        self.minimum_group_samples = minimum_group_samples
        self.stale_after_seconds = stale_after_seconds
        self.generated_at = generated_at or datetime.now(timezone.utc)

    def tables(
        self,
        group_to_service_class: Mapping[str, str],
        *,
        now: datetime | None = None,
    ) -> dict[str, SurvivalTable]:
        current = now or datetime.now(timezone.utc)
        stale = (current - self.generated_at).total_seconds() > self.stale_after_seconds
        by_group: dict[str, list[SessionLifecycle]] = {}
        by_class: dict[str, list[SessionLifecycle]] = {}
        for row in self.rows:
            by_group.setdefault(row.group_id, []).append(row)
            service_class = row.service_class or group_to_service_class.get(row.group_id)
            if service_class is not None:
                by_class.setdefault(service_class, []).append(row)
        result: dict[str, SurvivalTable] = {}
        for group_id, service_class in group_to_service_class.items():
            group_rows = by_group.get(group_id, [])
            source = "empirical-kaplan-meier"
            selected: Sequence[SessionLifecycle] = group_rows
            if len(group_rows) < self.minimum_group_samples:
                selected = by_class.get(service_class, [])
                source = "pooled-service-class-kaplan-meier"
            if not selected:
                table = static_survival_table(
                    bucket_count=self.bucket_count, generated_at=self.generated_at
                )
            else:
                table = kaplan_meier_table(
                    selected, bucket_steps=self.bucket_steps,
                    bucket_count=self.bucket_count, source=source,
                    generated_at=self.generated_at, conservative_upper=True,
                )
            if stale:
                table = SurvivalTable(
                    table.probabilities, table.source, table.generated_at,
                    table.sample_count, "low", table.upper_confidence, True,
                )
            result[group_id] = table
        return result
