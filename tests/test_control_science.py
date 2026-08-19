from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from forecasting import (
    CAUSAL_FEATURE_NAMES,
    CalendarRidgeV2Forecaster,
    DemandObservation,
    ForecastingError,
    causal_features,
)
from optimization import EmpiricalSurvivalProvider, SessionLifecycle, kaplan_meier_table
from schemas import GroupKey, TimeWindow


class SurvivalProviderTests(unittest.TestCase):
    def test_kaplan_meier_uses_censored_sessions_without_counting_them_as_releases(self) -> None:
        at = datetime(2026, 8, 19, tzinfo=timezone.utc)
        table = kaplan_meier_table(
            [
                SessionLifecycle("g", 2, True),
                SessionLifecycle("g", 4, False),
                SessionLifecycle("g", 5, True),
            ],
            bucket_steps=1, bucket_count=6, generated_at=at,
            conservative_upper=False,
        )
        self.assertEqual(table.probabilities[:2], (1.0, 1.0))
        self.assertAlmostEqual(table.probabilities[2], 2 / 3)
        self.assertAlmostEqual(table.probabilities[4], 2 / 3)
        self.assertEqual(table.probabilities[5], 0.0)

    def test_sparse_group_shrinks_to_service_class_and_stale_curve_is_marked(self) -> None:
        at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        rows = [SessionLifecycle("g-a", 3, True, "voice") for _ in range(10)]
        provider = EmpiricalSurvivalProvider(
            rows, bucket_steps=1, bucket_count=4, minimum_group_samples=100,
            stale_after_seconds=60, generated_at=at,
        )
        table = provider.tables(
            {"g-a": "voice", "g-b": "voice"}, now=at + timedelta(minutes=2)
        )["g-b"]
        self.assertEqual(table.source, "pooled-service-class-kaplan-meier")
        self.assertTrue(table.stale)
        self.assertEqual(table.confidence, "low")


class CausalCandidateTests(unittest.TestCase):
    def _series(self, count: int = 1040) -> list[DemandObservation]:
        group = GroupKey("zone", "internet", "1-1", 9)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = []
        for index in range(count):
            value = 100 + 15 * np.sin(2 * np.pi * index / 144) + index * 0.01
            window = TimeWindow(
                start + timedelta(minutes=10 * index),
                start + timedelta(minutes=10 * (index + 1)),
            )
            result.append(DemandObservation(window, group, value, value * .3, value * 2))
        return result

    def test_rich_feature_schema_is_finite_and_fixed(self) -> None:
        rows = self._series()
        features = causal_features(rows, "new_session_count", len(rows) - 1, rows[-1].window.end)
        self.assertEqual(len(features), len(CAUSAL_FEATURE_NAMES))
        self.assertTrue(np.all(np.isfinite(features)))

    def test_future_event_availability_is_rejected(self) -> None:
        rows = self._series()
        last = rows[-1]
        with self.assertRaises(ForecastingError):
            DemandObservation(
                last.window, last.group, 1, 1, 1,
                event_features={"known_event_phase": 1.0},
                available_at={"known_event_phase": last.window.end + timedelta(seconds=1)},
            )

    def test_ridge_v2_fits_and_returns_ordered_quantiles(self) -> None:
        rows = self._series(1040)
        group_id = rows[0].group.selection_id
        model = CalendarRidgeV2Forecaster("ridge-v2-unit", horizons=(1,)).fit(
            {group_id: [rows]}
        )
        target = TimeWindow(rows[-1].window.end, rows[-1].window.end + timedelta(minutes=10))
        forecast = model.predict(rows, issued_at=rows[-1].window.end, target_window=target)
        self.assertLessEqual(forecast.new_session_count.p50, forecast.new_session_count.p90)
        self.assertLessEqual(forecast.new_session_count.p90, forecast.new_session_count.p95)


if __name__ == "__main__":
    unittest.main()
