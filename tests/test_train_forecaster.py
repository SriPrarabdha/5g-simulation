from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from experiments.train_forecaster import _bucket_sequence
from simulator.macro import BoundedMemorySink, ScenarioConfig, Simulator


class ForecasterTrainingInputTests(unittest.TestCase):
    def test_residual_features_are_means_over_the_complete_bucket(self) -> None:
        config = ScenarioConfig.from_dict({
            "scenario_id": "training-means",
            "seed": 7,
            "start_time": "2026-08-05T12:00:00Z",
            "steps": 4,
            "step_seconds": 30,
            "decision_interval_steps": 4,
            "upfs": [{
                "upf_id": "upf-a",
                "zone": "zone-a",
                "capacity_mbps": {"ul": 1000, "dl": 1000},
                "safe_utilization": {"ul": 0.8, "dl": 0.8},
                "session_capacity": 10000,
                "session_safe_utilization": 0.9,
                "queue_limit_seconds": 0,
                "path_latency_ms_by_zone": {"zone-a": 1},
            }],
            "groups": [{
                "key": {"zone": "zone-a", "dnn": "internet", "snssai": "1-1", "five_qi": 9},
                "arrivals_per_step": 3,
                "lifetime_steps": {"min": 10, "max": 10},
                "offered_mbps_per_session": {"ul": 2, "dl": 4},
                "eligible_upfs": ["upf-a"],
            }],
            "events": [],
        })
        simulator = Simulator(config)
        result = BoundedMemorySink(
            simulator.make_summary_sink(), max_steps=config.steps, max_audits=0
        )
        simulator.attach_bounded_advance_sink(result)
        while simulator.current_step < config.steps:
            simulator.advance()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.parquet"
            result.write_parquet(path)
            observations = _bucket_sequence(path, config)["zone-a|internet|1-1"]

        residual = observations[0].existing_load_by_upf["upf-a"]
        expected_sessions = sum(step.upfs[0].active_sessions for step in result.steps) / 4
        expected_ul = sum(step.upfs[0].ul.offered_bytes for step in result.steps) * 8 / 120 / 1_000_000
        expected_dl = sum(step.upfs[0].dl.offered_bytes for step in result.steps) * 8 / 120 / 1_000_000
        self.assertAlmostEqual(residual.surviving_sessions, expected_sessions)
        self.assertAlmostEqual(residual.ul_mbps, expected_ul)
        self.assertAlmostEqual(residual.dl_mbps, expected_dl)


if __name__ == "__main__":
    unittest.main()
