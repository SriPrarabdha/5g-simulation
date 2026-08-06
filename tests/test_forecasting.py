from __future__ import annotations

import unittest
import importlib.util
from datetime import datetime, timedelta, timezone

from forecasting import (
    DemandObservation,
    ForecastingError,
    LightGBMQuantileForecaster,
    MovingAverageForecaster,
    ResidualObservation,
    SeasonalNaiveForecaster,
    evaluate_quantiles,
)
from schemas import GroupKey, Quantiles, TimeWindow


UTC = timezone.utc
START = datetime(2026, 8, 1, tzinfo=UTC)
GROUP = GroupKey("zone-a", "internet", "1-1")


def history(count: int) -> list[DemandObservation]:
    result = []
    for index in range(count):
        start = START + timedelta(minutes=10 * index)
        result.append(DemandObservation(
            window=TimeWindow(start, start + timedelta(minutes=10)),
            group=GROUP,
            new_session_count=float(index + 1),
            new_ul_mbps=float(2 * (index + 1)),
            new_dl_mbps=float(3 * (index + 1)),
            existing_load_by_upf={"upf-a": ResidualObservation(10 + index, 5 + index, 8 + index)},
        ))
    return result


class ForecastingTests(unittest.TestCase):
    def test_interpolated_equal_quantiles_are_monotonic(self) -> None:
        from forecasting.baselines import _quantiles

        result = _quantiles([54.449999999999996, 55.35, 55.35, 55.35])
        self.assertLessEqual(result.p50, result.p90)
        self.assertLessEqual(result.p90, result.p95)

    def test_moving_average_builds_monotonic_quantiles_and_residuals(self) -> None:
        observations = history(6)
        issued = observations[-1].window.end
        target = TimeWindow(issued, issued + timedelta(minutes=10))
        forecast = MovingAverageForecaster(3).predict(
            observations, issued_at=issued, target_window=target
        )
        self.assertEqual(forecast.new_session_count.p50, 5.0)
        self.assertAlmostEqual(forecast.new_session_count.p90 or 0, 5.8)
        self.assertAlmostEqual(forecast.new_session_count.p95, 5.9)
        self.assertEqual(forecast.existing_load_by_upf[0].upf_id, "upf-a")
        self.assertEqual(forecast.source_window_end, issued)

    def test_seasonal_naive_uses_requested_horizon_without_target_leakage(self) -> None:
        observations = history(8)
        issued = observations[-1].window.end
        target = TimeWindow(issued + timedelta(minutes=10), issued + timedelta(minutes=20))
        forecast = SeasonalNaiveForecaster(3).predict(
            observations, issued_at=issued, target_window=target, horizon_steps=2
        )
        self.assertEqual(forecast.new_session_count.p50, 7.0)
        self.assertEqual(forecast.horizon_steps, 2)

    def test_future_observation_is_rejected_as_leakage(self) -> None:
        observations = history(3)
        issued = observations[-2].window.end
        target = TimeWindow(observations[-1].window.end, observations[-1].window.end + timedelta(minutes=10))
        with self.assertRaisesRegex(ForecastingError, "leaks"):
            MovingAverageForecaster().predict(observations, issued_at=issued, target_window=target)

    def test_quantile_metrics_keep_forecast_accuracy_separate(self) -> None:
        result = evaluate_quantiles([10, 20], [Quantiles(10, 15, 14), Quantiles(18, 25, 22)])
        self.assertEqual(result.count, 2)
        self.assertEqual(result.mae_p50, 1.0)
        self.assertAlmostEqual(result.wape_p50 or 0, 2 / 30)
        self.assertEqual(result.coverage_p95, 1.0)

    @unittest.skipUnless(importlib.util.find_spec("lightgbm"), "LightGBM not installed")
    def test_lightgbm_quantiles_are_deterministic_and_monotonic(self) -> None:
        observations = history(24)
        issued = observations[-1].window.end
        target = TimeWindow(issued, issued + timedelta(minutes=10))
        model = LightGBMQuantileForecaster(
            season_steps=6, rolling_steps=3, n_estimators=10, min_training_rows=8
        )
        first = model.predict(observations, issued_at=issued, target_window=target)
        second = model.predict(observations, issued_at=issued, target_window=target)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertLessEqual(first.new_session_count.p50, first.new_session_count.p90 or 0)
        self.assertLessEqual(first.new_session_count.p90 or 0, first.new_session_count.p95)


if __name__ == "__main__":
    unittest.main()
