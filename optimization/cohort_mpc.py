"""Causal, new-session-only cohort-state model-predictive control.

The optimizer carries exact residual cohorts supplied by the simulator and an
expected survival model for future admissions.  It plans several decision
windows but returns only the first allocation.  A candidate is publishable
only when replay from the same state beats the contemporaneous static plan
without directional overload, drop, or session-capacity regression.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Mapping, Sequence

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix, csr_matrix, eye, hstack, vstack

from forecasting import ResidualObservation
from schemas import UPFState
from .survival import SurvivalTable

if TYPE_CHECKING:
    from simulator.macro.config import GroupProfile, ScenarioConfig, ScenarioEvent


@dataclass(frozen=True, slots=True)
class ActiveCohort:
    """An anchored active cohort as observed at a decision boundary."""

    group_id: str
    upf_id: str
    sessions: float
    remaining_steps: int
    ul_mbps_per_session: float
    dl_mbps_per_session: float

    def __post_init__(self) -> None:
        if self.sessions < 0 or self.remaining_steps < 1:
            raise ValueError("active cohorts require non-negative sessions and positive remaining steps")
        if min(self.ul_mbps_per_session, self.dl_mbps_per_session) < 0:
            raise ValueError("active-cohort rates must be non-negative")


@dataclass(frozen=True, slots=True)
class CohortMPCConfig:
    horizon_windows: int = 12
    timeout_seconds: float = 2.0
    max_group_upf_weight: float = 0.75
    overload_cost: float = 1.0
    drop_cost: float = 10.0
    terminal_exposure_cost: float = 1.0
    terminal_failure_exposure_cost: float = 0.0
    max_ul_exposure_increase_fraction: float = 1.0
    static_deviation_cost: float = 1e-4
    action_blend_fraction: float = 1.0
    min_relative_improvement: float = 1e-4
    min_ul_overload_relative_improvement: float = 0.0
    require_known_future_capacity_event: bool = False
    fallback_on_unplanned_capacity_state: bool = False
    fallback_on_telemetry_uncertainty: bool = False
    scheduled_capacity_factor_bias: float = 0.0
    scheduled_event_start_offset_windows: int = 0
    scheduled_event_end_offset_windows: int = 0
    guardrail_margin_fraction: float = 0.01
    guardrail_tolerance: float = 1e-7
    adaptive_forecast_risk_enabled: bool = False
    forecast_risk_alpha: float = 1.0
    failure_domain_protection_enabled: bool = False
    max_failure_domain_share: float = 1.0
    n_minus_one_exposure_cost: float = 0.0
    max_policy_l1_change_per_group: float = 2.0
    minimum_hold_epochs: int = 0
    solve_trigger_safe_utilization: float = 0.0
    fallback_on_stale_survival: bool = True
    minimum_survival_samples: int = 0
    retain_last_safe_on_failure: bool = False
    low_confidence_survival_safety_factor: float = 1.10

    def __post_init__(self) -> None:
        if self.horizon_windows < 2:
            raise ValueError("horizon_windows must be at least two")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0 < self.max_group_upf_weight <= 1:
            raise ValueError("max_group_upf_weight must be in (0, 1]")
        if min(
            self.overload_cost,
            self.drop_cost,
            self.terminal_exposure_cost,
            self.terminal_failure_exposure_cost,
            self.max_ul_exposure_increase_fraction,
            self.static_deviation_cost,
            self.min_relative_improvement,
            self.min_ul_overload_relative_improvement,
            self.guardrail_margin_fraction,
            self.guardrail_tolerance,
            self.n_minus_one_exposure_cost,
        ) < 0:
            raise ValueError("MPC costs and tolerances must be non-negative")
        if self.guardrail_margin_fraction >= 1:
            raise ValueError("guardrail_margin_fraction must be less than one")
        if self.min_ul_overload_relative_improvement >= 1:
            raise ValueError("min_ul_overload_relative_improvement must be less than one")
        if not 0 < self.action_blend_fraction <= 1:
            raise ValueError("action_blend_fraction must be in (0, 1]")
        if not 0 <= self.forecast_risk_alpha <= 1:
            raise ValueError("forecast_risk_alpha must be in [0, 1]")
        if not 0 < self.max_failure_domain_share <= 1:
            raise ValueError("max_failure_domain_share must be in (0, 1]")
        if not 0 <= self.max_policy_l1_change_per_group <= 2:
            raise ValueError("max_policy_l1_change_per_group must be in [0, 2]")
        if self.minimum_hold_epochs < 0 or self.minimum_survival_samples < 0:
            raise ValueError("hold epochs and survival samples must be non-negative")
        if not 0 <= self.solve_trigger_safe_utilization < 1:
            raise ValueError("solve_trigger_safe_utilization must be in [0, 1)")
        if self.low_confidence_survival_safety_factor < 1:
            raise ValueError("low-confidence survival safety factor must be at least one")
        if not -1 < self.scheduled_capacity_factor_bias < 1:
            raise ValueError("scheduled capacity factor bias must lie in (-1, 1)")
        if abs(self.scheduled_event_start_offset_windows) > self.horizon_windows:
            raise ValueError("scheduled event start offset exceeds the planning horizon")
        if abs(self.scheduled_event_end_offset_windows) > self.horizon_windows:
            raise ValueError("scheduled event end offset exceeds the planning horizon")


@dataclass(frozen=True, slots=True)
class MPCMetrics:
    overload_area_seconds: dict[str, float]
    dropped_bytes: dict[str, float]
    session_overload_area_seconds: float
    terminal_max_safe_utilization: float
    terminal_max_ul_mbps: float
    static_deviation_l1: float
    score: float


@dataclass(frozen=True, slots=True)
class MPCCertificate:
    accepted: bool
    reason: str
    relative_improvement: float
    ul_overload_relative_improvement: float
    candidate: MPCMetrics
    static: MPCMetrics


@dataclass(frozen=True, slots=True)
class CohortMPCResult:
    status: str
    message: str
    runtime_ms: int
    first_allocation: dict[str, dict[str, float]]
    static_first_allocation: dict[str, dict[str, float]]
    planned_allocation: dict[tuple[str, int], dict[str, float]]
    certificate: MPCCertificate | None
    known_future_events: int
    survival_audit: dict[str, dict[str, object]] | None = None
    model_variables: int = 0
    equality_constraints: int = 0
    inequality_constraints: int = 0
    matrix_nonzeros: int = 0


@dataclass(frozen=True, slots=True)
class _CapacityPath:
    safe_ul: np.ndarray
    safe_dl: np.ndarray
    physical_ul: np.ndarray
    physical_dl: np.ndarray
    safe_sessions: np.ndarray
    admission_healthy: np.ndarray


def solve_cohort_mpc(
    scenario: ScenarioConfig,
    groups: Sequence[GroupProfile],
    states: Sequence[UPFState],
    active_cohorts: Sequence[ActiveCohort],
    demand_by_group: Mapping[str, Sequence[ResidualObservation]],
    *,
    current_step: int,
    settings: CohortMPCConfig | None = None,
    survival_by_group: Mapping[str, SurvivalTable] | None = None,
    previous_allocation: Mapping[str, Mapping[str, float]] | None = None,
) -> CohortMPCResult:
    """Plan a causal horizon and certify its first-action plan against static."""

    config = settings or CohortMPCConfig()
    started = time.perf_counter()
    groups = tuple(groups)
    states = tuple(states)
    if not 0 <= current_step < scenario.steps:
        raise ValueError("current_step falls outside the scenario")
    group_by_id = {group.key.selection_id: group for group in groups}
    if set(demand_by_group) != set(group_by_id):
        raise ValueError("demand horizon must exactly match controller groups")
    if any(len(values) != config.horizon_windows for values in demand_by_group.values()):
        raise ValueError("every demand horizon must match horizon_windows")
    state_by_id = {state.upf_id: state for state in states}
    if set(state_by_id) != {upf.upf_id for upf in scenario.upfs}:
        raise ValueError("UPF states must exactly match the scenario")
    for cohort in active_cohorts:
        if cohort.group_id not in group_by_id or cohort.upf_id not in state_by_id:
            raise ValueError("active cohort references an unknown group or UPF")
    if survival_by_group is not None and set(survival_by_group) != set(group_by_id):
        raise ValueError("survival tables must exactly match controller groups")
    survival_audit = (
        {
            group_id: table.audit(
                now=scenario.start_time + timedelta(seconds=current_step * scenario.step_seconds)
            )
            for group_id, table in survival_by_group.items()
        }
        if survival_by_group is not None else {
            group_id: {
                "source": "configured-uniform-compatibility",
                "age_seconds": 0.0,
                "sample_count": 0,
                "confidence": "oracle",
                "upper_confidence": False,
                "stale": False,
            }
            for group_id in group_by_id
        }
    )

    path, known_count = _known_capacity_path(
        scenario, states, current_step, config.horizon_windows, config
    )
    model = _PlanningModel(
        scenario, groups, active_cohorts, demand_by_group, config.horizon_windows,
        survival_by_group=survival_by_group,
        low_confidence_safety_factor=config.low_confidence_survival_safety_factor,
    )
    static_vector = _static_plan(groups, path, model)
    if static_vector is None:
        return CohortMPCResult(
            "infeasible", "no static allocation exists across the planning horizon",
            round((time.perf_counter() - started) * 1000), {}, {}, {}, None, known_count,
            survival_audit, len(model.x_keys), len(model.columns_by_group_window), 0, 0,
        )

    nx = len(model.x_keys)
    load_rows = len(states) * config.horizon_windows
    sul = slice(nx, nx + load_rows)
    sdl = slice(sul.stop, sul.stop + load_rows)
    sn = slice(sdl.stop, sdl.stop + load_rows)
    dul = slice(sn.stop, sn.stop + load_rows)
    ddl = slice(dul.stop, dul.stop + load_rows)
    terminal = ddl.stop
    failure_exposure = terminal + 1
    change = slice(failure_exposure + 1, failure_exposure + 1 + nx)
    variable_count = change.stop
    seconds = scenario.step_seconds * scenario.decision_interval_steps

    objective = np.zeros(variable_count)
    objective[sul] = config.overload_cost * seconds / np.maximum(path.safe_ul.reshape(-1), 1e-9)
    objective[sdl] = config.overload_cost * seconds / np.maximum(path.safe_dl.reshape(-1), 1e-9)
    objective[sn] = config.overload_cost * seconds / np.maximum(path.safe_sessions.reshape(-1), 1e-9)
    nominal_ul = np.repeat(
        np.array([upf.capacity_ul_mbps for upf in scenario.upfs])[:, None],
        config.horizon_windows, axis=1,
    )
    nominal_dl = np.repeat(
        np.array([upf.capacity_dl_mbps for upf in scenario.upfs])[:, None],
        config.horizon_windows, axis=1,
    )
    objective[dul] = config.drop_cost * seconds / nominal_ul.reshape(-1)
    objective[ddl] = config.drop_cost * seconds / nominal_dl.reshape(-1)
    objective[terminal] = config.terminal_exposure_cost
    objective[failure_exposure] = (
        config.terminal_failure_exposure_cost + config.n_minus_one_exposure_cost
        / max(1e-9, float(np.mean(nominal_ul)))
    )
    objective[change] = config.static_deviation_cost / max(1, nx)

    equality_rows: list[int] = []
    equality_columns: list[int] = []
    for row, columns in enumerate(model.columns_by_group_window.values()):
        equality_rows.extend([row] * len(columns))
        equality_columns.extend(columns)
    equality = coo_matrix(
        (np.ones(len(equality_rows)), (equality_rows, equality_columns)),
        shape=(len(model.columns_by_group_window), variable_count),
    ).tocsr()
    equality_rhs = np.ones(equality.shape[0])

    identity = eye(load_rows, format="csr")
    zero_load = csr_matrix((load_rows, load_rows))
    zero_x = csr_matrix((load_rows, nx))
    zero_tail = csr_matrix((load_rows, 2 + nx))
    safe_ul_rows = hstack([model.ul, -identity, zero_load, zero_load, zero_load, zero_load, zero_tail], format="csr")
    safe_dl_rows = hstack([model.dl, zero_load, -identity, zero_load, zero_load, zero_load, zero_tail], format="csr")
    session_rows = hstack([model.sessions, zero_load, zero_load, -identity, zero_load, zero_load, zero_tail], format="csr")
    drop_ul_rows = hstack([model.ul, zero_load, zero_load, zero_load, -identity, zero_load, zero_tail], format="csr")
    drop_dl_rows = hstack([model.dl, zero_load, zero_load, zero_load, zero_load, -identity, zero_tail], format="csr")
    inequalities = [safe_ul_rows, safe_dl_rows, session_rows, drop_ul_rows, drop_dl_rows]
    rhs = [
        path.safe_ul.reshape(-1) - model.residual_ul.reshape(-1),
        path.safe_dl.reshape(-1) - model.residual_dl.reshape(-1),
        path.safe_sessions.reshape(-1) - model.residual_sessions.reshape(-1),
        path.physical_ul.reshape(-1) - model.residual_ul.reshape(-1),
        path.physical_dl.reshape(-1) - model.residual_dl.reshape(-1),
    ]

    # Make the static comparison a solver constraint as well as a post-solve
    # certificate. This avoids finding an attractive weighted compromise that
    # would later be rejected for a directional regression.
    static_metrics = _metrics(
        static_vector, static_vector, model, path, scenario, config
    )
    static_ul_load = (
        np.asarray(model.ul @ static_vector).reshape(model.residual_ul.shape)
        + model.residual_ul
    )
    for variable_slice, limit, coefficients in (
        (
            sul,
            static_metrics.overload_area_seconds["ul"],
            seconds / np.maximum(path.safe_ul.reshape(-1), 1e-9),
        ),
        (
            sdl,
            static_metrics.overload_area_seconds["dl"],
            seconds / np.maximum(path.safe_dl.reshape(-1), 1e-9),
        ),
        (
            sn,
            static_metrics.session_overload_area_seconds,
            seconds / np.maximum(path.safe_sessions.reshape(-1), 1e-9),
        ),
        (
            dul,
            static_metrics.dropped_bytes["ul"],
            np.full(load_rows, seconds * 1_000_000 / 8),
        ),
        (
            ddl,
            static_metrics.dropped_bytes["dl"],
            np.full(load_rows, seconds * 1_000_000 / 8),
        ),
    ):
        row = np.zeros(variable_count)
        row[variable_slice] = coefficients
        inequalities.append(csr_matrix(row[None, :]))
        guarded_limit = (
            limit * (1.0 - config.guardrail_margin_fraction)
            if limit > config.guardrail_tolerance else limit
        )
        rhs.append(np.asarray([
            guarded_limit
            + config.guardrail_tolerance * max(1.0, abs(limit))
        ]))

    # Bound every UPF/window's projected UL failure exposure relative to the
    # exact same-state static rollout. This removes zero-loss LP ties that can
    # silently concentrate persistent cohorts on a future failure domain.
    exposure_rows = hstack([
        model.ul,
        csr_matrix((load_rows, variable_count - nx)),
    ], format="csr")
    exposure_limit = (
        static_ul_load * (1.0 + config.max_ul_exposure_increase_fraction)
        - model.residual_ul
    )
    inequalities.append(exposure_rows)
    rhs.append(exposure_limit.reshape(-1))

    terminal_rows: list[csr_matrix] = []
    terminal_rhs: list[float] = []
    last = config.horizon_windows - 1
    for upf_index in range(len(states)):
        row_index = upf_index * config.horizon_windows + last
        for matrix, safe, residual in (
            (model.ul, path.safe_ul, model.residual_ul),
            (model.dl, path.safe_dl, model.residual_dl),
            (model.sessions, path.safe_sessions, model.residual_sessions),
        ):
            capacity = safe[upf_index, last]
            if capacity <= 0:
                continue
            row = np.zeros(variable_count)
            row[:nx] = matrix.getrow(row_index).toarray().ravel()
            row[terminal] = -capacity
            terminal_rows.append(csr_matrix(row[None, :]))
            terminal_rhs.append(-residual[upf_index, last])
    if terminal_rows:
        inequalities.extend(terminal_rows)
        rhs.append(np.asarray(terminal_rhs))

    if config.terminal_failure_exposure_cost + config.n_minus_one_exposure_cost > 0:
        failure_rows: list[csr_matrix] = []
        failure_rhs: list[float] = []
        for upf_index in range(len(states)):
            row_index = upf_index * config.horizon_windows + last
            row = np.zeros(variable_count)
            row[:nx] = model.ul.getrow(row_index).toarray().ravel()
            row[failure_exposure] = -1.0
            failure_rows.append(csr_matrix(row[None, :]))
            failure_rhs.append(-model.residual_ul[upf_index, last])
        inequalities.extend(failure_rows)
        rhs.append(np.asarray(failure_rhs))

    reference_vector = static_vector
    if previous_allocation is not None:
        reference_vector = _allocation_vector(previous_allocation, model, static_vector)
    change_identity = eye(nx, format="csr")
    change_prefix = csr_matrix((nx, variable_count - nx - nx))
    inequalities.extend([
        hstack([change_identity, change_prefix, -change_identity], format="csr"),
        hstack([-change_identity, change_prefix, -change_identity], format="csr"),
    ])
    rhs.extend([reference_vector, -reference_vector])

    if config.max_policy_l1_change_per_group < 2:
        for group in groups:
            row = np.zeros(variable_count)
            for column in model.columns_by_group_window[(group.key.selection_id, 0)]:
                row[change.start + column] = 1.0
            inequalities.append(csr_matrix(row[None, :]))
            rhs.append(np.asarray([config.max_policy_l1_change_per_group]))

    if config.failure_domain_protection_enabled and config.max_failure_domain_share < 1:
        domain_by_upf = {upf.upf_id: upf.zone for upf in scenario.upfs}
        for group in groups:
            domains = sorted({domain_by_upf[upf_id] for upf_id in group.eligible_upfs})
            for window in range(config.horizon_windows):
                for domain in domains:
                    row = np.zeros(variable_count)
                    for column in model.columns_by_group_window[(group.key.selection_id, window)]:
                        if domain_by_upf[model.x_keys[column][2]] == domain:
                            row[column] = 1.0
                    inequalities.append(csr_matrix(row[None, :]))
                    rhs.append(np.asarray([config.max_failure_domain_share]))

    lower = np.zeros(variable_count)
    upper = np.full(variable_count, np.inf)
    healthy_count = {
        (group.key.selection_id, window): sum(
            bool(path.admission_healthy[model.upf_index[upf_id], window])
            for upf_id in group.eligible_upfs
        )
        for group in groups
        for window in range(config.horizon_windows)
    }
    for column, (group_id, window, upf_id) in enumerate(model.x_keys):
        upf_index = model.upf_index[upf_id]
        count = healthy_count[(group_id, window)]
        upper[column] = (
            max(config.max_group_upf_weight, 1.0 / count)
            if path.admission_healthy[upf_index, window]
            else 0.0
        )
    inequality_matrix = vstack(inequalities, format="csr")
    inequality_rhs = np.concatenate(rhs)
    model_nonzeros = equality.nnz + inequality_matrix.nnz
    try:
        solution = linprog(
            objective,
            A_ub=inequality_matrix,
            b_ub=inequality_rhs,
            A_eq=equality,
            b_eq=equality_rhs,
            bounds=np.column_stack((lower, upper)),
            method="highs",
            options={"time_limit": config.timeout_seconds},
        )
    except Exception as error:
        return CohortMPCResult(
            "error", f"HiGHS error: {error}",
            round((time.perf_counter() - started) * 1000), {}, {}, {}, None, known_count,
            survival_audit, variable_count, equality.shape[0],
            inequality_matrix.shape[0], model_nonzeros,
        )
    runtime_ms = round((time.perf_counter() - started) * 1000)
    if not solution.success or solution.x is None:
        status = "timeout" if solution.status == 1 else "infeasible" if solution.status == 2 else "error"
        return CohortMPCResult(
            status, solution.message, runtime_ms, {}, {}, {}, None, known_count,
            survival_audit, variable_count, equality.shape[0],
            inequality_matrix.shape[0], model_nonzeros,
        )

    candidate = np.asarray(solution.x[:nx])
    if config.action_blend_fraction < 1:
        candidate = (
            config.action_blend_fraction * candidate
            + (1.0 - config.action_blend_fraction) * static_vector
        )
    candidate_metrics = _metrics(candidate, static_vector, model, path, scenario, config)
    certificate = _certificate(candidate_metrics, static_metrics, config)
    planned = _allocation_dict(candidate, model)
    first = {
        group.key.selection_id: planned[(group.key.selection_id, 0)]
        for group in groups
    }
    static_planned = _allocation_dict(static_vector, model)
    static_first = {
        group.key.selection_id: static_planned[(group.key.selection_id, 0)]
        for group in groups
    }
    return CohortMPCResult(
        "optimal", solution.message, runtime_ms, first, static_first, planned,
        certificate, known_count, survival_audit, variable_count,
        equality.shape[0], inequality_matrix.shape[0], model_nonzeros,
    )


class _PlanningModel:
    def __init__(
        self,
        scenario: ScenarioConfig,
        groups: Sequence[GroupProfile],
        cohorts: Sequence[ActiveCohort],
        demands: Mapping[str, Sequence[ResidualObservation]],
        horizon: int,
        *,
        survival_by_group: Mapping[str, SurvivalTable] | None = None,
        low_confidence_safety_factor: float = 1.0,
    ) -> None:
        self.horizon = horizon
        self.upf_index = {upf.upf_id: index for index, upf in enumerate(scenario.upfs)}
        self.x_keys: list[tuple[str, int, str]] = []
        self.columns_by_group_window: dict[tuple[str, int], list[int]] = {}
        for group in groups:
            group_id = group.key.selection_id
            for window in range(horizon):
                columns = []
                for upf_id in group.eligible_upfs:
                    columns.append(len(self.x_keys))
                    self.x_keys.append((group_id, window, upf_id))
                self.columns_by_group_window[(group_id, window)] = columns
        rows = len(scenario.upfs) * horizon
        matrix_rows: list[int] = []
        matrix_columns: list[int] = []
        session_data: list[float] = []
        ul_data: list[float] = []
        dl_data: list[float] = []
        group_by_id = {group.key.selection_id: group for group in groups}
        survival = {
            group_id: (
                survival_by_group[group_id].values(horizon)
                if survival_by_group is not None
                else bucket_survival(
                    group.lifetime_steps_min,
                    group.lifetime_steps_max,
                    scenario.decision_interval_steps,
                    horizon,
                )
            )
            for group_id, group in group_by_id.items()
        }
        for column, (group_id, source, upf_id) in enumerate(self.x_keys):
            upf = self.upf_index[upf_id]
            for target in range(source, horizon):
                fraction = survival[group_id][target - source]
                if (
                    survival_by_group is not None
                    and survival_by_group[group_id].confidence == "low"
                ):
                    fraction = min(1.0, fraction * low_confidence_safety_factor)
                if fraction <= 0:
                    continue
                demand = demands[group_id][source]
                matrix_rows.append(upf * horizon + target)
                matrix_columns.append(column)
                session_data.append(demand.surviving_sessions * fraction)
                ul_data.append(demand.ul_mbps * fraction)
                dl_data.append(demand.dl_mbps * fraction)
        shape = (rows, len(self.x_keys))
        coordinates = (matrix_rows, matrix_columns)
        self.sessions = coo_matrix((session_data, coordinates), shape=shape).tocsr()
        self.ul = coo_matrix((ul_data, coordinates), shape=shape).tocsr()
        self.dl = coo_matrix((dl_data, coordinates), shape=shape).tocsr()
        self.residual_sessions = np.zeros((len(scenario.upfs), horizon))
        self.residual_ul = np.zeros_like(self.residual_sessions)
        self.residual_dl = np.zeros_like(self.residual_sessions)
        bucket_steps = scenario.decision_interval_steps
        for cohort in cohorts:
            upf = self.upf_index[cohort.upf_id]
            for window in range(horizon):
                active_steps = min(
                    bucket_steps,
                    max(0, cohort.remaining_steps - window * bucket_steps),
                )
                fraction = active_steps / bucket_steps
                if fraction <= 0:
                    break
                sessions = cohort.sessions * fraction
                self.residual_sessions[upf, window] += sessions
                self.residual_ul[upf, window] += sessions * cohort.ul_mbps_per_session
                self.residual_dl[upf, window] += sessions * cohort.dl_mbps_per_session


def bucket_survival(
    lifetime_min: int,
    lifetime_max: int,
    bucket_steps: int,
    bucket_count: int,
) -> np.ndarray:
    """Expected active fraction by bucket lag for uniform arrivals/lifetimes."""

    lifetime_count = lifetime_max - lifetime_min + 1
    result = np.zeros(bucket_count, dtype=float)
    offsets = np.arange(bucket_steps)
    for lag in range(bucket_count):
        age = lag * bucket_steps + offsets[:, None] - offsets[None, :] + 1
        probability = np.where(
            age <= 0,
            0.0,
            np.where(
                age <= lifetime_min,
                1.0,
                np.maximum(0, lifetime_max - age + 1) / lifetime_count,
            ),
        )
        result[lag] = float(probability.mean())
        if lag > 0 and result[lag] == 0:
            break
    return result


def _known_capacity_path(
    scenario: ScenarioConfig,
    states: Sequence[UPFState],
    current_step: int,
    horizon: int,
    settings: CohortMPCConfig,
) -> tuple[_CapacityPath, int]:
    index = {upf.upf_id: position for position, upf in enumerate(scenario.upfs)}
    state_by_id = {state.upf_id: state for state in states}
    ul_factor = np.array([
        state_by_id[upf.upf_id].capacity_mbps.ul / upf.capacity_ul_mbps
        for upf in scenario.upfs
    ])
    dl_factor = np.array([
        state_by_id[upf.upf_id].capacity_mbps.dl / upf.capacity_dl_mbps
        for upf in scenario.upfs
    ])
    healthy = np.array([
        state_by_id[upf.upf_id].health in {"healthy", "degraded"}
        for upf in scenario.upfs
    ])
    known = [
        event for event in scenario.events
        if event.event_type in {"capacity_factor", "health"}
        and event.step > current_step
        and event.known_at_step is not None
        and event.known_at_step <= current_step
    ]
    by_step: dict[int, list[ScenarioEvent]] = {}
    for event in known:
        shifted_step = event.step
        if event.event_type == "capacity_factor":
            factors = [value for value in (event.ul_factor, event.dl_factor) if value is not None]
            is_recovery = bool(factors) and all(value >= 0.999999 for value in factors)
            offset = (
                settings.scheduled_event_end_offset_windows
                if is_recovery else settings.scheduled_event_start_offset_windows
            )
            shifted_step += offset * scenario.decision_interval_steps
        by_step.setdefault(max(current_step + 1, shifted_step), []).append(event)
    shape = (len(scenario.upfs), horizon)
    safe_ul = np.full(shape, np.inf)
    safe_dl = np.full(shape, np.inf)
    physical_ul = np.full(shape, np.inf)
    physical_dl = np.full(shape, np.inf)
    safe_sessions = np.full(shape, np.inf)
    admission = np.ones(shape, dtype=bool)
    bucket_steps = scenario.decision_interval_steps
    for window in range(horizon):
        for offset in range(bucket_steps):
            step = current_step + window * bucket_steps + offset
            for event in by_step.get(step, ()):
                upf = index[event.upf_id or ""]
                if event.event_type == "capacity_factor":
                    if event.ul_factor is not None:
                        ul_factor[upf] = (
                            event.ul_factor if event.ul_factor >= 0.999999
                            else min(1.0, max(0.0, event.ul_factor + settings.scheduled_capacity_factor_bias))
                        )
                    if event.dl_factor is not None:
                        dl_factor[upf] = (
                            event.dl_factor if event.dl_factor >= 0.999999
                            else min(1.0, max(0.0, event.dl_factor + settings.scheduled_capacity_factor_bias))
                        )
                else:
                    healthy[upf] = event.health in {"healthy", "degraded"}
            for upf_index, profile in enumerate(scenario.upfs):
                available = healthy[upf_index]
                ul = profile.capacity_ul_mbps * ul_factor[upf_index] if available else 0.0
                dl = profile.capacity_dl_mbps * dl_factor[upf_index] if available else 0.0
                physical_ul[upf_index, window] = min(physical_ul[upf_index, window], ul)
                physical_dl[upf_index, window] = min(physical_dl[upf_index, window], dl)
                safe_ul[upf_index, window] = min(
                    safe_ul[upf_index, window], ul * profile.safe_utilization_ul
                )
                safe_dl[upf_index, window] = min(
                    safe_dl[upf_index, window], dl * profile.safe_utilization_dl
                )
                safe_sessions[upf_index, window] = min(
                    safe_sessions[upf_index, window],
                    profile.session_capacity * profile.session_safe_utilization if available else 0.0,
                )
                admission[upf_index, window] &= available
    return _CapacityPath(
        safe_ul, safe_dl, physical_ul, physical_dl, safe_sessions, admission
    ), len(known)


def _static_plan(
    groups: Sequence[GroupProfile], path: _CapacityPath, model: _PlanningModel
) -> np.ndarray | None:
    vector = np.zeros(len(model.x_keys))
    index_by_key = {key: column for column, key in enumerate(model.x_keys)}
    for group in groups:
        group_id = group.key.selection_id
        for window in range(model.horizon):
            scores = {
                upf_id: path.safe_ul[model.upf_index[upf_id], window]
                + path.safe_dl[model.upf_index[upf_id], window]
                for upf_id in group.eligible_upfs
                if path.admission_healthy[model.upf_index[upf_id], window]
            }
            total = sum(scores.values())
            if total <= 0:
                return None
            for upf_id, score in scores.items():
                vector[index_by_key[(group_id, window, upf_id)]] = score / total
    return vector


def _allocation_dict(
    vector: np.ndarray, model: _PlanningModel
) -> dict[tuple[str, int], dict[str, float]]:
    result: dict[tuple[str, int], dict[str, float]] = {}
    for key, columns in model.columns_by_group_window.items():
        weights = {
            model.x_keys[column][2]: float(vector[column])
            for column in columns
            if vector[column] > 1e-9
        }
        total = sum(weights.values())
        result[key] = {
            upf_id: value / total for upf_id, value in weights.items()
        } if total > 0 else {}
    return result


def _allocation_vector(
    allocation: Mapping[str, Mapping[str, float]],
    model: _PlanningModel,
    fallback: np.ndarray,
) -> np.ndarray:
    """Expand a first-epoch policy across the horizon for churn accounting."""

    vector = fallback.copy()
    for (group_id, _window), columns in model.columns_by_group_window.items():
        weights = allocation.get(group_id)
        if not weights:
            continue
        eligible = {model.x_keys[column][2] for column in columns}
        total = sum(float(value) for upf_id, value in weights.items() if upf_id in eligible)
        if total <= 0:
            continue
        for column in columns:
            vector[column] = float(weights.get(model.x_keys[column][2], 0.0)) / total
    return vector


def _metrics(
    vector: np.ndarray,
    static: np.ndarray,
    model: _PlanningModel,
    path: _CapacityPath,
    scenario: ScenarioConfig,
    config: CohortMPCConfig,
) -> MPCMetrics:
    seconds = scenario.step_seconds * scenario.decision_interval_steps
    loads = {}
    overload = {}
    dropped = {}
    drop_area = 0.0
    for direction, matrix, residual, safe, physical, nominal in (
        (
            "ul", model.ul, model.residual_ul, path.safe_ul, path.physical_ul,
            np.array([upf.capacity_ul_mbps for upf in scenario.upfs])[:, None],
        ),
        (
            "dl", model.dl, model.residual_dl, path.safe_dl, path.physical_dl,
            np.array([upf.capacity_dl_mbps for upf in scenario.upfs])[:, None],
        ),
    ):
        load = np.asarray(matrix @ vector).reshape(safe.shape) + residual
        loads[direction] = load
        excess = np.maximum(0.0, load - safe)
        overload[direction] = float(
            np.sum(excess / np.maximum(safe, 1e-9)) * seconds
        )
        physical_excess = np.maximum(0.0, load - physical)
        dropped[direction] = float(
            np.sum(physical_excess) * seconds * 1_000_000 / 8
        )
        drop_area += float(np.sum(physical_excess / nominal) * seconds)
    session_load = (
        np.asarray(model.sessions @ vector).reshape(path.safe_sessions.shape)
        + model.residual_sessions
    )
    session_overload = float(
        np.sum(
            np.maximum(0.0, session_load - path.safe_sessions)
            / np.maximum(path.safe_sessions, 1e-9)
        ) * seconds
    )
    last = model.horizon - 1
    terminal_ratios = []
    for load, safe in (
        (loads["ul"], path.safe_ul),
        (loads["dl"], path.safe_dl),
        (session_load, path.safe_sessions),
    ):
        terminal_ratios.extend(
            (
                load[:, last] / np.maximum(safe[:, last], 1e-9)
            ).tolist()
        )
    terminal_max = max(terminal_ratios, default=0.0)
    terminal_max_ul = float(np.max(loads["ul"][:, last]))
    deviation = float(np.sum(np.abs(vector - static)))
    score = (
        config.overload_cost
        * (overload["ul"] + overload["dl"] + session_overload)
        + config.drop_cost * drop_area
        + config.terminal_exposure_cost * terminal_max
        + config.terminal_failure_exposure_cost
        * terminal_max_ul
        / max(1e-9, float(np.mean([
            upf.capacity_ul_mbps for upf in scenario.upfs
        ])))
        + config.static_deviation_cost * deviation / max(1, len(vector))
    )
    return MPCMetrics(
        overload,
        dropped,
        session_overload,
        terminal_max,
        terminal_max_ul,
        deviation,
        score,
    )


def _certificate(
    candidate: MPCMetrics,
    static: MPCMetrics,
    config: CohortMPCConfig,
) -> MPCCertificate:
    tolerance = config.guardrail_tolerance
    def within(value: float, baseline: float) -> bool:
        guarded = (
            baseline * (1.0 - config.guardrail_margin_fraction)
            if baseline > tolerance else baseline
        )
        return value <= guarded + tolerance * max(1.0, abs(baseline))

    guardrails = (
        within(
            candidate.overload_area_seconds["ul"],
            static.overload_area_seconds["ul"],
        )
        and within(
            candidate.overload_area_seconds["dl"],
            static.overload_area_seconds["dl"],
        )
        and within(
            candidate.session_overload_area_seconds,
            static.session_overload_area_seconds,
        )
        and all(
            within(candidate.dropped_bytes[direction], static.dropped_bytes[direction])
            for direction in ("ul", "dl")
        )
    )
    improvement = static.score - candidate.score
    relative = improvement / max(abs(static.score), 1e-12)
    static_ul = static.overload_area_seconds["ul"]
    candidate_ul = candidate.overload_area_seconds["ul"]
    ul_relative = (
        (static_ul - candidate_ul) / static_ul
        if static_ul > tolerance else 0.0
    )
    accepted = (
        guardrails
        and relative >= config.min_relative_improvement
        and ul_relative >= config.min_ul_overload_relative_improvement
    )
    if not guardrails:
        reason = "same_state_guardrail_regression"
    elif relative < config.min_relative_improvement:
        reason = "insufficient_same_state_improvement"
    elif ul_relative < config.min_ul_overload_relative_improvement:
        reason = "insufficient_modeled_ul_improvement"
    else:
        reason = "robust_same_state_improvement"
    return MPCCertificate(
        accepted, reason, relative, ul_relative, candidate, static
    )
