"""Tests for the causal exposure guard and the v4 mixed-stress campaign.

The v3 campaign shipped none of the unit tests its own plan called for, and a
20,000-pair verdict rested on that code.  These cover the behaviours the audit
found broken or unverified: relative-overload projection units, exact-Static
fallback, blend reduction, continuation enumeration, absence of future
peeking, scenario/controller independence, and an analysis that cannot let a
data defect masquerade as a controller result.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from experiments import analyze_mixed_stress_v4 as analysis
from experiments.mixed_stress_campaign_v4 import (
    FAMILIES, NOTICE_HOURS, SEED_POOLS, build_family, candidate_arm,
    development_cells, notice_hours_for_seed, seeds_for,
)
from experiments.mixed_stress_campaign import PROTECTED_SEEDS, churn_l1_per_group_hour
from forecasting import ResidualObservation
from optimization import exposure_guard
from optimization.exposure_guard import ExposureGuardConfig, guard_allocation
from optimization.predrain_flow import PreDrainFlowConfig, solve_predrain_flow
from schemas import Capacity, UPFState
from simulator.macro.config import GroupKey, GroupProfile, ScenarioConfig, ScenarioEvent, UPFProfile

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
GROUP = GroupKey("zone-a", "internet", "1-1")


def _upf(upf_id: str, *, ul: float, dl: float, sessions: int = 10_000) -> UPFProfile:
    return UPFProfile(
        upf_id=upf_id, zone="zone-a", capacity_ul_mbps=ul, capacity_dl_mbps=dl,
        safe_utilization_ul=1.0, safe_utilization_dl=1.0, session_capacity=sessions,
        session_safe_utilization=1.0, queue_limit_seconds=1.0,
        path_latency_ms_by_zone={"zone-a": 1.0},
    )


def _state(upf_id: str, *, ul: float, dl: float, sessions: int = 10_000) -> UPFState:
    return UPFState(
        measurement_time=NOW, upf_id=upf_id, capacity_mbps=Capacity(ul, dl),
        safe_utilization=Capacity(1, 1), session_capacity=sessions,
        session_safe_utilization=1, health="healthy", zone="zone-a",
        eligible_groups=[GROUP.selection_id],
        path_latency_ms_by_zone={"zone-a": 1.0}, state_ttl_seconds=60,
        calibration_version="test",
    )


def _scenario(events: tuple[ScenarioEvent, ...], upfs: tuple[UPFProfile, ...]) -> ScenarioConfig:
    group = GroupProfile(
        key=GROUP, arrivals_per_step=10.0, lifetime_steps_min=10, lifetime_steps_max=20,
        offered_ul_mbps_per_session=1.0, offered_dl_mbps_per_session=1.0,
        eligible_upfs=tuple(item.upf_id for item in upfs),
    )
    return ScenarioConfig(
        scenario_id="guard-test", seed=1, start_time=NOW, steps=600, step_seconds=30,
        decision_interval_steps=20, groups=(group,), upfs=upfs, events=events,
    )


class ExposureGuardTests(unittest.TestCase):
    """The guard must project in the unit the campaign scores."""

    def setUp(self) -> None:
        self.upfs = (_upf("upf-a", ul=1000, dl=1000), _upf("upf-b", ul=1000, dl=1000))
        self.states = [_state("upf-a", ul=1000, dl=1000), _state("upf-b", ul=1000, dl=1000)]
        self.groups = _scenario((), self.upfs).groups
        self.residual = {
            "upf-a": ResidualObservation(0, 0, 0),
            "upf-b": ResidualObservation(0, 0, 0),
        }
        self.demand = {GROUP.selection_id: ResidualObservation(100, 900, 900)}
        self.static = {GROUP.selection_id: {"upf-a": 0.5, "upf-b": 0.5}}
        self.proposed = {GROUP.selection_id: {"upf-b": 1.0}}

    def _guard(self, events, *, settings=None, proposed=None, blend=1.0):
        return guard_allocation(
            _scenario(events, self.upfs), self.groups, self.states, self.residual,
            self.demand, self.static, proposed or self.proposed,
            current_step=0, horizon_steps=100, requested_blend=blend,
            settings=settings or ExposureGuardConfig(),
        )

    def test_no_declared_event_publishes_exact_static(self) -> None:
        decision = self._guard(())
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.executed_blend, 0.0)
        self.assertEqual(decision.allocation, self.static)

    def test_future_event_not_yet_known_is_ignored(self) -> None:
        """known_at_step after the decision step must not reach the guard."""
        hidden = ScenarioEvent(50, "capacity_factor", upf_id="upf-a",
                               ul_factor=0.05, dl_factor=0.05, known_at_step=40)
        self.assertFalse(self._guard((hidden,)).accepted)
        declared = ScenarioEvent(50, "capacity_factor", upf_id="upf-a",
                                 ul_factor=0.05, dl_factor=0.05, known_at_step=0)
        self.assertTrue(self._guard((declared,)).accepted)

    def test_event_beyond_horizon_is_ignored(self) -> None:
        far = ScenarioEvent(500, "capacity_factor", upf_id="upf-a",
                            ul_factor=0.05, dl_factor=0.05, known_at_step=0)
        self.assertFalse(self._guard((far,)).accepted)

    def test_declared_event_drains_the_degraded_upf(self) -> None:
        event = ScenarioEvent(50, "capacity_factor", upf_id="upf-a",
                              ul_factor=0.05, dl_factor=0.05, known_at_step=0)
        decision = self._guard((event,))
        self.assertTrue(decision.accepted)
        self.assertGreater(decision.projected_ul_gain, 0.0)
        self.assertGreater(decision.allocation[GROUP.selection_id]["upf-b"], 0.5)

    def test_projection_uses_relative_overload_units(self) -> None:
        """When both UPFs are overloaded, only relative units see the gain.

        Absolute excess summed over UPFs is ``total_load - total_capacity``,
        which is invariant to how the load is split once every UPF is over its
        envelope.  The v3 projection was in absolute Mbps, so during exactly the
        conditions pre-drain exists for it reported no improvement and the guard
        rejected with ``declared_event_no_ul_improvement``.  Relative excess
        falls when load moves off the UPF with the smaller envelope, which is
        what the engine scores.
        """
        states = [_state("upf-a", ul=1000, dl=1_000_000, sessions=10**7),
                  _state("upf-b", ul=100, dl=1_000_000, sessions=10**7)]
        groups = _scenario((), (_upf("upf-a", ul=1000, dl=1_000_000),
                                _upf("upf-b", ul=100, dl=1_000_000))).groups
        residual = {"upf-a": ResidualObservation(0, 0, 0), "upf-b": ResidualObservation(0, 0, 0)}
        demand = {GROUP.selection_id: ResidualObservation(10, 300, 10)}
        factors = {"upf-a": 0.05, "upf-b": 1.0}  # upf-a envelope becomes 50 Mbps
        static = {GROUP.selection_id: {"upf-a": 0.5, "upf-b": 0.5}}
        drained = {GROUP.selection_id: {"upf-b": 1.0}}

        static_ul = exposure_guard._project(groups, states, residual, demand, static, factors)[0]
        drained_ul = exposure_guard._project(groups, states, residual, demand, drained, factors)[0]
        # Relative: (150/50 - 1) + (150/100 - 1) = 2.5 versus (300/100 - 1) = 2.0
        self.assertAlmostEqual(static_ul, 2.5, places=6)
        self.assertAlmostEqual(drained_ul, 2.0, places=6)
        self.assertLess(drained_ul, static_ul)
        # Absolute Mbps would have reported 150 versus 200 -- a false regression.
        absolute_static = (150 - 50) + (150 - 100)
        absolute_drained = (300 - 100)
        self.assertGreater(absolute_drained, absolute_static)

    def test_zero_capacity_is_floored_rather_than_infinite(self) -> None:
        """An inf projection makes every comparison a tie and defeats the guard."""
        states = [_state("upf-a", ul=1000, dl=1000)]
        groups = _scenario((), (_upf("upf-a", ul=1000, dl=1000),)).groups
        residual = {"upf-a": ResidualObservation(0, 0, 0)}
        demand = {GROUP.selection_id: ResidualObservation(1, 10, 10)}
        allocation = {GROUP.selection_id: {"upf-a": 1.0}}
        heavy = exposure_guard._project(groups, states, residual,
                                        {GROUP.selection_id: ResidualObservation(1, 20, 10)},
                                        allocation, {"upf-a": 0.0})[0]
        light = exposure_guard._project(groups, states, residual, demand, allocation,
                                        {"upf-a": 0.0})[0]
        self.assertTrue(math.isfinite(heavy) and math.isfinite(light))
        self.assertGreater(heavy, light)

    def test_continuations_cover_every_proposed_destination(self) -> None:
        """Losing a destination the candidate loads must be tested."""
        self.upfs = (_upf("upf-a", ul=1000, dl=1000), _upf("upf-b", ul=120, dl=100_000))
        self.states = [_state("upf-a", ul=1000, dl=1000),
                       _state("upf-b", ul=120, dl=100_000)]
        self.demand = {GROUP.selection_id: ResidualObservation(10, 400, 10)}
        event = ScenarioEvent(50, "capacity_factor", upf_id="upf-a",
                              ul_factor=0.05, dl_factor=0.05, known_at_step=0)
        decision = self._guard(
            (event,), settings=ExposureGuardConfig(surprise_capacity_factor=0.02)
        )
        self.assertFalse(decision.accepted)
        self.assertIn("continuation_regression:loss:upf-b", decision.rejection_reason or "")
        self.assertEqual(decision.allocation, self.static)

    def test_blend_reduces_before_rejecting(self) -> None:
        event = ScenarioEvent(50, "capacity_factor", upf_id="upf-a",
                              ul_factor=0.05, dl_factor=0.05, known_at_step=0)
        decision = self._guard((event,), blend=0.5)
        self.assertTrue(decision.accepted)
        self.assertLessEqual(decision.executed_blend, 0.5)
        self.assertGreater(decision.executed_blend, 0.0)

    def test_disabled_guard_passes_the_requested_blend_through(self) -> None:
        decision = self._guard((), settings=ExposureGuardConfig(enabled=False), blend=0.4)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.executed_blend, 0.4)


class PreDrainFlowUnitTests(unittest.TestCase):
    def test_overflow_is_priced_in_relative_units(self) -> None:
        """Overflow on a small UPF must cost more than the same Mbps on a big one.

        The engine scores ``load / capacity - 1``.  Pricing slack per absolute
        Mbps, as v3 did, told the LP that spilling 10 Mbps onto a 20 Mbps UPF
        was no worse than spilling it onto a 2000 Mbps UPF.
        """
        upfs = (_upf("upf-big", ul=2000, dl=2000), _upf("upf-small", ul=20, dl=20))
        states = [_state("upf-big", ul=2000, dl=2000), _state("upf-small", ul=20, dl=20)]
        scenario = _scenario(
            (ScenarioEvent(20, "capacity_factor", upf_id="upf-big", ul_factor=0.1,
                           dl_factor=0.1, known_at_step=0),),
            upfs,
        )
        residual = {"upf-big": ResidualObservation(0, 0, 0), "upf-small": ResidualObservation(0, 0, 0)}
        demand = {GROUP.selection_id: ResidualObservation(10, 100, 100)}
        result = solve_predrain_flow(
            scenario, scenario.groups, states, residual, demand,
            current_step=0, settings=PreDrainFlowConfig(lead_windows=4, max_group_upf_weight=1.0),
        )
        self.assertEqual(result.status, "optimal")
        # Demand exceeds the small UPF outright, so the solution must not simply
        # dump everything there to dodge the risk cost on the big UPF.
        self.assertLess(result.allocation[GROUP.selection_id].get("upf-small", 0.0), 1.0)

    def test_no_known_event_skips_without_solving(self) -> None:
        upfs = (_upf("upf-a", ul=1000, dl=1000),)
        result = solve_predrain_flow(
            _scenario((), upfs), _scenario((), upfs).groups, [_state("upf-a", ul=1000, dl=1000)],
            {"upf-a": ResidualObservation(0, 0, 0)},
            {GROUP.selection_id: ResidualObservation(1, 1, 1)},
            current_step=0,
        )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.message, "no_known_reduction_in_lead_horizon")


class CampaignConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _scenario((), (
            _upf("upf-a", ul=2000, dl=2000), _upf("upf-b", ul=1500, dl=1500),
            _upf("upf-c", ul=1000, dl=1000), _upf("upf-d", ul=800, dl=800),
        ))

    def test_scenario_is_independent_of_the_candidate_arm(self) -> None:
        """Notice must be a scenario property, never derived from the controller."""
        for family in FAMILIES:
            first = build_family(self.base, family, 80000)
            second = build_family(self.base, family, 80000)
            self.assertEqual(first.events, second.events)
        self.assertEqual(notice_hours_for_seed(80000), notice_hours_for_seed(80000))

    def test_notice_axis_is_exercised(self) -> None:
        cells = development_cells(0)
        observed = {cell["notice_hours"] for cell in cells}
        self.assertEqual(observed, set(NOTICE_HOURS))

    def test_declared_events_carry_notice_and_surprises_do_not(self) -> None:
        for seed in seeds_for("development", "declared_maintenance"):
            events = build_family(self.base, "declared_maintenance", seed).events
            self.assertTrue(events)
            for event in events:
                self.assertIsNotNone(event.known_at_step)
                self.assertLess(event.known_at_step, event.step)
        for family in ("surprise_demand", "surprise_brownout"):
            for seed in seeds_for("development", family):
                for event in build_family(self.base, family, seed).events:
                    self.assertIsNone(event.known_at_step)

    def test_every_event_leaves_finite_capacity(self) -> None:
        """v3 used health=unavailable, which makes the scored metric infinite."""
        for family in FAMILIES:
            for seed in seeds_for("development", family):
                for event in build_family(self.base, family, seed).events:
                    if event.event_type == "capacity_factor":
                        self.assertGreater(event.ul_factor, 0.0)
                        self.assertGreater(event.dl_factor, 0.0)
                    self.assertNotEqual(event.event_type, "health")

    def test_families_are_balanced_and_seeds_are_safe(self) -> None:
        cells = development_cells(7)
        self.assertEqual(len(cells), 125)
        for family in FAMILIES:
            self.assertEqual(sum(cell["family"] == family for cell in cells), 25)
        for stage in SEED_POOLS:
            for family in FAMILIES:
                seeds = set(seeds_for(stage, family))
                self.assertFalse(seeds & PROTECTED_SEEDS)
                self.assertFalse(seeds & set(range(47000, 71000)))

    def test_arm_grid_is_complete_and_distinct(self) -> None:
        arms = [candidate_arm(index) for index in range(160)]
        self.assertEqual(len({(a.controller, a.cadence_minutes, a.horizon_hours,
                               a.maximum_blend, a.destination_reserve,
                               a.surprise_capacity_factor) for a in arms}), 160)
        self.assertEqual(sum(a.controller == "mpc" for a in arms), 32)

    def test_churn_normalisation(self) -> None:
        value = churn_l1_per_group_hour(60.0, groups=10, steps=120, step_seconds=30)
        self.assertAlmostEqual(value, 6.0)


class AnalysisTests(unittest.TestCase):
    """A data defect must never read as a controller verdict."""

    def _pair(self, family: str, seed: int, baseline: float, candidate: float) -> dict:
        zeros = {"ul": 0.0, "dl": 0.0}
        def summary(area: float) -> dict:
            return {
                "overload_area_seconds": {"ul": area, "dl": 0.0},
                "overload_duration_seconds": dict(zeros), "offered_bytes": dict(zeros),
                "carried_bytes": dict(zeros), "dropped_bytes": dict(zeros),
                "rejected_bytes": dict(zeros), "establishment_failures": 0, "steps": 100,
            }
        return {
            "cell": {"arm": {"index": 0, "controller": "predrain", "cadence_minutes": 10,
                             "horizon_hours": 2, "maximum_blend": 0.5,
                             "destination_reserve": 0.8, "surprise_capacity_factor": 0.45},
                     "family": family, "seed": seed, "notice_hours": 2.0},
            "group_count": 1, "step_seconds": 30, "sizing": {},
            "static": summary(baseline), "hybrid": summary(candidate),
            "decision_diagnostics": [], "control_metrics": {},
        }

    def _analyze(self, pairs: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as root:
            node = Path(root) / "node-0" / "pairs"
            node.mkdir(parents=True)
            for index, pair in enumerate(pairs):
                (node / f"{pair['cell']['family']}-{index:06d}.json").write_text(
                    json.dumps(pair), encoding="utf-8")
            return analysis.analyze_arm(node.parent)

    def _balanced(self, **overrides: tuple[float, float]) -> list[dict]:
        pairs = []
        for position, family in enumerate(FAMILIES):
            baseline, candidate = overrides.get(family, (100.0, 100.0))
            for seed in range(5):
                pairs.append(self._pair(family, position * 5 + seed, baseline, candidate))
        return pairs

    def test_family_normalisation_resists_a_dominant_denominator(self) -> None:
        """One family with 100x the overload mass must not decide the campaign."""
        result = self._analyze(self._balanced(
            declared_maintenance=(100.0, 10.0),
            maintenance_then_stadium=(100.0, 10.0),
            maintenance_then_brownout=(1_000_000.0, 1_010_000.0),
        ))
        # Raw pooled severity weighting would report a net loss here.
        self.assertGreater(result["macro_gain"], 0.2)
        self.assertFalse(result["performance_gates"]["no_family_aggregate_regression"])

    def test_validity_is_separate_from_performance(self) -> None:
        pairs = self._balanced(declared_maintenance=(100.0, 10.0))
        pairs[-1]["static"]["overload_area_seconds"]["ul"] = math.inf
        pairs[-1]["hybrid"]["overload_area_seconds"]["ul"] = math.inf
        result = self._analyze(pairs)
        self.assertFalse(result["validity"]["all_overload_metrics_finite"])
        self.assertFalse(result["valid"])
        # The infinite tie must not be charged against the controller.
        self.assertEqual(result["family"]["surprise_brownout"]["aggregate_gain"], 0.0)

    def test_harm_ratio_bounds_the_tail_in_scored_units(self) -> None:
        result = self._analyze(self._balanced(
            declared_maintenance=(1000.0, 100.0),
            maintenance_then_brownout=(10.0, 400.0),
        ))
        self.assertAlmostEqual(result["overload_removed"], 900.0 * 5)
        self.assertAlmostEqual(result["overload_added"], 390.0 * 5)
        self.assertFalse(result["performance_gates"]["harm_ratio_within_limit"])

    def test_informative_mean_is_not_diluted_by_zero_baseline_pairs(self) -> None:
        pairs = self._balanced(declared_maintenance=(100.0, 20.0))
        for pair in pairs:
            if pair["cell"]["family"] != "declared_maintenance":
                pair["static"]["overload_area_seconds"]["ul"] = 0.0
                pair["hybrid"]["overload_area_seconds"]["ul"] = 0.0
        result = self._analyze(pairs)
        self.assertAlmostEqual(result["family"]["declared_maintenance"]["mean_informative_gain"], 0.8)
        self.assertAlmostEqual(result["mean_informative_gain"], 0.8)
        self.assertAlmostEqual(result["mean_pair_ul_gain"], 0.8 / len(FAMILIES))


if __name__ == "__main__":
    unittest.main()
