from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from schemas import (
    ConstraintSlack,
    Fallback,
    Forecast,
    GroupKey,
    Policy,
    PolicyGroup,
    SolverReport,
    UPFState,
)
from schemas.common import parse_utc


@dataclass(frozen=True, slots=True)
class OptimizationConfig:
    planning_quantile: str = "p95"
    max_latency_ms: float | None = None
    overload_penalty: float = 1_000_000.0
    utilization_cost: float = 1.0
    locality_cost: float = 0.001
    churn_cost: float = 0.01
    slack_tolerance: float = 1e-7
    timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.planning_quantile not in {"p50", "p90", "p95"}:
            raise ValueError("planning_quantile must be p50, p90, or p95")
        if self.max_latency_ms is not None and self.max_latency_ms < 0:
            raise ValueError("max_latency_ms must be non-negative")
        if min(self.overload_penalty, self.utilization_cost, self.locality_cost, self.churn_cost) < 0:
            raise ValueError("objective costs must be non-negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    status: str
    policy: Policy | None
    message: str
    projected_ul_mbps_by_upf: dict[str, float]
    projected_dl_mbps_by_upf: dict[str, float]
    projected_sessions_by_upf: dict[str, float]
    max_safe_utilization: float | None


def _q(value, quantile: str) -> float:
    selected = getattr(value, quantile)
    if selected is None:
        raise ValueError(f"forecast does not provide {quantile}")
    return float(selected)


def _validate_inputs(forecasts: list[Forecast], states: list[UPFState]) -> None:
    if not forecasts or not states:
        raise ValueError("forecasts and UPF states are required")
    targets = {(item.target_window.start, item.target_window.end) for item in forecasts}
    if len(targets) != 1:
        raise ValueError("all forecasts must target the same window")
    horizons = {item.horizon_steps for item in forecasts}
    if len(horizons) != 1:
        raise ValueError("all forecasts must use the same horizon")
    group_ids = [item.group.selection_id for item in forecasts]
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("duplicate group forecasts")
    state_ids = [item.upf_id for item in states]
    if len(state_ids) != len(set(state_ids)):
        raise ValueError("duplicate UPF states")


def solve_allocation(
    forecasts: Iterable[Forecast],
    upf_states: Iterable[UPFState],
    *,
    created_at: datetime,
    policy_version: int,
    previous_policy: Policy | None = None,
    config: OptimizationConfig | None = None,
) -> OptimizationResult:
    """Solve the v1 LP and return no policy for structural/solver failures."""

    try:
        from scipy.optimize import linprog
    except ImportError:
        return OptimizationResult(
            status="error", policy=None, message="SciPy with HiGHS is not installed",
            projected_ul_mbps_by_upf={}, projected_dl_mbps_by_upf={},
            projected_sessions_by_upf={}, max_safe_utilization=None,
        )

    settings = config or OptimizationConfig()
    forecasts = list(forecasts)
    states = list(upf_states)
    _validate_inputs(forecasts, states)
    created_at = parse_utc(created_at)
    state_by_id = {state.upf_id: state for state in states}
    upf_ids = sorted(state_by_id)
    group_ids = [forecast.group.selection_id for forecast in forecasts]
    forecast_by_group = {forecast.group.selection_id: forecast for forecast in forecasts}

    allowed: dict[str, list[str]] = {}
    for forecast in forecasts:
        group_id = forecast.group.selection_id
        allowed[group_id] = []
        for upf_id in upf_ids:
            state = state_by_id[upf_id]
            latency = state.path_latency_ms_by_zone.get(forecast.group.zone)
            if state.health not in {"healthy", "degraded"}:
                continue
            if group_id not in state.eligible_groups:
                continue
            if latency is None:
                continue
            if settings.max_latency_ms is not None and latency > settings.max_latency_ms:
                continue
            allowed[group_id].append(upf_id)
        if not allowed[group_id]:
            return OptimizationResult(
                status="infeasible", policy=None,
                message=f"no healthy eligible UPF for group {group_id}",
                projected_ul_mbps_by_upf={}, projected_dl_mbps_by_upf={},
                projected_sessions_by_upf={}, max_safe_utilization=None,
            )

    # Residual load is a global UPF forecast repeated in group records. Take
    # the maximum supplied value to stay conservative without double-counting.
    residual_ul = {upf_id: 0.0 for upf_id in upf_ids}
    residual_dl = {upf_id: 0.0 for upf_id in upf_ids}
    residual_sessions = {upf_id: 0.0 for upf_id in upf_ids}
    for forecast in forecasts:
        for item in forecast.existing_load_by_upf:
            if item.upf_id not in state_by_id:
                raise ValueError(f"forecast references unknown UPF {item.upf_id}")
            residual_ul[item.upf_id] = max(residual_ul[item.upf_id], _q(item.ul_mbps, settings.planning_quantile))
            residual_dl[item.upf_id] = max(residual_dl[item.upf_id], _q(item.dl_mbps, settings.planning_quantile))
            residual_sessions[item.upf_id] = max(
                residual_sessions[item.upf_id],
                _q(item.surviving_sessions, settings.planning_quantile),
            )

    p_keys = [(group_id, upf_id) for group_id in group_ids for upf_id in allowed[group_id]]
    p_index = {key: index for index, key in enumerate(p_keys)}
    cursor = len(p_keys)
    z_index = cursor
    cursor += 1
    slack_ul_index = {upf_id: cursor + i for i, upf_id in enumerate(upf_ids)}
    cursor += len(upf_ids)
    slack_dl_index = {upf_id: cursor + i for i, upf_id in enumerate(upf_ids)}
    cursor += len(upf_ids)
    slack_n_index = {upf_id: cursor + i for i, upf_id in enumerate(upf_ids)}
    cursor += len(upf_ids)
    change_index = {key: cursor + i for i, key in enumerate(p_keys)}
    variable_count = cursor + len(p_keys)

    objective = [0.0] * variable_count
    objective[z_index] = settings.utilization_cost
    for group_id, upf_id in p_keys:
        latency = state_by_id[upf_id].path_latency_ms_by_zone[forecast_by_group[group_id].group.zone]
        objective[p_index[(group_id, upf_id)]] = settings.locality_cost * latency
        objective[change_index[(group_id, upf_id)]] = settings.churn_cost
    for upf_id in upf_ids:
        safe = state_by_id[upf_id].safe_capacity_mbps
        safe_sessions = state_by_id[upf_id].safe_session_capacity
        objective[slack_ul_index[upf_id]] = settings.overload_penalty / safe.ul
        objective[slack_dl_index[upf_id]] = settings.overload_penalty / safe.dl
        objective[slack_n_index[upf_id]] = settings.overload_penalty / safe_sessions

    equalities: list[list[float]] = []
    equality_rhs: list[float] = []
    for group_id in group_ids:
        row = [0.0] * variable_count
        for upf_id in allowed[group_id]:
            row[p_index[(group_id, upf_id)]] = 1.0
        equalities.append(row)
        equality_rhs.append(1.0)

    inequalities: list[list[float]] = []
    inequality_rhs: list[float] = []
    for upf_id in upf_ids:
        state = state_by_id[upf_id]
        for direction, residual, slack_indexes in (
            ("ul", residual_ul, slack_ul_index),
            ("dl", residual_dl, slack_dl_index),
        ):
            row = [0.0] * variable_count
            for group_id in group_ids:
                if upf_id in allowed[group_id]:
                    forecast = forecast_by_group[group_id]
                    demand = forecast.new_load_ul_mbps if direction == "ul" else forecast.new_load_dl_mbps
                    row[p_index[(group_id, upf_id)]] = _q(demand, settings.planning_quantile)
            safe_capacity = getattr(state.safe_capacity_mbps, direction)
            row[z_index] = -safe_capacity
            row[slack_indexes[upf_id]] = -1.0
            inequalities.append(row)
            inequality_rhs.append(-residual[upf_id])

        row = [0.0] * variable_count
        for group_id in group_ids:
            if upf_id in allowed[group_id]:
                row[p_index[(group_id, upf_id)]] = _q(
                    forecast_by_group[group_id].new_session_count,
                    settings.planning_quantile,
                )
        row[z_index] = -state.safe_session_capacity
        row[slack_n_index[upf_id]] = -1.0
        inequalities.append(row)
        inequality_rhs.append(-residual_sessions[upf_id])

    for group_id, upf_id in p_keys:
        previous = 0.0
        if previous_policy is not None:
            try:
                previous = previous_policy.weights_for(forecast_by_group[group_id].group).get(upf_id, 0.0)
            except KeyError:
                previous = 0.0
        # p - c <= previous; -p - c <= -previous
        row_positive = [0.0] * variable_count
        row_positive[p_index[(group_id, upf_id)]] = 1.0
        row_positive[change_index[(group_id, upf_id)]] = -1.0
        inequalities.append(row_positive)
        inequality_rhs.append(previous)
        row_negative = [0.0] * variable_count
        row_negative[p_index[(group_id, upf_id)]] = -1.0
        row_negative[change_index[(group_id, upf_id)]] = -1.0
        inequalities.append(row_negative)
        inequality_rhs.append(-previous)

    bounds = [(0.0, None)] * variable_count
    for index in p_index.values():
        bounds[index] = (0.0, 1.0)
    bounds[z_index] = (0.0, 1.0)

    started = time.perf_counter()
    try:
        solution = linprog(
            objective,
            A_ub=inequalities,
            b_ub=inequality_rhs,
            A_eq=equalities,
            b_eq=equality_rhs,
            bounds=bounds,
            method="highs",
            options={"time_limit": settings.timeout_seconds},
        )
    except Exception as error:
        runtime_ms = round((time.perf_counter() - started) * 1000)
        return OptimizationResult(
            status="error", policy=None, message=f"HiGHS error after {runtime_ms} ms: {error}",
            projected_ul_mbps_by_upf={}, projected_dl_mbps_by_upf={},
            projected_sessions_by_upf={}, max_safe_utilization=None,
        )
    runtime_ms = round((time.perf_counter() - started) * 1000)
    if not solution.success or solution.x is None:
        status = "timeout" if solution.status == 1 else "infeasible" if solution.status == 2 else "error"
        return OptimizationResult(
            status=status, policy=None, message=solution.message,
            projected_ul_mbps_by_upf={}, projected_dl_mbps_by_upf={},
            projected_sessions_by_upf={}, max_safe_utilization=None,
        )

    weights = {
        group_id: {upf_id: float(solution.x[p_index[(group_id, upf_id)]]) for upf_id in allowed[group_id]}
        for group_id in group_ids
    }
    # Remove solver dust and normalize only a successfully solved equality row.
    for group_weights in weights.values():
        for upf_id, value in list(group_weights.items()):
            group_weights[upf_id] = 0.0 if abs(value) < settings.slack_tolerance else value
        total = sum(group_weights.values())
        for upf_id in group_weights:
            group_weights[upf_id] /= total

    projected_ul = dict(residual_ul)
    projected_dl = dict(residual_dl)
    projected_sessions = dict(residual_sessions)
    for group_id, group_weights in weights.items():
        forecast = forecast_by_group[group_id]
        for upf_id, weight in group_weights.items():
            projected_ul[upf_id] += weight * _q(forecast.new_load_ul_mbps, settings.planning_quantile)
            projected_dl[upf_id] += weight * _q(forecast.new_load_dl_mbps, settings.planning_quantile)
            projected_sessions[upf_id] += weight * _q(forecast.new_session_count, settings.planning_quantile)

    slack_ul = {
        upf_id: max(0.0, projected_ul[upf_id] - state_by_id[upf_id].safe_capacity_mbps.ul)
        for upf_id in upf_ids
    }
    slack_dl = {
        upf_id: max(0.0, projected_dl[upf_id] - state_by_id[upf_id].safe_capacity_mbps.dl)
        for upf_id in upf_ids
    }
    slack_sessions = {
        upf_id: max(0.0, projected_sessions[upf_id] - state_by_id[upf_id].safe_session_capacity)
        for upf_id in upf_ids
    }
    has_slack = any(
        value > settings.slack_tolerance
        for category in (slack_ul, slack_dl, slack_sessions)
        for value in category.values()
    )
    status = "feasible_with_slack" if has_slack else "optimal"
    target = forecasts[0].target_window
    forecast_digest = "+".join(sorted(item.forecast_id for item in forecasts))
    policy = Policy(
        policy_id=f"predictive:{policy_version}:{forecast_digest[:16]}",
        policy_version=policy_version,
        created_at=created_at,
        validity=target,
        forecast_id=forecast_digest,
        upf_state_time=max(state.measurement_time for state in states),
        solver=SolverReport(name="highs", status=status, runtime_ms=runtime_ms),
        constraint_slack=ConstraintSlack(
            ul_mbps_by_upf=slack_ul,
            dl_mbps_by_upf=slack_dl,
            sessions_by_upf=slack_sessions,
        ),
        groups=[
            PolicyGroup(
                key=GroupKey(forecast_by_group[group_id].group.zone, forecast_by_group[group_id].group.dnn, forecast_by_group[group_id].group.snssai),
                weights=weights[group_id],
            )
            for group_id in group_ids
        ],
        fallback=Fallback(),
        validator_version="pending-independent-validation",
    )
    return OptimizationResult(
        status=status,
        policy=policy,
        message=solution.message,
        projected_ul_mbps_by_upf=projected_ul,
        projected_dl_mbps_by_upf=projected_dl,
        projected_sessions_by_upf=projected_sessions,
        max_safe_utilization=float(solution.x[z_index]),
    )
