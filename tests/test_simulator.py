from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from simulator.macro import ScenarioConfig, Simulator, load_scenario
from simulator.macro.config import ScenarioEvent


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
            self.assertTrue(any(
                json.loads(line)["record_type"] == "selection_audit"
                for line in first.read_text().splitlines()[1:]
            ))

    def test_parquet_output_has_canonical_schema_and_nested_upfs(self) -> None:
        result = Simulator(ScenarioConfig.from_dict(self._scenario(10.0))).run()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.parquet"
            result.write_parquet(path)
            import pyarrow.parquet as pq
            table = pq.read_table(path)
            self.assertEqual(table.schema.metadata[b"schema_version"], b"simulation-step/1.0")
            self.assertEqual(table.num_rows, len(result.steps))
            self.assertEqual(len(table.column("upfs")[0].as_py()), 1)
            audit_path = Path(directory) / "selection-audits.parquet"
            result.write_selection_audits_parquet(audit_path)
            audits = pq.read_table(audit_path)
            self.assertEqual(audits.schema.metadata[b"schema_version"], b"selection-audit/1.0")
            self.assertGreater(audits.num_rows, 0)

    def test_crowd_and_link_events_are_applied_at_the_declared_step(self) -> None:
        payload = self._scenario(100.0)
        group_id = "zone-a|internet|1-1"
        payload["events"] = [
            {"step": 2, "event_type": "arrival_factor", "group_id": group_id, "arrival_factor": 5},
            {"step": 2, "event_type": "path_latency", "upf_id": "upf-a", "zone": "zone-a", "latency_ms": 50},
        ]
        result = Simulator(ScenarioConfig.from_dict(payload)).run()
        before = sum(result.steps[index].group_arrivals[group_id] for index in range(2))
        after = sum(result.steps[index].group_arrivals[group_id] for index in range(2, 8))
        self.assertGreater(after / 6, before / 2)

    def test_incremental_fault_changes_only_future_ticks(self) -> None:
        simulator = Simulator(ScenarioConfig.from_dict(self._scenario(100.0)))
        first = simulator.advance()
        second = simulator.advance()
        realized = [first.to_dict(), second.to_dict()]
        simulator.inject_event(ScenarioEvent(
            step=simulator.current_step, event_type="health",
            upf_id="upf-a", health="unavailable",
        ))
        third = simulator.advance()
        self.assertEqual([first.to_dict(), second.to_dict()], realized)
        self.assertEqual(third.upfs[0].health, "unavailable")
        self.assertEqual(simulator.current_step, 3)

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
