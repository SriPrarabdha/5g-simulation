from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from simulator.macro import ScenarioConfig, Simulator, load_scenario


class SimulatorTests(unittest.TestCase):
    def test_demo_manifest_runs_deterministically(self) -> None:
        config = load_scenario("configs/demo_scenario.json")
        first = Simulator(config).run()
        second = Simulator(config).run()
        self.assertEqual([step.to_dict() for step in first.steps], [step.to_dict() for step in second.steps])
        self.assertEqual(len(first.steps), config.steps)
        self.assertGreater(first.summary["offered_bytes"]["ul"], 0)

    def test_capacity_reduction_does_not_change_offered_demand(self) -> None:
        high = Simulator(ScenarioConfig.from_dict(self._scenario(100.0))).run().summary
        low = Simulator(ScenarioConfig.from_dict(self._scenario(1.0))).run().summary
        self.assertEqual(high["offered_bytes"], low["offered_bytes"])
        self.assertLess(low["carried_bytes"]["ul"], high["carried_bytes"]["ul"])
        self.assertGreater(low["dropped_bytes"]["ul"], high["dropped_bytes"]["ul"])

    def test_jsonl_output_is_byte_stable(self) -> None:
        result = Simulator(ScenarioConfig.from_dict(self._scenario(10.0))).run()
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "first.jsonl", Path(directory) / "second.jsonl"
            result.write_jsonl(first)
            result.write_jsonl(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(json.loads(first.read_text().splitlines()[0])["record_type"], "simulation_metadata")

    @staticmethod
    def _scenario(capacity: float) -> dict:
        return {
            "scenario_id": "unit", "seed": 42, "start_time": "2026-08-05T12:00:00Z",
            "steps": 8, "step_seconds": 30, "decision_interval_steps": 4,
            "upfs": [{
                "upf_id": "upf-a", "zone": "zone-a",
                "capacity_mbps": {"ul": capacity, "dl": capacity},
                "safe_utilization": {"ul": 0.8, "dl": 0.8},
                "session_capacity": 10000, "session_safe_utilization": 0.9,
                "queue_limit_seconds": 0, "path_latency_ms_by_zone": {"zone-a": 1}
            }],
            "groups": [{
                "key": {"zone": "zone-a", "dnn": "internet", "snssai": "1-1"},
                "arrivals_per_step": 5, "lifetime_steps": {"min": 4, "max": 4},
                "offered_mbps_per_session": {"ul": 1, "dl": 1},
                "eligible_upfs": ["upf-a"]
            }],
            "events": []
        }


if __name__ == "__main__":
    unittest.main()
