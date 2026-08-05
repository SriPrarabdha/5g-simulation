from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from schemas import Quantiles


@dataclass(frozen=True, slots=True)
class QuantileMetrics:
    count: int
    mae_p50: float
    wape_p50: float | None
    pinball_p50: float
    pinball_p90: float | None
    pinball_p95: float
    coverage_p50: float
    coverage_p90: float | None
    coverage_p95: float


def _pinball(actual: float, predicted: float, quantile: float) -> float:
    error = actual - predicted
    return max(quantile * error, (quantile - 1) * error)


def evaluate_quantiles(actual: Iterable[float], predicted: Iterable[Quantiles]) -> QuantileMetrics:
    pairs = list(zip(actual, predicted))
    if not pairs:
        raise ValueError("at least one forecast is required")
    actual_sum = sum(value for value, _ in pairs)
    absolute = [abs(value - forecast.p50) for value, forecast in pairs]
    has_p90 = all(forecast.p90 is not None for _, forecast in pairs)
    count = len(pairs)
    return QuantileMetrics(
        count=count,
        mae_p50=sum(absolute) / count,
        wape_p50=sum(absolute) / actual_sum if actual_sum > 0 else None,
        pinball_p50=sum(_pinball(value, forecast.p50, 0.5) for value, forecast in pairs) / count,
        pinball_p90=(
            sum(_pinball(value, forecast.p90 or 0.0, 0.9) for value, forecast in pairs) / count
            if has_p90 else None
        ),
        pinball_p95=sum(_pinball(value, forecast.p95, 0.95) for value, forecast in pairs) / count,
        coverage_p50=sum(value <= forecast.p50 for value, forecast in pairs) / count,
        coverage_p90=(
            sum(value <= (forecast.p90 or 0.0) for value, forecast in pairs) / count
            if has_p90 else None
        ),
        coverage_p95=sum(value <= forecast.p95 for value, forecast in pairs) / count,
    )
