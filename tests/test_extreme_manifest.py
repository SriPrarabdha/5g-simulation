from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from experiments.build_extreme_history_manifest import STEPS_PER_DAY, build, build_optimizer_pilot
from simulator.macro import ScenarioConfig


ROOT = Path(__file__).resolve().parents[1]


class ExtremeManifestTests(unittest.TestCase):
    def test_profile_build_is_deterministic_and_high_scale(self) -> None:
        profile = json.loads((ROOT / "configs" / "extreme_training_profile.json").read_text())
        start = datetime(2026, 1, 5, tzinfo=timezone.utc)
        first = build(profile, 77, start, days=1)
        second = build(profile, 77, start, days=1)
        self.assertEqual(first, second)
        self.assertEqual(first["steps"], STEPS_PER_DAY)
        self.assertEqual(len(first["groups"]), 96)
        self.assertEqual(len(first["upfs"]), 24)
        self.assertEqual(first["corpus"]["nominal_ue_population"], 16_000_000)
        self.assertEqual(first["selection_audit_stride"], 5000)
        self.assertGreater(len(first["events"]), 96 * 24)
        ScenarioConfig.from_dict(first)

    def test_full_profile_has_canonical_training_split(self) -> None:
        profile = json.loads((ROOT / "configs" / "extreme_training_profile.json").read_text())
        payload = build(profile, 88, datetime(2026, 1, 5, tzinfo=timezone.utc))
        self.assertEqual(payload["steps"], 16 * 7 * STEPS_PER_DAY)
        self.assertEqual(payload["corpus"]["split"]["train_weeks"], [1, 11])
        self.assertIn("near_total_upf_outages", payload["corpus"]["stress_families"])

    def test_optimizer_pilot_is_one_day_fresh_seed_and_event_dense(self) -> None:
        profile = json.loads((ROOT / "configs" / "extreme_training_profile.json").read_text())
        payload = build_optimizer_pilot(
            profile, 20260806, datetime(2026, 5, 4, tzinfo=timezone.utc)
        )
        self.assertEqual(payload["steps"], STEPS_PER_DAY)
        self.assertEqual(payload["scenario_id"], "extreme-optimizer-pilot-1d-s20260806")
        self.assertEqual(len(payload["corpus"]["pilot_surge_windows"]), 3)
        self.assertEqual(payload["corpus"]["pilot_fault_events"], 14)
        self.assertGreater(
            max(
                event["arrival_factor"]
                for event in payload["events"]
                if event["event_type"] == "arrival_factor"
            ),
            5.0,
        )
        ScenarioConfig.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
