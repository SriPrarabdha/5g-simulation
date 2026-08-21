"""Bounded single-epoch min-cost flow for scheduled capacity pre-drain."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Sequence

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from forecasting import ResidualObservation
from schemas import ConstraintSlack, UPFState

if TYPE_CHECKING:
    from simulator.macro.config import GroupProfile, ScenarioConfig


@dataclass(frozen=True, slots=True)
class PreDrainFlowConfig:
    lead_windows: int = 12
    timeout_seconds: float = 0.25
    max_group_upf_weight: float = 0.75
    event_risk_cost: float = 100.0
    latency_cost: float = 0.01
    utilization_cost: float = 0.10
    overflow_cost: float = 10_000.0
    overflow_tolerance: float = 1e-7
    action_blend_fraction: float = 1.0
    minimum_action_blend_fraction: float | None = None
    full_action_below_residual_utilization: float = 0.50
    minimum_action_above_residual_utilization: float = 0.80

    def __post_init__(self) -> None:
        if self.lead_windows < 1 or self.timeout_seconds <= 0:
            raise ValueError("pre-drain horizon and timeout must be positive")
        if not 0 < self.max_group_upf_weight <= 1:
            raise ValueError("pre-drain maximum weight must be in (0, 1]")
        if not 0 < self.action_blend_fraction <= 1:
            raise ValueError("pre-drain action blend fraction must be in (0, 1]")
        if self.minimum_action_blend_fraction is not None:
            if not 0 < self.minimum_action_blend_fraction <= self.action_blend_fraction:
                raise ValueError(
                    "minimum pre-drain blend must be positive and no greater than full blend"
                )
            if not (
                0 <= self.full_action_below_residual_utilization
                < self.minimum_action_above_residual_utilization
            ):
                raise ValueError("adaptive pre-drain utilization thresholds are invalid")
        if min(
            self.event_risk_cost, self.latency_cost,
            self.utilization_cost, self.overflow_cost, self.overflow_tolerance,
        ) < 0:
            raise ValueError("pre-drain costs must be non-negative")


@dataclass(frozen=True, slots=True)
class PreDrainFlowResult:
    status: str
    message: str
    runtime_ms: int
    allocation: dict[str, dict[str, float]]
    targeted_upfs: tuple[str, ...]
    variable_count: int
    equality_constraints: int
    inequality_constraints: int
    matrix_nonzeros: int
    overflow: float
    constraint_slack: ConstraintSlack = field(default_factory=ConstraintSlack)


def _known_reduction_risk(
    scenario: ScenarioConfig, *, current_step: int, lead_steps: int,
) -> dict[str, float]:
    risk: dict[str, float] = {}
    for event in scenario.events:
        if (
            event.step <= current_step
            or event.step > current_step + lead_steps
            or event.known_at_step is None
            or event.known_at_step > current_step
            or event.event_type not in {"capacity_factor", "health"}
        ):
            continue
        if event.event_type == "health":
            severity = 1.0 if event.health not in {"healthy", "degraded"} else 0.0
        else:
            factors = [
                value for value in (event.ul_factor, event.dl_factor)
                if value is not None
            ]
            severity = max(0.0, 1.0 - min(factors, default=1.0))
        if severity <= 0:
            continue
        urgency = 1.0 - (event.step - current_step - 1) / max(1, lead_steps)
        risk[event.upf_id or ""] = max(
            risk.get(event.upf_id or "", 0.0), severity * max(0.0, urgency)
        )
    return risk


def solve_predrain_flow(
    scenario: ScenarioConfig,
    groups: Sequence[GroupProfile],
    states: Sequence[UPFState],
    residual_by_upf: Mapping[str, ResidualObservation],
    demand_by_group: Mapping[str, ResidualObservation],
    *,
    current_step: int,
    settings: PreDrainFlowConfig | None = None,
) -> PreDrainFlowResult:
    config = settings or PreDrainFlowConfig()
    started = time.perf_counter()
    states = tuple(states)
    groups = tuple(groups)
    state_by_id = {state.upf_id: state for state in states}
    upf_index = {state.upf_id: index for index, state in enumerate(states)}
    risk = _known_reduction_risk(
        scenario,
        current_step=current_step,
        lead_steps=config.lead_windows * scenario.decision_interval_steps,
    )
    if not risk:
        return PreDrainFlowResult(
            "skipped", "no_known_reduction_in_lead_horizon", 0, {}, (),
            0, 0, 0, 0, 0.0,
        )
    keys = [
        (group.key.selection_id, upf_id)
        for group in groups
        for upf_id in group.eligible_upfs
        if state_by_id[upf_id].health in {"healthy", "degraded"}
    ]
    x_count = len(keys)
    upf_count = len(states)
    # UL, DL and session overflow slacks make overload explicit and preserve
    # feasibility; their high cost ensures they are used only when unavoidable.
    variable_count = x_count + 3 * upf_count
    objective = np.zeros(variable_count)
    for column, (group_id, upf_id) in enumerate(keys):
        group = next(item for item in groups if item.key.selection_id == group_id)
        state = state_by_id[upf_id]
        residual = residual_by_upf.get(upf_id, ResidualObservation(0, 0, 0))
        utilization = max(
            residual.ul_mbps / max(state.safe_capacity_mbps.ul, 1e-9),
            residual.dl_mbps / max(state.safe_capacity_mbps.dl, 1e-9),
            residual.surviving_sessions / max(state.safe_session_capacity, 1),
        )
        latency = state.path_latency_ms_by_zone.get(group.key.zone, 1_000.0)
        objective[column] = (
            config.event_risk_cost * risk.get(upf_id, 0.0)
            + config.latency_cost * latency
            + config.utilization_cost * utilization
        )
    objective[x_count:] = config.overflow_cost

    group_index = {group.key.selection_id: index for index, group in enumerate(groups)}
    eq_rows, eq_columns = [], []
    for column, (group_id, _upf_id) in enumerate(keys):
        eq_rows.append(group_index[group_id])
        eq_columns.append(column)
    equality = coo_matrix(
        (np.ones(len(eq_rows)), (eq_rows, eq_columns)),
        shape=(len(groups), variable_count),
    ).tocsr()
    equality_rhs = np.ones(len(groups))

    ub_rows: list[int] = []
    ub_columns: list[int] = []
    ub_data: list[float] = []
    for column, (group_id, upf_id) in enumerate(keys):
        demand = demand_by_group[group_id]
        upf = upf_index[upf_id]
        for offset, value in enumerate((demand.ul_mbps, demand.dl_mbps, demand.surviving_sessions)):
            ub_rows.append(offset * upf_count + upf)
            ub_columns.append(column)
            ub_data.append(value)
    for offset in range(3):
        for upf in range(upf_count):
            ub_rows.append(offset * upf_count + upf)
            ub_columns.append(x_count + offset * upf_count + upf)
            ub_data.append(-1.0)
    inequality = coo_matrix(
        (ub_data, (ub_rows, ub_columns)),
        shape=(3 * upf_count, variable_count),
    ).tocsr()
    limits = []
    for direction in ("ul", "dl", "sessions"):
        for state in states:
            residual = residual_by_upf.get(state.upf_id, ResidualObservation(0, 0, 0))
            if direction == "ul":
                limits.append(state.safe_capacity_mbps.ul - residual.ul_mbps)
            elif direction == "dl":
                limits.append(state.safe_capacity_mbps.dl - residual.dl_mbps)
            else:
                limits.append(state.safe_session_capacity - residual.surviving_sessions)
    lower = np.zeros(variable_count)
    upper = np.full(variable_count, np.inf)
    eligible_counts = Counter(group_id for group_id, _ in keys)
    for column, (group_id, _upf_id) in enumerate(keys):
        upper[column] = max(
            config.max_group_upf_weight, 1.0 / eligible_counts[group_id]
        )
    try:
        solution = linprog(
            objective,
            A_ub=inequality,
            b_ub=np.asarray(limits),
            A_eq=equality,
            b_eq=equality_rhs,
            bounds=np.column_stack((lower, upper)),
            method="highs",
            options={"time_limit": config.timeout_seconds},
        )
    except Exception as error:
        return PreDrainFlowResult(
            "error", f"HiGHS error: {error}",
            round((time.perf_counter() - started) * 1000), {}, tuple(sorted(risk)),
            variable_count, equality.shape[0], inequality.shape[0],
            equality.nnz + inequality.nnz, 0.0,
        )
    runtime_ms = round((time.perf_counter() - started) * 1000)
    if not solution.success or solution.x is None:
        status = "timeout" if solution.status == 1 else "infeasible" if solution.status == 2 else "error"
        return PreDrainFlowResult(
            status, solution.message, runtime_ms, {}, tuple(sorted(risk)),
            variable_count, equality.shape[0], inequality.shape[0],
            equality.nnz + inequality.nnz, 0.0,
        )
    allocation: dict[str, dict[str, float]] = {group.key.selection_id: {} for group in groups}
    for column, (group_id, upf_id) in enumerate(keys):
        if solution.x[column] > 1e-10:
            allocation[group_id][upf_id] = float(solution.x[column])
    slack_by_resource = []
    for offset in range(3):
        values = {
            state.upf_id: float(solution.x[x_count + offset * upf_count + upf])
            for upf, state in enumerate(states)
            if solution.x[x_count + offset * upf_count + upf] > config.overflow_tolerance
        }
        slack_by_resource.append(values)
    constraint_slack = ConstraintSlack(*slack_by_resource)
    constraint_slack.validate()
    return PreDrainFlowResult(
        "optimal", solution.message, runtime_ms, allocation, tuple(sorted(risk)),
        variable_count, equality.shape[0], inequality.shape[0],
        equality.nnz + inequality.nnz, float(np.sum(solution.x[x_count:])),
        constraint_slack,
    )
