from __future__ import annotations

import hashlib
import json
import math
import statistics
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from forecasting import DemandObservation, TrainedForecastBundle, train_forecast_bundle, write_forecast_bundle
from forecasting.bundle import _sequence_training_rows
from experiments.report_forecast_bundle import build_report
from experiments.freeze_forecast_bundle import build_freeze_record
from schemas import GroupKey, TimeWindow


class ForecastBundleTests(unittest.TestCase):
    def test_vectorized_rows_match_scalar_feature_contract(self) -> None:
        group = GroupKey("zone", "internet", "1-1", 9)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        observations = []
        for index in range(240):
            value = 100 + 20 * math.sin(2 * math.pi * index / 144) + index * .02
            window = TimeWindow(
                start + timedelta(minutes=10 * index),
                start + timedelta(minutes=10 * (index + 1)),
            )
            observations.append(DemandObservation(window, group, value, value * .2, value * 2))

        for field in ("new_session_count", "new_ul_mbps", "new_dl_mbps"):
            values = [float(getattr(item, field)) for item in observations]
            for horizon in (1, 8):
                actual_rows, actual_targets = _sequence_training_rows(observations, field, horizon)
                expected_rows = []
                expected_targets = []
                for origin in range(5, len(observations) - horizon):
                    recent = values[origin - 5:origin + 1]
                    target_index = origin + horizon
                    target_start = observations[target_index].window.start
                    seconds = target_start.hour * 3600 + target_start.minute * 60 + target_start.second
                    daily_angle = 2 * math.pi * seconds / 86_400
                    weekly_angle = 2 * math.pi * target_start.weekday() / 7
                    expected_rows.append([
                        1.0,
                        values[origin],
                        statistics.fmean(recent),
                        values[origin] - values[origin - 1],
                        values[origin - 143] if origin >= 143 else statistics.fmean(recent),
                        math.sin(daily_angle),
                        math.cos(daily_angle),
                        math.sin(weekly_angle),
                        math.cos(weekly_angle),
                    ])
                    expected_targets.append(values[target_index])
                np.testing.assert_allclose(actual_rows, expected_rows, rtol=1e-12, atol=1e-12)
                np.testing.assert_allclose(actual_targets, expected_targets, rtol=0, atol=0)

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
            bundle.validate_groups([group])
            with self.assertRaisesRegex(ValueError, "incompatible"):
                bundle.validate_groups([GroupKey("other-zone", "internet", "1-1", 9)])
            with self.assertRaisesRegex(ValueError, "key_mismatch"):
                bundle.validate_groups([GroupKey("zone", "internet", "1-1", 8)])
            report = build_report(path)
            self.assertEqual(report["groups"], 1)
            self.assertEqual(report["fitted_models"], 24)
            self.assertEqual(len(report["by_horizon"]), 8)
            issued = observations[-1].window.end
            target = TimeWindow(issued + timedelta(minutes=70), issued + timedelta(minutes=80))
            forecast = bundle.predict(observations, issued_at=issued, target_window=target, horizon_steps=8)
            self.assertEqual(forecast.horizon_steps, 8)
            self.assertLessEqual(forecast.new_session_count.p50, forecast.new_session_count.p90)
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({
                "corpus": {
                    "duration_days": 1,
                    "manifest_sha256": "embedded",
                    "nominal_ue_population": 100,
                    "topology": {"groups": 1, "upfs": 1, "zones": 1},
                }
            }), encoding="utf-8")
            manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
            metadata = Path(directory) / "metadata.json"
            metadata.write_text(json.dumps({
                "manifest_sha256": manifest_sha,
                "campaign_id": "unit",
                "summary": {"steps": 240},
            }), encoding="utf-8")
            freeze = build_freeze_record(path, manifest, metadata)
            self.assertEqual(freeze["status"], "frozen-provisional")
            self.assertFalse(freeze["release_accepted"])
            self.assertEqual(freeze["model"]["bundle_sha256"], payload["bundle_sha256"])
            payload["model_version"] = "tampered"
            write_forecast_bundle(path, payload)
            with self.assertRaisesRegex(ValueError, "checksum"):
                TrainedForecastBundle.load(path)


if __name__ == "__main__":
    unittest.main()
