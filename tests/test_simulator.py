from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
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
        self.assertEqual(first.summary["control_scope"], "new_session_placement_only")
        self.assertFalse(first.summary["session_migration_supported"])
        self.assertTrue(any(step.group_upf_admissions for step in first.steps))
        for step in first.steps:
            self.assertEqual(
                sum(sum(by_upf.values()) for by_upf in step.group_upf_admissions.values()),
                sum(upf.new_sessions for upf in step.upfs),
            )

    def test_capacity_reduction_does_not_change_offered_demand(self) -> None:
        high = Simulator(ScenarioConfig.from_dict(self._scenario(100.0))).run().summary
        low = Simulator(ScenarioConfig.from_dict(self._scenario(1.0))).run().summary
        self.assertEqual(high["offered_bytes"], low["offered_bytes"])
        self.assertLess(low["carried_bytes"]["ul"], high["carried_bytes"]["ul"])
        self.assertGreater(low["dropped_bytes"]["ul"], high["dropped_bytes"]["ul"])

    def test_total_overload_is_decomposed_without_hiding_it(self) -> None:
        summary = Simulator(ScenarioConfig.from_dict(self._scenario(1.0))).run().summary
        for direction in ("ul", "dl"):
            total = summary["overload_area_seconds"][direction]
            residual = summary["residual_overload_area_seconds"][direction]
            incremental = summary["incremental_new_session_overload_area_seconds"][direction]
            self.assertGreater(total, 0)
            self.assertGreaterEqual(residual, 0)
            self.assertGreaterEqual(incremental, 0)
            self.assertAlmostEqual(total, residual + incremental)

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
            self.assertEqual(table.schema.metadata[b"schema_version"], b"simulation-step/1.1")
            self.assertEqual(table.num_rows, len(result.steps))
            self.assertEqual(len(table.column("upfs")[0].as_py()), 1)
            joint = table.column("group_upf_buckets").to_pylist()
            self.assertEqual([index for index, rows in enumerate(joint) if rows], [3, 7])
            self.assertEqual(joint[0], [])
            self.assertEqual(joint[3][0]["five_qi"], 9)
            first_bucket_admitted = sum(item["admitted_sessions"] for item in joint[3])
            self.assertEqual(
                first_bucket_admitted,
                sum(step.upfs[0].new_sessions for step in result.steps[:4]),
            )
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

    def test_scheduled_arrival_hint_is_exposed_only_when_event_becomes_active(self) -> None:
        payload = self._scenario(100.0)
        payload["events"] = [{
            "step": 4, "event_type": "arrival_factor",
            "group_id": "zone-a|internet|1-1", "arrival_factor": 5,
            "known_at_step": 0, "forecast_hint_multiplier": 5,
        }]
        simulator = Simulator(ScenarioConfig.from_dict(payload))
        self.assertEqual(simulator.control_context().scheduled_multiplier_by_group[
            "zone-a|internet|1-1"
        ], 1.0)
        for _ in range(4):
            simulator.advance()
        simulator.replan()
        self.assertEqual(simulator.control_context().scheduled_multiplier_by_group[
            "zone-a|internet|1-1"
        ], 5.0)

    def test_forecast_hint_validation_prevents_future_leakage(self) -> None:
        with self.assertRaisesRegex(ValueError, "known_at_step"):
            ScenarioEvent(
                step=4, event_type="arrival_factor",
                group_id="zone-a|internet|1-1", arrival_factor=5,
                known_at_step=5, forecast_hint_multiplier=5,
            )

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

    def test_selection_audit_stride_retains_a_deterministic_sample(self) -> None:
        config = replace(ScenarioConfig.from_dict(self._scenario(100.0)), selection_audit_stride=3)
        first = Simulator(config).run()
        second = Simulator(config).run()
        total_arrivals = sum(sum(step.group_arrivals.values()) for step in first.steps)
        self.assertEqual(len(first.selection_audits), (total_arrivals + 2) // 3)
        self.assertEqual(
            [item.to_dict() for item in first.selection_audits],
            [item.to_dict() for item in second.selection_audits],
        )
        self.assertEqual(first.summary["selection_audit_stride"], 3)

    def test_run_reports_bounded_progress_and_completion(self) -> None:
        updates: list[tuple[int, int]] = []
        config = ScenarioConfig.from_dict(self._scenario(100.0))
        result = Simulator(config).run(
            progress_interval_steps=3,
            progress_callback=lambda completed, total: updates.append((completed, total)),
        )
        self.assertEqual(updates, [(3, 8), (6, 8), (8, 8)])
        self.assertEqual(len(result.steps), 8)

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
                "key": {"zone": "zone-a", "dnn": "internet", "snssai": "1-1", "five_qi": 9},
                "arrivals_per_step": 5, "lifetime_steps": {"min": 4, "max": 4},
                "offered_mbps_per_session": {"ul": 1, "dl": 1},
                "eligible_upfs": ["upf-a"]
            }],
            "events": []
        }


if __name__ == "__main__":
    unittest.main()
