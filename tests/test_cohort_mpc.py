from __future__ import annotations

import json
import unittest
from pathlib import Path

from forecasting import ResidualObservation
from experiments.evaluate_cohort_mpc import evaluate
from experiments.evaluate_cohort_mpc_pilot import (
    SCENARIO_KINDS,
    build_pilot_scenario,
)
from optimization import CohortMPCConfig, solve_cohort_mpc
from simulator.macro import ScenarioConfig, Simulator
from simulator.macro.controllers import CohortMPCController


class CohortMPCTests(unittest.TestCase):
    def test_pilot_matrix_builds_all_four_full_day_scenarios_causally(self) -> None:
        base = ScenarioConfig.from_dict(
            json.loads(Path("configs/demo_scenario.json").read_text())
        )
        for offset, kind in enumerate(SCENARIO_KINDS):
            scenario, events = build_pilot_scenario(
                base,
                kind=kind,
                seed=32001 + offset,
                steps=2880,
                horizon_windows=12,
            )
            self.assertEqual(scenario.steps, 2880)
            self.assertTrue(events)
            self.assertTrue(all(item["step"] < scenario.steps for item in events))
            for item in events:
                known_at = item.get("known_at_step")
                if known_at is not None:
                    self.assertLessEqual(known_at, item["step"])

    def test_development_evaluator_rejects_reserved_validation_seeds(self) -> None:
        with self.assertRaisesRegex(ValueError, "reserved"):
            evaluate(
                Path("configs/demo_scenario.json"),
                Path("configs/demo_forecast_bundle.json"),
                Path("configs/cohort_mpc_development_v1.json"),
                [20260810],
                steps=480,
            )

    def test_known_future_fault_predrains_and_receives_same_state_certificate(self) -> None:
        config = ScenarioConfig.from_dict(self._scenario(known_at_step=0))
        states = Simulator(config).current_states()
        demand = {
            config.groups[0].key.selection_id: [
                ResidualObservation(4, 4, 0.4) for _ in range(4)
            ]
        }
        result = solve_cohort_mpc(
            config,
            config.groups,
            states,
            (),
            demand,
            current_step=0,
            settings=CohortMPCConfig(
                horizon_windows=4,
                max_group_upf_weight=0.75,
                min_relative_improvement=1e-8,
                guardrail_margin_fraction=0.0,
            ),
        )
        group_id = config.groups[0].key.selection_id
        self.assertEqual(result.status, "optimal")
        self.assertEqual(result.known_future_events, 1)
        self.assertTrue(result.certificate.accepted)
        self.assertLess(result.first_allocation[group_id].get("upf-a", 0), 0.75)
        self.assertLessEqual(
            result.certificate.candidate.dropped_bytes["ul"],
            result.certificate.static.dropped_bytes["ul"] * (1 + 1e-7),
        )

    def test_undisclosed_future_fault_is_not_consumed(self) -> None:
        config = ScenarioConfig.from_dict(self._scenario(known_at_step=None))
        states = Simulator(config).current_states()
        group_id = config.groups[0].key.selection_id
        result = solve_cohort_mpc(
            config,
            config.groups,
            states,
            (),
            {group_id: [ResidualObservation(4, 4, 0.4) for _ in range(4)]},
            current_step=0,
            settings=CohortMPCConfig(horizon_windows=4),
        )
        self.assertEqual(result.known_future_events, 0)

    def test_simulator_exposes_exact_anchored_cohorts_without_oracle_arrivals(self) -> None:
        payload = self._scenario(known_at_step=None)
        payload["events"] = []
        simulator = Simulator(ScenarioConfig.from_dict(payload))
        first = simulator.advance()
        context = simulator.control_context()
        self.assertEqual(
            sum(item.sessions for item in context.active_cohorts),
            sum(item.active_sessions - item.departed_sessions for item in first.upfs),
        )
        self.assertTrue(all(item.remaining_steps >= 1 for item in context.active_cohorts))

    def test_controller_defaults_to_static_until_forecast_history_exists(self) -> None:
        config = ScenarioConfig.from_dict(self._scenario(known_at_step=0))
        controller = CohortMPCController(
            mpc_config=CohortMPCConfig(horizon_windows=4)
        )
        simulator = Simulator(config, controller)
        policy = simulator.replan()
        self.assertTrue(policy.fallback.used)
        self.assertIn("insufficient_multi_horizon", policy.fallback.reason)

    @staticmethod
    def _scenario(*, known_at_step: int | None) -> dict:
        event = {
            "step": 4,
            "event_type": "capacity_factor",
            "upf_id": "upf-a",
            "ul_factor": 0.01,
            "dl_factor": 0.01,
        }
        if known_at_step is not None:
            event["known_at_step"] = known_at_step
        return {
            "scenario_id": "cohort-mpc-unit",
            "seed": 17,
            "start_time": "2026-08-07T00:00:00Z",
            "steps": 12,
            "step_seconds": 30,
            "decision_interval_steps": 2,
            "upfs": [
                {
                    "upf_id": "upf-a", "zone": "zone-a",
                    "capacity_mbps": {"ul": 12, "dl": 12},
                    "safe_utilization": {"ul": 1, "dl": 1},
                    "session_capacity": 1000, "session_safe_utilization": 1,
                    "queue_limit_seconds": 0,
                    "path_latency_ms_by_zone": {"zone-a": 1},
                },
                {
                    "upf_id": "upf-b", "zone": "zone-a",
                    "capacity_mbps": {"ul": 4, "dl": 4},
                    "safe_utilization": {"ul": 1, "dl": 1},
                    "session_capacity": 1000, "session_safe_utilization": 1,
                    "queue_limit_seconds": 0,
                    "path_latency_ms_by_zone": {"zone-a": 1},
                },
            ],
            "groups": [{
                "key": {"zone": "zone-a", "dnn": "internet", "snssai": "1-1", "five_qi": 9},
                "arrivals_per_step": 2,
                "lifetime_steps": {"min": 8, "max": 8},
                "offered_mbps_per_session": {"ul": 1, "dl": 0.1},
                "eligible_upfs": ["upf-a", "upf-b"],
            }],
            "events": [event],
        }


if __name__ == "__main__":
    unittest.main()
