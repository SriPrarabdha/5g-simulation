from __future__ import annotations

import unittest
from pathlib import Path

from experiments.freeze_oracle_benchmark import build_freeze_record

from optimization.oracle_bounds import (
    bucket_arrivals_from_steps,
    evaluate_allocation,
    expected_bucket_arrivals,
    solve_bounded_migration_bound,
    solve_new_session_bound,
    static_capacity_allocation,
)
from simulator.macro import ScenarioConfig


class OracleBoundTests(unittest.TestCase):
    def test_stage_a_freeze_revalidates_all_benchmark_inputs(self) -> None:
        record = build_freeze_record(
            Path("output/models/extreme-oracle-bound-evaluation-v1.json"),
            Path("configs/extreme_validation_fault_knowledge_v1.json"),
            Path("docs/extreme-oracle-bound-results.md"),
        )
        self.assertEqual(record["status"], "frozen-stage-a-benchmark")
        self.assertEqual(len(record["scenario_inputs"]), 2)
        self.assertEqual(len(record["freeze_record_sha256"]), 64)

    def test_bucket_arrivals_require_an_exact_known_group_trace(self) -> None:
        config = ScenarioConfig.from_dict(self._scenario())
        group_id = config.groups[0].key.selection_id
        trace = [{group_id: step} for step in range(config.steps)]
        buckets = bucket_arrivals_from_steps(config, trace)
        self.assertEqual(buckets[group_id].tolist(), [1.0, 5.0, 9.0, 13.0])
        with self.assertRaisesRegex(ValueError, "length"):
            bucket_arrivals_from_steps(config, trace[:-1])
        trace[0] = {"unknown": 1}
        with self.assertRaisesRegex(ValueError, "unknown group"):
            bucket_arrivals_from_steps(config, trace)

    def test_clairvoyant_fault_bound_can_predrain_but_arrival_oracle_cannot(self) -> None:
        config = ScenarioConfig.from_dict(self._scenario())
        arrivals = expected_bucket_arrivals(config)
        baseline = evaluate_allocation(
            config, arrivals, static_capacity_allocation(config)
        )
        causal = solve_new_session_bound(
            config,
            arrivals,
            regime="arrival_only",
            guardrail_metrics=baseline,
            timeout_seconds=10,
        )
        clairvoyant = solve_new_session_bound(
            config,
            arrivals,
            regime="clairvoyant_fault",
            guardrail_metrics=baseline,
            timeout_seconds=10,
        )
        self.assertEqual(causal.status, "optimal")
        self.assertEqual(clairvoyant.status, "optimal")
        self.assertIsNotNone(causal.metrics)
        self.assertIsNotNone(clairvoyant.metrics)
        self.assertLess(
            clairvoyant.metrics.overload_area_seconds["ul"],
            causal.metrics.overload_area_seconds["ul"],
        )
        self.assertLessEqual(
            clairvoyant.metrics.dropped_bytes["ul"],
            baseline.dropped_bytes["ul"] + 1e-6,
        )

    def test_scheduled_fault_is_visible_only_at_declared_knowledge_time(self) -> None:
        undisclosed = ScenarioConfig.from_dict(self._scenario())
        disclosed_payload = self._scenario()
        disclosed_payload["events"][0]["known_at_step"] = 0
        disclosed = ScenarioConfig.from_dict(disclosed_payload)
        results = {}
        for name, config in (("undisclosed", undisclosed), ("disclosed", disclosed)):
            arrivals = expected_bucket_arrivals(config)
            baseline = evaluate_allocation(
                config, arrivals, static_capacity_allocation(config)
            )
            results[name] = solve_new_session_bound(
                config,
                arrivals,
                regime="scheduled_fault",
                guardrail_metrics=baseline,
                timeout_seconds=10,
            )
        self.assertGreater(
            results["undisclosed"].metrics.overload_area_seconds["ul"],
            results["disclosed"].metrics.overload_area_seconds["ul"],
        )

    def test_bounded_migration_respects_half_l1_turnover_budget(self) -> None:
        config = ScenarioConfig.from_dict(self._scenario())
        arrivals = expected_bucket_arrivals(config)
        baseline = evaluate_allocation(
            config, arrivals, static_capacity_allocation(config)
        )
        fraction = 0.1
        result = solve_bounded_migration_bound(
            config,
            arrivals,
            migration_fraction_per_bucket=fraction,
            guardrail_metrics=baseline,
            timeout_seconds=10,
        )
        self.assertEqual(result.status, "optimal")
        group_id = config.groups[0].key.selection_id
        for bucket in range(1, 4):
            previous = result.allocation[(group_id, bucket - 1)]
            current = result.allocation[(group_id, bucket)]
            turnover = 0.5 * sum(
                abs(current.get(upf, 0.0) - previous.get(upf, 0.0))
                for upf in {"upf-a", "upf-b"}
            )
            self.assertLessEqual(turnover, fraction + 1e-7)

    @staticmethod
    def _scenario() -> dict:
        return {
            "scenario_id": "oracle-unit",
            "seed": 7,
            "start_time": "2026-08-07T00:00:00Z",
            "steps": 8,
            "step_seconds": 30,
            "decision_interval_steps": 2,
            "upfs": [
                {
                    "upf_id": "upf-a",
                    "zone": "zone-a",
                    "capacity_mbps": {"ul": 12, "dl": 12},
                    "safe_utilization": {"ul": 1, "dl": 1},
                    "session_capacity": 100000,
                    "session_safe_utilization": 1,
                    "queue_limit_seconds": 0,
                    "path_latency_ms_by_zone": {"zone-a": 1},
                },
                {
                    "upf_id": "upf-b",
                    "zone": "zone-a",
                    "capacity_mbps": {"ul": 4, "dl": 4},
                    "safe_utilization": {"ul": 1, "dl": 1},
                    "session_capacity": 100000,
                    "session_safe_utilization": 1,
                    "queue_limit_seconds": 0,
                    "path_latency_ms_by_zone": {"zone-a": 1},
                },
            ],
            "groups": [
                {
                    "key": {
                        "zone": "zone-a",
                        "dnn": "internet",
                        "snssai": "1-1",
                        "five_qi": 9,
                    },
                    "arrivals_per_step": 2,
                    "lifetime_steps": {"min": 8, "max": 8},
                    "offered_mbps_per_session": {"ul": 1, "dl": 1},
                    "eligible_upfs": ["upf-a", "upf-b"],
                }
            ],
            "events": [
                {
                    "step": 4,
                    "event_type": "capacity_factor",
                    "upf_id": "upf-a",
                    "ul_factor": 0.01,
                    "dl_factor": 0.01,
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
