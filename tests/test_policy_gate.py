from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from schemas import Capacity, ConstraintSlack, Fallback, GroupKey, Policy, PolicyGroup, SolverReport, TimeWindow, UPFState
from steering import PolicyGate, PolicyGateConfig


NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
GROUP = GroupKey("zone", "internet", "1-1")


def policy(version: int, a: float) -> Policy:
    start = NOW + timedelta(minutes=10 * (version - 1))
    return Policy(
        f"p{version}", version, start, TimeWindow(start, start + timedelta(minutes=10)),
        "forecast", start, SolverReport("test", "optimal", 0), ConstraintSlack(),
        [PolicyGroup(GROUP, {"upf-a": a, "upf-b": 1 - a})], Fallback(), "test",
    )


def states(health: str = "healthy") -> list[UPFState]:
    return [
        UPFState(NOW, upf_id, Capacity(100, 100), Capacity(1, 1), 1000, 1,
                 health if upf_id == "upf-a" else "healthy", "zone", [GROUP.selection_id],
                 {"zone": 1}, 600, "test")
        for upf_id in ("upf-a", "upf-b")
    ]


class PolicyGateTests(unittest.TestCase):
    def test_hold_hysteresis_churn_and_emergency_are_real_decisions(self) -> None:
        gate = PolicyGate(PolicyGateConfig(2, .03, .18, 1.0))
        self.assertTrue(gate.evaluate(policy(1, .5), epoch=1, candidate_objective=.8,
                                      states=states()).applied)
        held = gate.evaluate(policy(2, .55), epoch=2, candidate_objective=.79,
                             current=policy(1, .5), current_objective=.8, states=states())
        self.assertEqual((held.action, held.reason), ("hold", "minimum_hold"))
        hysteresis = gate.evaluate(policy(3, .55), epoch=3, candidate_objective=.79,
                                   current=policy(2, .5), current_objective=.8, states=states())
        self.assertEqual(hysteresis.reason, "hysteresis")
        churn = gate.evaluate(policy(4, .9), epoch=4, candidate_objective=.7,
                              current=policy(3, .5), current_objective=.8, states=states())
        self.assertEqual(churn.reason, "churn_budget")
        emergency = gate.evaluate(policy(5, .1), epoch=5, candidate_objective=.8,
                                  current=policy(4, .9), current_objective=1.2, states=states())
        self.assertEqual(emergency.action, "emergency_apply")
        self.assertTrue(emergency.emergency_override)


if __name__ == "__main__":
    unittest.main()
