"""Causal, deterministic safety guard shared by event-aware controllers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Sequence

from forecasting import ResidualObservation
from schemas import UPFState

if TYPE_CHECKING:
    from simulator.macro.config import GroupProfile, ScenarioConfig


# The engine scores overload as relative excess, ``load / safe_capacity - 1``
# (simulator/macro/engine.py).  A UPF driven to a small capacity fraction is
# therefore weighted by the reciprocal of that fraction.  Projections here use
# the same units so the guard optimises what the campaign measures.  Zero
# capacity is floored at this fraction of nominal so the comparison stays
# finite and monotone in load instead of collapsing to ``inf == inf``.
MINIMUM_CAPACITY_FRACTION = 1e-3


@dataclass(frozen=True, slots=True)
class ExposureGuardConfig:
    enabled: bool = True
    minimum_blend_fraction: float = 0.05
    search_steps: int = 20
    demand_shock_multiplier: float = 3.2
    surprise_capacity_factor: float = 0.45
    tolerance: float = 1e-9

    def __post_init__(self) -> None:
        if not 0 < self.minimum_blend_fraction <= 1:
            raise ValueError("guard minimum blend must be in (0, 1]")
        if self.search_steps < 1:
            raise ValueError("guard search_steps must be positive")
        if self.demand_shock_multiplier < 1:
            raise ValueError("guard demand shock multiplier must be at least one")
        if not 0 <= self.surprise_capacity_factor < 1:
            raise ValueError("guard surprise capacity factor must be in [0, 1)")
        if self.tolerance < 0:
            raise ValueError("guard tolerance must be non-negative")


@dataclass(frozen=True, slots=True)
class ExposureGuardDecision:
    accepted: bool
    requested_blend: float
    executed_blend: float
    allocation: dict[str, dict[str, float]]
    projected_ul_gain: float
    worst_continuation: str | None
    worst_exposure: float
    rejection_reason: str | None


def guard_allocation(
    scenario: ScenarioConfig,
    groups: Sequence[GroupProfile],
    states: Sequence[UPFState],
    residual_by_upf: Mapping[str, ResidualObservation],
    demand_by_group: Mapping[str, ResidualObservation],
    static: Mapping[str, Mapping[str, float]],
    proposed: Mapping[str, Mapping[str, float]],
    *,
    current_step: int,
    horizon_steps: int,
    requested_blend: float = 1.0,
    settings: ExposureGuardConfig | None = None,
) -> ExposureGuardDecision:
    """Accept the strongest safe blend using only state known at ``current_step``."""

    cfg = settings or ExposureGuardConfig()
    static_copy = {key: dict(value) for key, value in static.items()}
    if not cfg.enabled:
        allocation = _blend(static, proposed, requested_blend)
        return ExposureGuardDecision(True, requested_blend, requested_blend, allocation, 0.0, None, 0.0, None)

    declared = _capacity_factors(
        scenario, states, current_step=current_step, horizon_steps=horizon_steps
    )
    static_declared = _project(groups, states, residual_by_upf, demand_by_group, static, declared)
    destination_ids = sorted({upf for weights in proposed.values() for upf, value in weights.items() if value > 0})
    continuations: list[tuple[str, float, str | None]] = [
        ("demand_shock", cfg.demand_shock_multiplier, None)
    ] + [(f"loss:{upf_id}", 1.0, upf_id) for upf_id in destination_ids]

    minimum = min(requested_blend, cfg.minimum_blend_fraction)
    blends = [
        requested_blend - (requested_blend - minimum) * index / cfg.search_steps
        for index in range(cfg.search_steps + 1)
    ]
    worst_name: str | None = None
    worst_exposure = 0.0
    last_reason = "declared_event_no_ul_improvement"
    for blend in blends:
        candidate = _blend(static, proposed, blend)
        candidate_declared = _project(groups, states, residual_by_upf, demand_by_group, candidate, declared)
        ul_gain = static_declared[0] - candidate_declared[0]
        if ul_gain <= cfg.tolerance:
            continue
        safe = True
        local_worst_name = None
        local_worst = 0.0
        for name, shock, failed_upf in continuations:
            factors = dict(declared)
            if failed_upf is not None:
                factors[failed_upf] = min(factors.get(failed_upf, 1.0), cfg.surprise_capacity_factor)
            shocked = {
                key: ResidualObservation(value.surviving_sessions * shock, value.ul_mbps * shock, value.dl_mbps * shock)
                for key, value in demand_by_group.items()
            }
            baseline_metrics = _project(groups, states, residual_by_upf, shocked, static, factors)
            candidate_metrics = _project(groups, states, residual_by_upf, shocked, candidate, factors)
            exposure = max((candidate_metrics[i] - baseline_metrics[i] for i in range(3)), default=0.0)
            if exposure > local_worst:
                local_worst, local_worst_name = exposure, name
            if any(candidate_metrics[i] > baseline_metrics[i] + cfg.tolerance for i in range(3)):
                safe = False
                last_reason = f"continuation_regression:{name}"
                break
        worst_name, worst_exposure = local_worst_name, local_worst
        if safe:
            return ExposureGuardDecision(True, requested_blend, blend, candidate, ul_gain, worst_name, worst_exposure, None)
    return ExposureGuardDecision(False, requested_blend, 0.0, static_copy, 0.0, worst_name, worst_exposure, last_reason)


def _blend(static, proposed, blend: float) -> dict[str, dict[str, float]]:
    result = {}
    for group_id in static:
        destinations = set(static[group_id]) | set(proposed.get(group_id, {}))
        weights = {upf: (1 - blend) * static[group_id].get(upf, 0.0) + blend * proposed.get(group_id, {}).get(upf, 0.0) for upf in destinations}
        total = sum(weights.values())
        result[group_id] = {upf: value / total for upf, value in weights.items() if value > 1e-12}
    return result


def _capacity_factors(scenario, states, *, current_step: int, horizon_steps: int) -> dict[str, float]:
    factors = {state.upf_id: 1.0 for state in states}
    for event in scenario.events:
        if not (current_step < event.step <= current_step + horizon_steps and event.known_at_step is not None and event.known_at_step <= current_step):
            continue
        if event.event_type == "health" and event.health not in {"healthy", "degraded"}:
            factors[event.upf_id] = 0.0
        elif event.event_type == "capacity_factor":
            values = [value for value in (event.ul_factor, event.dl_factor) if value is not None]
            factors[event.upf_id] = min(factors[event.upf_id], min(values, default=1.0))
    return factors


def _project(groups, states, residual, demand, allocation, factors) -> tuple[float, float, float]:
    loads = {state.upf_id: [float(residual.get(state.upf_id, ResidualObservation(0, 0, 0)).ul_mbps), float(residual.get(state.upf_id, ResidualObservation(0, 0, 0)).dl_mbps), float(residual.get(state.upf_id, ResidualObservation(0, 0, 0)).surviving_sessions)] for state in states}
    for group in groups:
        group_id = group.key.selection_id
        item = demand[group_id]
        for upf_id, weight in allocation[group_id].items():
            loads[upf_id][0] += item.ul_mbps * weight
            loads[upf_id][1] += item.dl_mbps * weight
            loads[upf_id][2] += item.surviving_sessions * weight
    metrics = [0.0, 0.0, 0.0]
    for state in states:
        factor = factors.get(state.upf_id, 1.0)
        nominal = (state.safe_capacity_mbps.ul, state.safe_capacity_mbps.dl, float(state.safe_session_capacity))
        for index in range(3):
            floor = nominal[index] * MINIMUM_CAPACITY_FRACTION
            capacity = max(nominal[index] * factor, floor)
            if capacity <= 0:
                continue
            metrics[index] += max(0.0, loads[state.upf_id][index] / capacity - 1.0)
    return tuple(metrics)
