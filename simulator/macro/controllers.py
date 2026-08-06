from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from forecasting import DemandObservation, ForecastingError, MovingAverageForecaster, ResidualObservation
from optimization import OptimizationConfig, solve_allocation

from schemas import (
    ConstraintSlack,
    ExistingLoad,
    Forecast,
    Fallback,
    GroupKey,
    Policy,
    PolicyGroup,
    Quantiles,
    SolverReport,
    TimeWindow,
    UPFState,
)
from steering import (
    PolicyGate,
    PolicyGateConfig,
    PolicyGateDecision,
    PolicyValidationError,
    ValidationConfig,
    validate_policy,
)

from .config import GroupProfile, ScenarioConfig


@dataclass(frozen=True, slots=True)
class ControlContext:
    history_by_group: dict[str, tuple[DemandObservation, ...]] = field(default_factory=dict)
    residual_by_upf: dict[str, ResidualObservation] = field(default_factory=dict)
    oracle_new_by_group: dict[str, ResidualObservation] = field(default_factory=dict)


class Controller(Protocol):
    name: str

    def build_policy(
        self,
        config: ScenarioConfig,
        groups: tuple[GroupProfile, ...],
        upf_states: list[UPFState],
        created_at: datetime,
        version: int,
        context: ControlContext | None = None,
    ) -> Policy: ...


class StaticCapacityController:
    """Static baseline weighted by the current directional safe envelope."""

    name = "static-capacity-v1"

    def build_policy(
        self,
        config: ScenarioConfig,
        groups: tuple[GroupProfile, ...],
        upf_states: list[UPFState],
        created_at: datetime,
        version: int,
        context: ControlContext | None = None,
    ) -> Policy:
        state_by_id = {state.upf_id: state for state in upf_states}
        policy_groups: list[PolicyGroup] = []
        for group in groups:
            scores: dict[str, float] = {}
            for upf_id in group.eligible_upfs:
                state = state_by_id[upf_id]
                if state.health not in {"healthy", "degraded"}:
                    continue
                safe = state.safe_capacity_mbps
                scores[upf_id] = safe.ul + safe.dl
            total = sum(scores.values())
            if total > 0:
                policy_groups.append(
                    PolicyGroup(key=GroupKey(group.key.zone, group.key.dnn, group.key.snssai), weights={upf_id: score / total for upf_id, score in scores.items()})
                )

        if not policy_groups:
            # A Policy cannot pretend structural infeasibility is a normalized allocation.
            raise RuntimeError("no healthy eligible UPF exists for any selection group")

        duration = timedelta(seconds=config.step_seconds * config.decision_interval_steps)
        return Policy(
            policy_id=f"{config.scenario_id}:static:{version}",
            policy_version=version,
            created_at=created_at,
            validity=TimeWindow(start=created_at, end=created_at + duration),
            forecast_id="baseline:no-forecast",
            upf_state_time=created_at,
            solver=SolverReport(name=self.name, status="optimal", runtime_ms=0),
            constraint_slack=ConstraintSlack(),
            groups=policy_groups,
            fallback=Fallback(used=True, reason="static_capacity_weighted"),
            validator_version="contract-only/0.1",
        )


class ReactiveThresholdController:
    """Reactive baseline using only residual load observed at decision time."""

    name = "reactive-threshold-v1"

    def __init__(self, threshold: float = 0.8) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        self.threshold = threshold

    def build_policy(
        self,
        config: ScenarioConfig,
        groups: tuple[GroupProfile, ...],
        upf_states: list[UPFState],
        created_at: datetime,
        version: int,
        context: ControlContext | None = None,
    ) -> Policy:
        context = context or ControlContext()
        state_by_id = {state.upf_id: state for state in upf_states}
        policy_groups: list[PolicyGroup] = []
        for group in groups:
            headroom: dict[str, float] = {}
            utilization: dict[str, float] = {}
            for upf_id in group.eligible_upfs:
                state = state_by_id[upf_id]
                if state.health not in {"healthy", "degraded"}:
                    continue
                residual = context.residual_by_upf.get(upf_id, ResidualObservation(0, 0, 0))
                safe = state.safe_capacity_mbps
                utilization[upf_id] = max(
                    residual.ul_mbps / safe.ul,
                    residual.dl_mbps / safe.dl,
                    residual.surviving_sessions / state.safe_session_capacity,
                )
                headroom[upf_id] = max(
                    0.0,
                    min(
                        1.0 - residual.ul_mbps / safe.ul,
                        1.0 - residual.dl_mbps / safe.dl,
                        1.0 - residual.surviving_sessions / state.safe_session_capacity,
                    ),
                )
            preferred = {upf_id: value for upf_id, value in headroom.items() if utilization[upf_id] < self.threshold and value > 0}
            if not preferred and utilization:
                least_loaded = min(utilization, key=lambda upf_id: (utilization[upf_id], upf_id))
                preferred = {least_loaded: 1.0}
            total = sum(preferred.values())
            if total > 0:
                policy_groups.append(PolicyGroup(
                    key=GroupKey(group.key.zone, group.key.dnn, group.key.snssai),
                    weights={upf_id: value / total for upf_id, value in preferred.items()},
                ))
        if not policy_groups:
            raise RuntimeError("no healthy eligible UPF exists for any selection group")
        duration = timedelta(seconds=config.step_seconds * config.decision_interval_steps)
        return Policy(
            policy_id=f"{config.scenario_id}:reactive:{version}",
            policy_version=version,
            created_at=created_at,
            validity=TimeWindow(created_at, created_at + duration),
            forecast_id="baseline:no-forecast",
            upf_state_time=max(state.measurement_time for state in upf_states),
            solver=SolverReport(self.name, "optimal", 0),
            constraint_slack=ConstraintSlack(),
            groups=policy_groups,
            fallback=Fallback(),
            validator_version="reactive-controller/1.0",
        )


class PredictiveHiGHSController:
    name = "predictive-highs-v1"

    def __init__(
        self,
        history_windows: int = 6,
        *,
        allow_slack: bool = True,
        forecaster: Any | None = None,
        gate_config: PolicyGateConfig | None = None,
    ) -> None:
        self.forecaster = forecaster or MovingAverageForecaster(history_windows)
        self.allow_slack = allow_slack
        self.gate = PolicyGate(gate_config)
        self._previous_policy: Policy | None = None
        self._fallback = StaticCapacityController()
        self.last_forecasts: list[Forecast] = []
        self.last_optimization: Any | None = None
        self.last_candidate: Policy | None = None
        self.last_gate_decision: PolicyGateDecision | None = None

    def build_policy(
        self,
        config: ScenarioConfig,
        groups: tuple[GroupProfile, ...],
        upf_states: list[UPFState],
        created_at: datetime,
        version: int,
        context: ControlContext | None = None,
    ) -> Policy:
        context = context or ControlContext()
        duration = timedelta(seconds=config.step_seconds * config.decision_interval_steps)
        target = TimeWindow(created_at, created_at + duration)
        forecasts = []
        try:
            for group in groups:
                history = context.history_by_group.get(group.key.selection_id, ())
                predicted = self.forecaster.predict(
                    history,
                    issued_at=created_at,
                    target_window=target,
                )
                predicted.existing_load_by_upf = [
                    ExistingLoad(
                        upf_id=upf_id,
                        surviving_sessions=Quantiles(value.surviving_sessions, value.surviving_sessions, value.surviving_sessions),
                        ul_mbps=Quantiles(value.ul_mbps, value.ul_mbps, value.ul_mbps),
                        dl_mbps=Quantiles(value.dl_mbps, value.dl_mbps, value.dl_mbps),
                    )
                    for upf_id, value in sorted(context.residual_by_upf.items())
                ]
                forecasts.append(predicted)
        except ForecastingError:
            return self._fallback_policy(config, groups, upf_states, created_at, version, "insufficient_forecast_history")

        self.last_forecasts = forecasts
        result = solve_allocation(
            forecasts,
            upf_states,
            created_at=created_at,
            policy_version=version,
            previous_policy=self._previous_policy,
            config=OptimizationConfig(),
        )
        self.last_optimization = result
        if result.policy is None or (result.status == "feasible_with_slack" and not self.allow_slack):
            return self._fallback_policy(config, groups, upf_states, created_at, version, result.status)
        self.last_candidate = result.policy
        try:
            validate_policy(
                result.policy,
                forecasts,
                upf_states,
                activation_time=created_at,
                previous_policy=self._previous_policy,
                config=ValidationConfig(allow_feasible_with_slack=self.allow_slack),
            )
        except PolicyValidationError as error:
            return self._fallback_policy(
                config, groups, upf_states, created_at, version, f"validation:{error}"
            )
        candidate_objective = result.max_safe_utilization or 0.0
        current_objective = (
            _operating_index(self._previous_policy.groups, forecasts, upf_states)
            if self._previous_policy is not None else None
        )
        decision = self.gate.evaluate(
            result.policy,
            epoch=version,
            candidate_objective=candidate_objective,
            states=upf_states,
            current=self._previous_policy,
            current_objective=current_objective,
        )
        self.last_gate_decision = decision
        if decision.applied or self._previous_policy is None:
            self._previous_policy = result.policy
            return result.policy
        retained = self._roll_previous_policy(
            config, created_at, version, forecasts, upf_states, decision.reason
        )
        self._previous_policy = retained
        return retained

    def _roll_previous_policy(
        self,
        config: ScenarioConfig,
        created_at: datetime,
        version: int,
        forecasts: list[Forecast],
        upf_states: list[UPFState],
        reason: str,
    ) -> Policy:
        assert self._previous_policy is not None
        duration = timedelta(seconds=config.step_seconds * config.decision_interval_steps)
        projected = _project(self._previous_policy.groups, forecasts, upf_states)
        slack = _slack(projected, upf_states)
        has_slack = any(value > 1e-7 for values in slack for value in values.values())
        retained = Policy(
            policy_id=f"{config.scenario_id}:retained:{version}",
            policy_version=version,
            created_at=created_at,
            validity=TimeWindow(created_at, created_at + duration),
            forecast_id="+".join(sorted(item.forecast_id for item in forecasts)),
            upf_state_time=max(state.measurement_time for state in upf_states),
            solver=SolverReport(self.name, "feasible_with_slack" if has_slack else "optimal", 0),
            constraint_slack=ConstraintSlack(*slack),
            groups=[
                PolicyGroup(item.key, dict(item.weights))
                for item in self._previous_policy.groups
            ],
            fallback=Fallback(
                used=True,
                reason=f"policy_gate:{reason}",
                source_policy_id=self._previous_policy.policy_id,
            ),
            validator_version="policy-gate/1.0",
        )
        validate_policy(
            retained,
            forecasts,
            upf_states,
            activation_time=created_at,
            previous_policy=self._previous_policy,
            config=ValidationConfig(allow_feasible_with_slack=self.allow_slack),
        )
        return retained

    def _fallback_policy(
        self,
        config: ScenarioConfig,
        groups: tuple[GroupProfile, ...],
        upf_states: list[UPFState],
        created_at: datetime,
        version: int,
        reason: str,
    ) -> Policy:
        self.last_forecasts = []
        self.last_optimization = None
        self.last_candidate = None
        self.last_gate_decision = None
        previous = self._previous_policy
        if previous is not None and _policy_routes_safely(previous, upf_states):
            duration = timedelta(seconds=config.step_seconds * config.decision_interval_steps)
            retained = Policy(
                policy_id=f"{config.scenario_id}:retained-fallback:{version}",
                policy_version=version,
                created_at=created_at,
                validity=TimeWindow(created_at, created_at + duration),
                forecast_id="fallback:no-current-forecast",
                upf_state_time=max(state.measurement_time for state in upf_states),
                solver=SolverReport(self.name, "error", 0),
                constraint_slack=ConstraintSlack(),
                groups=[PolicyGroup(item.key, dict(item.weights)) for item in previous.groups],
                fallback=Fallback(True, reason, previous.policy_id),
                validator_version="last-safe-retention/1.0",
            )
            self._previous_policy = retained
            return retained
        policy = self._fallback.build_policy(config, groups, upf_states, created_at, version)
        policy.policy_id = f"{config.scenario_id}:predictive-fallback:{version}"
        policy.fallback = Fallback(
            used=True,
            reason=f"emergency_safe_fallback:{reason}" if previous is not None else reason,
            source_policy_id=previous.policy_id if previous is not None else None,
        )
        if previous is not None:
            self.last_candidate = policy
            self.last_gate_decision = self.gate.evaluate(
                policy,
                epoch=version,
                candidate_objective=0.0,
                states=upf_states,
                current=previous,
                current_objective=self.gate.config.emergency_objective_threshold + 1e-9,
            )
        self._previous_policy = policy
        return policy


class ForecastCapacityController(PredictiveHiGHSController):
    """Forecast baseline allocated in proportion to forecast-safe headroom."""

    name = "forecast-capacity-v1"

    def build_policy(
        self,
        config: ScenarioConfig,
        groups: tuple[GroupProfile, ...],
        upf_states: list[UPFState],
        created_at: datetime,
        version: int,
        context: ControlContext | None = None,
    ) -> Policy:
        context = context or ControlContext()
        duration = timedelta(seconds=config.step_seconds * config.decision_interval_steps)
        target = TimeWindow(created_at, created_at + duration)
        state_by_id = {state.upf_id: state for state in upf_states}
        policy_groups: list[PolicyGroup] = []
        forecasts: list[Forecast] = []
        try:
            for group in groups:
                predicted = self.forecaster.predict(
                    context.history_by_group.get(group.key.selection_id, ()),
                    issued_at=created_at,
                    target_window=target,
                )
                predicted.existing_load_by_upf = _residual_contract(context)
                forecasts.append(predicted)
                scores: dict[str, float] = {}
                for upf_id in group.eligible_upfs:
                    state = state_by_id[upf_id]
                    if state.health not in {"healthy", "degraded"}:
                        continue
                    residual = context.residual_by_upf.get(upf_id, ResidualObservation(0, 0, 0))
                    scores[upf_id] = max(0.0, min(
                        state.safe_capacity_mbps.ul - residual.ul_mbps,
                        state.safe_capacity_mbps.dl - residual.dl_mbps,
                        float(state.safe_session_capacity) - residual.surviving_sessions,
                    ))
                total = sum(scores.values())
                if total <= 0:
                    raise ForecastingError("no forecast-safe headroom")
                policy_groups.append(PolicyGroup(
                    GroupKey(group.key.zone, group.key.dnn, group.key.snssai),
                    {upf_id: score / total for upf_id, score in scores.items() if score > 0},
                ))
        except ForecastingError as error:
            return self._fallback_policy(config, groups, upf_states, created_at, version, str(error))

        projected = _project(policy_groups, forecasts, upf_states)
        slack = _slack(projected, upf_states)
        has_slack = any(value > 1e-7 for values in slack for value in values.values())
        policy = Policy(
            policy_id=f"{config.scenario_id}:forecast-capacity:{version}",
            policy_version=version,
            created_at=created_at,
            validity=target,
            forecast_id="+".join(sorted(item.forecast_id for item in forecasts)),
            upf_state_time=max(state.measurement_time for state in upf_states),
            solver=SolverReport(self.name, "feasible_with_slack" if has_slack else "optimal", 0),
            constraint_slack=ConstraintSlack(*slack),
            groups=policy_groups,
            fallback=Fallback(),
            validator_version="pending-independent-validation",
        )
        try:
            validate_policy(
                policy, forecasts, upf_states, activation_time=created_at,
                previous_policy=self._previous_policy,
                config=ValidationConfig(allow_feasible_with_slack=self.allow_slack),
            )
        except PolicyValidationError as error:
            return self._fallback_policy(config, groups, upf_states, created_at, version, f"validation:{error}")
        self._previous_policy = policy
        return policy


class OracleHiGHSController(PredictiveHiGHSController):
    """Non-deployable evaluator that peeks at the target arrival stream."""

    name = "oracle-highs-v1"

    def build_policy(
        self,
        config: ScenarioConfig,
        groups: tuple[GroupProfile, ...],
        upf_states: list[UPFState],
        created_at: datetime,
        version: int,
        context: ControlContext | None = None,
    ) -> Policy:
        context = context or ControlContext()
        duration = timedelta(seconds=config.step_seconds * config.decision_interval_steps)
        target = TimeWindow(created_at, created_at + duration)
        forecasts: list[Forecast] = []
        for group in groups:
            oracle = context.oracle_new_by_group.get(group.key.selection_id)
            if oracle is None:
                return self._fallback_policy(config, groups, upf_states, created_at, version, "oracle_unavailable")
            arrivals = Quantiles(oracle.surviving_sessions, oracle.surviving_sessions, oracle.surviving_sessions)
            ul = Quantiles(oracle.ul_mbps, oracle.ul_mbps, oracle.ul_mbps)
            dl = Quantiles(oracle.dl_mbps, oracle.dl_mbps, oracle.dl_mbps)
            forecasts.append(Forecast(
                forecast_id=f"oracle:{config.scenario_id}:{version}:{group.key.selection_id}",
                issued_at=created_at,
                source_window_end=created_at,
                target_window=target,
                horizon_steps=1,
                group=GroupKey(group.key.zone, group.key.dnn, group.key.snssai),
                new_session_count=arrivals,
                new_load_ul_mbps=ul,
                new_load_dl_mbps=dl,
                existing_load_by_upf=_residual_contract(context),
                model_version="oracle-evaluator/non-deployable",
                quality_flags=["oracle_non_deployable"],
            ))
        result = solve_allocation(
            forecasts, upf_states, created_at=created_at, policy_version=version,
            previous_policy=self._previous_policy, config=OptimizationConfig(),
        )
        if result.policy is None:
            return self._fallback_policy(config, groups, upf_states, created_at, version, result.status)
        try:
            validate_policy(
                result.policy, forecasts, upf_states, activation_time=created_at,
                previous_policy=self._previous_policy,
                config=ValidationConfig(allow_feasible_with_slack=True),
            )
        except PolicyValidationError as error:
            return self._fallback_policy(config, groups, upf_states, created_at, version, f"validation:{error}")
        result.policy.solver = SolverReport(self.name, result.policy.solver.status, result.policy.solver.runtime_ms)
        self._previous_policy = result.policy
        return result.policy


def _residual_contract(context: ControlContext) -> list[ExistingLoad]:
    return [
        ExistingLoad(
            upf_id=upf_id,
            surviving_sessions=Quantiles(value.surviving_sessions, value.surviving_sessions, value.surviving_sessions),
            ul_mbps=Quantiles(value.ul_mbps, value.ul_mbps, value.ul_mbps),
            dl_mbps=Quantiles(value.dl_mbps, value.dl_mbps, value.dl_mbps),
        )
        for upf_id, value in sorted(context.residual_by_upf.items())
    ]


def _project(
    groups: list[PolicyGroup], forecasts: list[Forecast], states: list[UPFState]
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    forecast_by_group = {item.group.selection_id: item for item in forecasts}
    residual = {state.upf_id: ResidualObservation(0, 0, 0) for state in states}
    for forecast in forecasts:
        for item in forecast.existing_load_by_upf:
            current = residual[item.upf_id]
            residual[item.upf_id] = ResidualObservation(
                max(current.surviving_sessions, item.surviving_sessions.p95),
                max(current.ul_mbps, item.ul_mbps.p95),
                max(current.dl_mbps, item.dl_mbps.p95),
            )
    ul = {upf_id: value.ul_mbps for upf_id, value in residual.items()}
    dl = {upf_id: value.dl_mbps for upf_id, value in residual.items()}
    sessions = {upf_id: value.surviving_sessions for upf_id, value in residual.items()}
    for group in groups:
        forecast = forecast_by_group[group.key.selection_id]
        for upf_id, weight in group.weights.items():
            ul[upf_id] += weight * forecast.new_load_ul_mbps.p95
            dl[upf_id] += weight * forecast.new_load_dl_mbps.p95
            sessions[upf_id] += weight * forecast.new_session_count.p95
    return ul, dl, sessions


def _slack(
    projected: tuple[dict[str, float], dict[str, float], dict[str, float]],
    states: list[UPFState],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    ul, dl, sessions = projected
    return (
        {state.upf_id: max(0.0, ul[state.upf_id] - state.safe_capacity_mbps.ul) for state in states},
        {state.upf_id: max(0.0, dl[state.upf_id] - state.safe_capacity_mbps.dl) for state in states},
        {state.upf_id: max(0.0, sessions[state.upf_id] - state.safe_session_capacity) for state in states},
    )


def _operating_index(
    groups: list[PolicyGroup], forecasts: list[Forecast], states: list[UPFState]
) -> float:
    ul, dl, sessions = _project(groups, forecasts, states)
    def ratio(value: float, capacity: float) -> float:
        return value / capacity if capacity > 0 else (math.inf if value > 0 else 0.0)
    return max(
        max(
            ratio(ul[state.upf_id], state.safe_capacity_mbps.ul),
            ratio(dl[state.upf_id], state.safe_capacity_mbps.dl),
            ratio(sessions[state.upf_id], state.safe_session_capacity),
        )
        for state in states
    )


def _policy_routes_safely(policy: Policy, states: list[UPFState]) -> bool:
    state_by_id = {state.upf_id: state for state in states}
    return all(
        weight <= 0
        or (
            upf_id in state_by_id
            and state_by_id[upf_id].health in {"healthy", "degraded"}
            and group.key.selection_id in state_by_id[upf_id].eligible_groups
        )
        for group in policy.groups
        for upf_id, weight in group.weights.items()
    )


def controller_by_name(name: str, *, forecaster: Any | None = None) -> Controller:
    if forecaster is not None and name not in {"forecast-capacity", "predictive"}:
        raise ValueError("a forecast bundle can only be attached to a forecast controller")
    controllers: dict[str, Controller] = {
        "static": StaticCapacityController(),
        "reactive": ReactiveThresholdController(),
        "forecast-capacity": ForecastCapacityController(forecaster=forecaster),
        "predictive": PredictiveHiGHSController(forecaster=forecaster),
        "oracle": OracleHiGHSController(),
    }
    try:
        return controllers[name]
    except KeyError as error:
        raise ValueError(f"unknown controller {name!r}; choose from {sorted(controllers)}") from error


def normalized_healthy_weights(
    policy: Policy,
    group: GroupKey,
    eligible_upfs: tuple[str, ...],
    upf_states: dict[str, UPFState],
) -> dict[str, float]:
    """Apply the enforcement-time health gate without inventing eligibility."""
    try:
        requested = policy.weights_for(group)
    except KeyError:
        return {}
    allowed = {
        upf_id: weight
        for upf_id, weight in requested.items()
        if upf_id in eligible_upfs and upf_states[upf_id].health in {"healthy", "degraded"}
    }
    total = sum(allowed.values())
    return {upf_id: weight / total for upf_id, weight in allowed.items()} if total > 0 else {}
