"""Regime- and horizon-aware forecast release metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from schemas import Quantiles
from schemas.common import parse_utc


@dataclass(frozen=True, slots=True)
class ForecastEvaluationRecord:
    actual: float
    forecast: Quantiles
    target_time: datetime
    horizon_minutes: int
    regime: str

    def __post_init__(self) -> None:
        if self.actual < 0 or self.horizon_minutes < 1:
            raise ValueError("forecast evaluation records require non-negative actuals and a horizon")


def _metrics(records: list[ForecastEvaluationRecord], *, surge_threshold: float) -> dict[str, float | None]:
    if not records:
        return {"count": 0}
    absolute = [abs(row.actual - row.forecast.p50) for row in records]
    actual_sum = sum(row.actual for row in records)
    peak_actual = max(records, key=lambda row: (row.actual, -parse_utc(row.target_time).timestamp()))
    peak_forecast = max(records, key=lambda row: (row.forecast.p50, -parse_utc(row.target_time).timestamp()))
    actual_surges = [row for row in records if row.actual >= surge_threshold]
    normal = [row for row in records if row.actual < surge_threshold]
    detected = lambda row: (row.forecast.p90 if row.forecast.p90 is not None else row.forecast.p95) >= surge_threshold
    p90_widths = [
        (row.forecast.p90 if row.forecast.p90 is not None else row.forecast.p95) - row.forecast.p50
        for row in records
    ]
    return {
        "count": len(records),
        "mae": sum(absolute) / len(records),
        "wape": sum(absolute) / actual_sum if actual_sum else None,
        "peak_underprediction": max(0.0, peak_actual.actual - peak_actual.forecast.p50),
        "peak_timing_error_minutes": abs(
            (parse_utc(peak_forecast.target_time) - parse_utc(peak_actual.target_time)).total_seconds()
        ) / 60,
        "surge_recall": sum(detected(row) for row in actual_surges) / len(actual_surges) if actual_surges else None,
        "false_alarm_rate": sum(detected(row) for row in normal) / len(normal) if normal else None,
        "coverage_p90": sum(
            row.actual <= (row.forecast.p90 if row.forecast.p90 is not None else row.forecast.p95)
            for row in records
        ) / len(records),
        "coverage_p95": sum(row.actual <= row.forecast.p95 for row in records) / len(records),
        "mean_interval_width_p90": sum(p90_widths) / len(records),
        "mean_interval_width_p95": sum(row.forecast.p95 - row.forecast.p50 for row in records) / len(records),
    }


def evaluate_forecast_records(
    records: Iterable[ForecastEvaluationRecord], *, surge_threshold: float,
) -> dict[str, object]:
    rows = list(records)
    if not rows or surge_threshold <= 0:
        raise ValueError("evaluation needs records and a positive surge threshold")
    horizons = sorted({row.horizon_minutes for row in rows})
    regimes = sorted({row.regime for row in rows})
    return {
        "schema_version": "forecast-regime-evaluation/1.0",
        "overall": _metrics(rows, surge_threshold=surge_threshold),
        "by_horizon": {
            str(horizon): _metrics(
                [row for row in rows if row.horizon_minutes == horizon],
                surge_threshold=surge_threshold,
            )
            for horizon in horizons
        },
        "by_regime": {
            regime: _metrics(
                [row for row in rows if row.regime == regime],
                surge_threshold=surge_threshold,
            )
            for regime in regimes
        },
    }


def forecast_promotion_gates(
    evaluation: dict[str, object], *,
    baseline_peak_underprediction: float,
    horizon_wape_baseline: dict[str, float],
    closed_loop_improved: bool,
) -> dict[str, bool]:
    overall = evaluation["overall"]
    assert isinstance(overall, dict)
    by_horizon = evaluation["by_horizon"]
    assert isinstance(by_horizon, dict)
    coverage = float(overall["coverage_p90"])
    gates = {
        "overall_wape_at_most_8_70_percent": float(overall["wape"] or 0.0) <= 0.087,
        "peak_underprediction_reduced_20_percent": float(overall["peak_underprediction"]) <= 0.8 * baseline_peak_underprediction,
        "p90_coverage_between_88_and_95_percent": 0.88 <= coverage <= 0.95,
        "no_horizon_wape_regression_over_5_percent": all(
            float(metrics["wape"] or 0.0) <= horizon_wape_baseline[horizon] * 1.05
            for horizon, metrics in by_horizon.items()
        ),
        "closed_loop_mpc_improved": closed_loop_improved,
    }
    return gates
