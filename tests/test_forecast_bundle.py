from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from forecasting import DemandObservation, TrainedForecastBundle, train_forecast_bundle, write_forecast_bundle
from schemas import GroupKey, TimeWindow


class ForecastBundleTests(unittest.TestCase):
    def test_national_scale_features_are_numerically_stable(self) -> None:
        group = GroupKey("zone", "high-volume", "1-2", 9)
        start = datetime(2026, 1, 5, tzinfo=timezone.utc)
        observations = []
        for index in range(96):
            value = 200_000.0 + (index % 4) * 2_500.0
            window = TimeWindow(
                start + timedelta(minutes=10 * index),
                start + timedelta(minutes=10 * (index + 1)),
            )
            observations.append(DemandObservation(window, group, value, value * 3, value * 6))
        payload = train_forecast_bundle(
            {group.selection_id: [observations]},
            model_version="national-scale-unit",
        )
        coefficient = payload["groups"][group.selection_id]["targets"]["new_session_count"]["1"]["model"]["coefficients"]
        self.assertTrue(all(math.isfinite(item) for item in coefficient))

    def test_frozen_bundle_is_checksum_verified_and_supports_eight_horizons(self) -> None:
        group = GroupKey("zone", "internet", "1-1", 9)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        observations = []
        for index in range(240):
            value = 100 + 20 * math.sin(2 * math.pi * index / 144) + index * .02
            window = TimeWindow(start + timedelta(minutes=10 * index), start + timedelta(minutes=10 * (index + 1)))
            observations.append(DemandObservation(window, group, value, value * .2, value * 2))
        payload = train_forecast_bundle(
            {group.selection_id: [observations]}, model_version="unit-bundle",
            source={"synthetic": True},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.json"
            write_forecast_bundle(path, payload)
            bundle = TrainedForecastBundle.load(path)
            issued = observations[-1].window.end
            target = TimeWindow(issued + timedelta(minutes=70), issued + timedelta(minutes=80))
            forecast = bundle.predict(observations, issued_at=issued, target_window=target, horizon_steps=8)
            self.assertEqual(forecast.horizon_steps, 8)
            self.assertLessEqual(forecast.new_session_count.p50, forecast.new_session_count.p90)
            payload["model_version"] = "tampered"
            write_forecast_bundle(path, payload)
            with self.assertRaisesRegex(ValueError, "checksum"):
                TrainedForecastBundle.load(path)


if __name__ == "__main__":
    unittest.main()
