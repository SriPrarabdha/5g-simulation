from __future__ import annotations

import threading
import unittest
from datetime import datetime, timedelta, timezone

from optimization import OptimizationConfig, solve_allocation
from schemas import Capacity, ExistingLoad, Forecast, GroupKey, Quantiles, TimeWindow, UPFState
from steering import AtomicPolicyStore, PolicyValidationError, ValidationConfig, validate_policy


UTC = timezone.utc
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
TARGET = TimeWindow(NOW, NOW + timedelta(minutes=10))
GROUP = GroupKey("zone-a", "internet", "1-1")


def state(
    upf_id: str,
    *,
    ul: float = 100,
    dl: float = 100,
    sessions: int = 1000,
    health: str = "healthy",
    eligible: bool = True,
    latency: float = 1,
) -> UPFState:
    return UPFState(
        measurement_time=NOW,
        upf_id=upf_id,
        capacity_mbps=Capacity(ul, dl),
        safe_utilization=Capacity(1, 1),
        session_capacity=sessions,
        session_safe_utilization=1,
        health=health,
        zone="zone-a",
        eligible_groups=[GROUP.selection_id] if eligible else [],
        path_latency_ms_by_zone={"zone-a": latency},
        state_ttl_seconds=60,
        calibration_version="test",
    )


def forecast(*, ul: float = 100, dl: float = 100, arrivals: float = 100, residual_a: float = 0) -> Forecast:
    q_arrivals = Quantiles(arrivals, arrivals, arrivals)
    q_ul = Quantiles(ul, ul, ul)
    q_dl = Quantiles(dl, dl, dl)
    existing = []
    if residual_a:
        q_residual = Quantiles(residual_a, residual_a, residual_a)
        existing.append(ExistingLoad("upf-a", q_residual, q_residual, q_residual))
    return Forecast(
        forecast_id="forecast-a",
        issued_at=NOW,
        source_window_end=NOW,
        target_window=TARGET,
        horizon_steps=1,
        group=GROUP,
        new_session_count=q_arrivals,
        new_load_ul_mbps=q_ul,
        new_load_dl_mbps=q_dl,
        existing_load_by_upf=existing,
        model_version="test",
    )


@unittest.skipUnless(__import__("importlib").util.find_spec("scipy"), "SciPy/HiGHS not installed")
class OptimizationTests(unittest.TestCase):
    def test_hand_solvable_balanced_topology(self) -> None:
        result = solve_allocation(
            [forecast()], [state("upf-a"), state("upf-b")],
            created_at=NOW, policy_version=1,
            config=OptimizationConfig(locality_cost=0, churn_cost=0),
        )
        self.assertEqual(result.status, "optimal")
        self.assertIsNotNone(result.policy)
        self.assertLess(result.policy.solver.runtime_ms, 1000)  # type: ignore[union-attr]
        weights = result.policy.weights_for(GROUP)  # type: ignore[union-attr]
        self.assertAlmostEqual(weights["upf-a"], 0.5, places=6)
        self.assertAlmostEqual(weights["upf-b"], 0.5, places=6)
        validate_policy(result.policy, [forecast()], [state("upf-a"), state("upf-b")], activation_time=NOW)  # type: ignore[arg-type]

    def test_ul_constraint_and_latency_limit_are_independent(self) -> None:
        result = solve_allocation(
            [forecast(ul=120, dl=20)],
            [state("upf-a", ul=40, dl=100, latency=1), state("upf-b", ul=100, dl=10, latency=50)],
            created_at=NOW, policy_version=1,
            config=OptimizationConfig(max_latency_ms=10),
        )
        self.assertEqual(result.status, "feasible_with_slack")
        self.assertEqual(result.policy.weights_for(GROUP), {"upf-a": 1.0})  # type: ignore[union-attr]
        with self.assertRaisesRegex(PolicyValidationError, "disabled"):
            validate_policy(result.policy, [forecast(ul=120, dl=20)], [state("upf-a", ul=40, dl=100, latency=1), state("upf-b", ul=100, dl=10, latency=50)], activation_time=NOW)  # type: ignore[arg-type]
        validate_policy(
            result.policy, [forecast(ul=120, dl=20)],
            [state("upf-a", ul=40, dl=100, latency=1), state("upf-b", ul=100, dl=10, latency=50)],
            activation_time=NOW,
            config=ValidationConfig(allow_feasible_with_slack=True),
        )  # type: ignore[arg-type]

    def test_structural_infeasibility_publishes_no_policy(self) -> None:
        result = solve_allocation(
            [forecast()], [state("upf-a", eligible=False), state("upf-b", health="unavailable")],
            created_at=NOW, policy_version=1,
        )
        self.assertEqual(result.status, "infeasible")
        self.assertIsNone(result.policy)

    def test_atomic_store_compare_and_swap_never_partially_publishes(self) -> None:
        solved = solve_allocation(
            [forecast()], [state("upf-a"), state("upf-b")], created_at=NOW, policy_version=1
        )
        policy = solved.policy
        self.assertIsNotNone(policy)
        validate_policy(policy, [forecast()], [state("upf-a"), state("upf-b")], activation_time=NOW)  # type: ignore[arg-type]
        store = AtomicPolicyStore()
        errors: list[Exception] = []

        def publish() -> None:
            try:
                store.publish(policy, expected_current_version=0)  # type: ignore[arg-type]
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=publish) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(errors), 1)
        self.assertEqual(store.read().to_dict(), policy.to_dict())  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
