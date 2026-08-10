from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from forecasting import DemandObservation
from schemas import GroupKey, TimeWindow
from simulator.macro.config import GroupProfile
from simulator.macro.controllers import (
    ForecastAdjustmentConfig,
    PredictiveHiGHSController,
    _lifetime_demand_multipliers,
)


UTC = timezone.utc
GROUP = GroupKey("zone-a", "internet", "1-1")


def history(values: list[float]) -> tuple[DemandObservation, ...]:
    start = datetime(2026, 8, 5, tzinfo=UTC)
    return tuple(
        DemandObservation(
            TimeWindow(start + timedelta(minutes=10 * index), start + timedelta(minutes=10 * (index + 1))),
            GROUP, value, value, value,
        )
        for index, value in enumerate(values)
    )


class PredictiveAdjustmentTests(unittest.TestCase):
    def test_observed_anomaly_uses_only_closed_history(self) -> None:
        controller = PredictiveHiGHSController(forecast_adjustment_config=ForecastAdjustmentConfig(
            anomaly_fallback_enabled=True,
            anomaly_history_windows=4,
            anomaly_ratio_threshold=1.5,
            anomaly_multiplier_cap=3,
        ))
        multiplier, flags = controller._forecast_adjustment(
            history([10, 11, 9, 10, 40]), 1.0
        )
        self.assertEqual(multiplier, 3)
        self.assertEqual(flags, ["observed_anomaly_multiplier:3"])

    def test_scheduled_hint_takes_precedence_over_anomaly(self) -> None:
        controller = PredictiveHiGHSController(forecast_adjustment_config=ForecastAdjustmentConfig(
            scheduled_event_hints_enabled=True,
            anomaly_fallback_enabled=True,
        ))
        multiplier, flags = controller._forecast_adjustment(
            history([10, 10, 50]), 6.0
        )
        self.assertEqual(multiplier, 6)
        self.assertEqual(flags, ["causal_scheduled_event_multiplier:6"])

    def test_lifetime_weighting_is_relative_and_favors_persistent_cohorts(self) -> None:
        short = GroupProfile(GROUP, 1, 1, 20, 1, 1, ("upf-a",))
        long = GroupProfile(GroupKey("zone-b", "internet", "1-1"), 1, 200, 400, 1, 1, ("upf-a",))
        weights = _lifetime_demand_multipliers(
            (short, long), decision_interval_steps=20,
            horizon_windows=6, strength=1,
        )
        self.assertLess(weights[short.key.selection_id], 1)
        self.assertGreater(weights[long.key.selection_id], 1)
        self.assertAlmostEqual(sum(weights.values()) / 2, 1)


if __name__ == "__main__":
    unittest.main()
