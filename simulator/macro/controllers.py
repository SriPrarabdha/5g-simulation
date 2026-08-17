from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from forecasting import DemandObservation, ForecastingError, MovingAverageForecaster, ResidualObservation
from optimization import (
    ActiveCohort,
    CohortMPCConfig,
    CohortMPCResult,
    OptimizationConfig,
    solve_allocation,
    solve_cohort_mpc,
)

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
    scheduled_multiplier_by_group: dict[str, float] = field(default_factory=dict)
    scheduled_multiplier_by_group_horizon: dict[str, tuple[float, ...]] = field(default_factory=dict)
    active_cohorts: tuple[ActiveCohort, ...] = ()


@dataclass(frozen=True, slots=True)
class ForecastAdjustmentConfig:
    scheduled_event_hints_enabled: bool = False
    anomaly_fallback_enabled: bool = False
    anomaly_history_windows: int = 6
    anomaly_ratio_threshold: float = 1.5
    anomaly_multiplier_cap: float = 4.0

    def __post_init__(self) -> None:
        if self.anomaly_history_windows < 2:
            raise ValueError("anomaly_history_windows must be at least two")
        if self.anomaly_ratio_threshold <= 1:
            raise ValueError("anomaly_ratio_threshold must be greater than one")
        if self.anomaly_multiplier_cap < 1:
            raise ValueError("anomaly_multiplier_cap must be at least one")


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
    required_history_windows = 0

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
    required_history_windows = 0

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
        optimization_config: OptimizationConfig | None = None,
        optimizer_weight: float = 1.0,
        forecast_adjustment_config: ForecastAdjustmentConfig | None = None,
    ) -> None:
        if not 0 <= optimizer_weight <= 1:
            raise ValueError("optimizer_weight must be in [0, 1]")
        self.forecaster = forecaster or MovingAverageForecaster(history_windows)
        self.allow_slack = allow_slack
        self.gate = PolicyGate(gate_config)
        self.optimization_config = optimization_config or OptimizationConfig()
        self.optimizer_weight = optimizer_weight
        self.forecast_adjustment_config = (
            forecast_adjustment_config or ForecastAdjustmentConfig()
        )
        self._previous_policy: Policy | None = None
        self._fallback = StaticCapacityController()
        self.last_forecasts: list[Forecast] = []
        self.last_optimization: Any | None = None
        self.last_candidate: Policy | None = None
        self.last_gate_decision: PolicyGateDecision | None = None

    @property
    def required_history_windows(self) -> int:
        return int(getattr(self.forecaster, "required_history_windows", 144))

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
                multiplier, flags = self._forecast_adjustment(
                    history, context.scheduled_multiplier_by_group.get(
                        group.key.selection_id, 1.0
                    )
                )
                if multiplier != 1.0:
                    predicted.new_session_count = _scale_quantiles(
                        predicted.new_session_count, multiplier
                    )
                    predicted.new_load_ul_mbps = _scale_quantiles(
                        predicted.new_load_ul_mbps, multiplier
                    )
                    predicted.new_load_dl_mbps = _scale_quantiles(
                        predicted.new_load_dl_mbps, multiplier
                    )
                predicted.quality_flags.extend(flags)
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
            config=self.optimization_config,
            demand_multiplier_by_group=_lifetime_demand_multipliers(
                groups,
                decision_interval_steps=config.decision_interval_steps,
                horizon_windows=self.optimization_config.lifetime_horizon_windows,
                strength=self.optimization_config.lifetime_weight_strength,
            ),
        )
        self.last_optimization = result
        if result.policy is None or (result.status == "feasible_with_slack" and not self.allow_slack):
            return self._fallback_policy(config, groups, upf_states, created_at, version, result.status)
        if self.optimizer_weight < 1.0:
            static_policy = self._fallback.build_policy(
                config, groups, upf_states, created_at, version, context
            )
            static_by_group = {
                item.key.selection_id: item.weights for item in static_policy.groups
            }
            blended_groups: list[PolicyGroup] = []
            for optimized in result.policy.groups:
                baseline = static_by_group[optimized.key.selection_id]
                destinations = sorted(set(baseline) | set(optimized.weights))
                weights = {
                    upf_id: (
                        (1.0 - self.optimizer_weight) * baseline.get(upf_id, 0.0)
                        + self.optimizer_weight * optimized.weights.get(upf_id, 0.0)
                    )
                    for upf_id in destinations
                }
                total = sum(weights.values())
                blended_groups.append(PolicyGroup(
                    optimized.key,
                    {upf_id: weight / total for upf_id, weight in weights.items()},
                ))
            result.policy.groups = blended_groups
            projected = _project(blended_groups, forecasts, upf_states)
            slack = _slack(projected, upf_states)
            result.policy.constraint_slack = ConstraintSlack(*slack)
            has_slack = any(
                value > self.optimization_config.slack_tolerance
                for category in slack
                for value in category.values()
            )
            result.policy.solver = SolverReport(
                result.policy.solver.name,
                "feasible_with_slack" if has_slack else "optimal",
                result.policy.solver.runtime_ms,
            )
            result.policy.policy_id = (
                f"predictive-blend-{self.optimizer_weight:g}:{version}:"
                f"{result.policy.forecast_id[:16]}"
            )
            result.policy.validate()
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
        candidate_objective = _operating_index(result.policy.groups, forecasts, upf_states)
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

    def _forecast_adjustment(
        self,
        history: tuple[DemandObservation, ...],
        scheduled_multiplier: float,
    ) -> tuple[float, list[str]]:
        settings = self.forecast_adjustment_config
        if settings.scheduled_event_hints_enabled and scheduled_multiplier != 1.0:
            return scheduled_multiplier, [
                f"causal_scheduled_event_multiplier:{scheduled_multiplier:g}"
            ]
        if not settings.anomaly_fallback_enabled or len(history) < 3:
            return 1.0, []
        prior = history[-(settings.anomaly_history_windows + 1):-1]
        baseline_values = sorted(item.new_session_count for item in prior)
        if not baseline_values:
            return 1.0, []
        middle = len(baseline_values) // 2
        baseline = (
            baseline_values[middle]
            if len(baseline_values) % 2
            else (baseline_values[middle - 1] + baseline_values[middle]) / 2
        )
        latest = history[-1].new_session_count
        ratio = latest / max(1.0, baseline)
        if ratio < settings.anomaly_ratio_threshold:
            return 1.0, []
        multiplier = min(settings.anomaly_multiplier_cap, ratio)
        return multiplier, [f"observed_anomaly_multiplier:{multiplier:g}"]

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
                multiplier, flags = self._forecast_adjustment(
                    context.history_by_group.get(group.key.selection_id, ()),
                    context.scheduled_multiplier_by_group.get(
                        group.key.selection_id, 1.0
                    ),
                )
                if multiplier != 1.0:
                    predicted.new_session_count = _scale_quantiles(
                        predicted.new_session_count, multiplier
                    )
                    predicted.new_load_ul_mbps = _scale_quantiles(
                        predicted.new_load_ul_mbps, multiplier
                    )
                    predicted.new_load_dl_mbps = _scale_quantiles(
                        predicted.new_load_dl_mbps, multiplier
                    )
                predicted.quality_flags.extend(flags)
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
    required_history_windows = 0

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


class CohortMPCController:
    """Causal multi-period controller guarded by a same-state static replay."""

    name = "cohort-mpc-v1"

    def __init__(
        self,
        *,
        forecaster: Any | None = None,
        mpc_config: CohortMPCConfig | None = None,
        forecast_adjustment: ForecastAdjustmentConfig | None = None,
    ) -> None:
        self.forecaster = forecaster or MovingAverageForecaster(6)
        self.mpc_config = mpc_config or CohortMPCConfig()
        self.forecast_adjustment = forecast_adjustment or ForecastAdjustmentConfig()
        self._fallback = StaticCapacityController()
        self.last_result: CohortMPCResult | None = None
        self.last_forecasts: list[Forecast] = []
        self.decision_count = 0
        self.certified_decision_count = 0

    @property
    def required_history_windows(self) -> int:
        return int(getattr(self.forecaster, "required_history_windows", 144))

    def build_policy(
        self,
        config: ScenarioConfig,
        groups: tuple[GroupProfile, ...],
        upf_states: list[UPFState],
        created_at: datetime,
        version: int,
        context: ControlContext | None = None,
    ) -> Policy:
        self.decision_count += 1
        context = context or ControlContext()
        duration = timedelta(seconds=config.step_seconds * config.decision_interval_steps)
        demand_by_group: dict[str, list[ResidualObservation]] = {}
        forecasts: list[Forecast] = []
        try:
            for group in groups:
                group_id = group.key.selection_id
                history = context.history_by_group.get(group_id, ())
                horizon: list[ResidualObservation] = []
                last: Forecast | None = None
                for horizon_step in range(1, self.mpc_config.horizon_windows + 1):
                    target = TimeWindow(
                        created_at + duration * (horizon_step - 1),
                        created_at + duration * horizon_step,
                    )
                    try:
                        predicted = self.forecaster.predict(
                            history,
                            issued_at=created_at,
                            target_window=target,
                            horizon_steps=horizon_step,
                        )
                        last = predicted
                    except ForecastingError:
                        if last is None:
                            raise
                        predicted = last
                    horizon_multipliers = context.scheduled_multiplier_by_group_horizon.get(
                        group_id, ()
                    )
                    scheduled_multiplier = (
                        horizon_multipliers[horizon_step - 1]
                        if horizon_step <= len(horizon_multipliers)
                        else 1.0
                    )
                    anomaly_multiplier = self._observed_anomaly_multiplier(history)
                    multiplier = scheduled_multiplier * anomaly_multiplier
                    flags: list[str] = []
                    if scheduled_multiplier != 1.0:
                        flags.append("scheduled_event_knowledge")
                    if anomaly_multiplier != 1.0:
                        flags.append("surprise_anomaly_adaptation")
                    horizon.append(ResidualObservation(
                        predicted.new_session_count.p95 * multiplier,
                        predicted.new_load_ul_mbps.p95 * multiplier,
                        predicted.new_load_dl_mbps.p95 * multiplier,
                    ))
                    if horizon_step == 1:
                        if multiplier != 1.0:
                            predicted.new_session_count = _scale_quantiles(
                                predicted.new_session_count, multiplier
                            )
                            predicted.new_load_ul_mbps = _scale_quantiles(
                                predicted.new_load_ul_mbps, multiplier
                            )
                            predicted.new_load_dl_mbps = _scale_quantiles(
                                predicted.new_load_dl_mbps, multiplier
                            )
                        predicted.quality_flags.extend(flags)
                        forecasts.append(predicted)
                demand_by_group[group_id] = horizon
        except ForecastingError:
            self.last_result = None
            self.last_forecasts = []
            return self._static_fallback(
                config, groups, upf_states, created_at, version,
                "insufficient_multi_horizon_forecast_history",
            )

        self.last_forecasts = forecasts
        current_step = round(
            (created_at - config.start_time).total_seconds() / config.step_seconds
        )
        result = solve_cohort_mpc(
            config,
            groups,
            upf_states,
            context.active_cohorts,
            demand_by_group,
            current_step=current_step,
            settings=self.mpc_config,
        )
        self.last_result = result
        profile_by_id = {profile.upf_id: profile for profile in config.upfs}
        unplanned_capacity_state = (
            result.known_future_events == 0
            and any(
                state.health not in {"healthy", "degraded"}
                or state.capacity_mbps.ul
                < profile_by_id[state.upf_id].capacity_ul_mbps - 1e-9
                or state.capacity_mbps.dl
                < profile_by_id[state.upf_id].capacity_dl_mbps - 1e-9
                for state in upf_states
            )
        )
        if (
            result.status != "optimal"
            or result.certificate is None
            or not result.certificate.accepted
            or (
                self.mpc_config.require_known_future_capacity_event
                and result.known_future_events == 0
            )
            or (
                self.mpc_config.fallback_on_unplanned_capacity_state
                and unplanned_capacity_state
            )
        ):
            reason = (
                "no_known_future_capacity_event"
                if (
                    self.mpc_config.require_known_future_capacity_event
                    and result.known_future_events == 0
                )
                else "observed_unplanned_capacity_state"
                if (
                    self.mpc_config.fallback_on_unplanned_capacity_state
                    and unplanned_capacity_state
                )
                else result.certificate.reason
                if result.certificate is not None
                else result.status
            )
            return self._static_fallback(
                config, groups, upf_states, created_at, version,
                f"same_state_static_certificate:{reason}",
            )

        policy_groups = [
            PolicyGroup(
                GroupKey(group.key.zone, group.key.dnn, group.key.snssai),
                result.first_allocation[group.key.selection_id],
            )
            for group in groups
        ]
        policy = Policy(
            policy_id=f"{config.scenario_id}:cohort-mpc:{version}",
            policy_version=version,
            created_at=created_at,
            validity=TimeWindow(created_at, created_at + duration),
            forecast_id="+".join(sorted(item.forecast_id for item in forecasts)),
            upf_state_time=max(state.measurement_time for state in upf_states),
            solver=SolverReport(self.name, "optimal", result.runtime_ms),
            constraint_slack=ConstraintSlack(),
            groups=policy_groups,
            fallback=Fallback(),
            validator_version="same-state-static-certificate/1.0",
        )
        policy.validate()
        self.certified_decision_count += 1
        return policy

    def _observed_anomaly_multiplier(
        self, history: tuple[DemandObservation, ...]
    ) -> float:
        settings = self.forecast_adjustment
        if not settings.anomaly_fallback_enabled or len(history) < 2:
            return 1.0
        prior = history[-(settings.anomaly_history_windows + 1):-1]
        if not prior:
            return 1.0
        values = sorted(item.new_session_count for item in prior)
        middle = len(values) // 2
        baseline = (
            values[middle]
            if len(values) % 2
            else (values[middle - 1] + values[middle]) / 2
        )
        ratio = history[-1].new_session_count / max(1.0, baseline)
        if ratio < settings.anomaly_ratio_threshold:
            return 1.0
        return min(settings.anomaly_multiplier_cap, ratio)

    def _static_fallback(
        self,
        config: ScenarioConfig,
        groups: tuple[GroupProfile, ...],
        upf_states: list[UPFState],
        created_at: datetime,
        version: int,
        reason: str,
    ) -> Policy:
        policy = self._fallback.build_policy(
            config, groups, upf_states, created_at, version
        )
        # Preserve the exact static policy identity as well as its weights.
        # The policy ID salts weighted rendezvous, so changing it here would
        # reshuffle sessions and make an MPC fallback differ from paired static.
        policy.solver = SolverReport(
            self.name,
            self.last_result.status if self.last_result is not None else "error",
            self.last_result.runtime_ms if self.last_result is not None else 0,
        )
        policy.fallback = Fallback(True, reason)
        policy.validator_version = "same-state-static-certificate/1.0"
        policy.validate()
        return policy


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


def _scale_quantiles(values: Quantiles, multiplier: float) -> Quantiles:
    return Quantiles(
        values.p50 * multiplier,
        values.p95 * multiplier,
        None if values.p90 is None else values.p90 * multiplier,
    )


def _lifetime_demand_multipliers(
    groups: tuple[GroupProfile, ...],
    *,
    decision_interval_steps: int,
    horizon_windows: int,
    strength: float,
) -> dict[str, float]:
    """Relative integrated occupancy of a newly admitted cohort over the horizon."""
    if horizon_windows == 1 or strength == 0:
        return {}
    occupancies: dict[str, float] = {}
    for group in groups:
        lifetime_values = range(group.lifetime_steps_min, group.lifetime_steps_max + 1)
        count = group.lifetime_steps_max - group.lifetime_steps_min + 1
        occupancy = 0.0
        for horizon in range(horizon_windows):
            threshold = horizon * decision_interval_steps
            surviving = max(
                0,
                group.lifetime_steps_max - max(group.lifetime_steps_min, threshold + 1) + 1,
            )
            occupancy += surviving / count
        occupancies[group.key.selection_id] = occupancy
    normalizer = sum(occupancies.values()) / len(occupancies)
    return {
        group_id: (1.0 - strength) + strength * occupancy / normalizer
        for group_id, occupancy in occupancies.items()
    }


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


def controller_by_name(
    name: str,
    *,
    forecaster: Any | None = None,
    gate_config: PolicyGateConfig | None = None,
    optimization_config: OptimizationConfig | None = None,
    optimizer_weight: float = 1.0,
    forecast_adjustment_config: ForecastAdjustmentConfig | None = None,
    mpc_config: CohortMPCConfig | None = None,
) -> Controller:
    if forecaster is not None and name not in {"forecast-capacity", "predictive", "mpc"}:
        raise ValueError("a forecast bundle can only be attached to a forecast controller")
    if (
        gate_config is not None
        or optimization_config is not None
        or forecast_adjustment_config is not None
    ) and name not in {"forecast-capacity", "predictive"}:
        raise ValueError("forecast settings can only be attached to a forecast controller")
    if mpc_config is not None and name != "mpc":
        raise ValueError("MPC settings can only be attached to the MPC controller")
    controllers: dict[str, Controller] = {
        "static": StaticCapacityController(),
        "reactive": ReactiveThresholdController(),
        "forecast-capacity": ForecastCapacityController(
            forecaster=forecaster,
            gate_config=gate_config,
            optimization_config=optimization_config,
            optimizer_weight=optimizer_weight,
            forecast_adjustment_config=forecast_adjustment_config,
        ),
        "predictive": PredictiveHiGHSController(
            forecaster=forecaster,
            gate_config=gate_config,
            optimization_config=optimization_config,
            optimizer_weight=optimizer_weight,
            forecast_adjustment_config=forecast_adjustment_config,
        ),
        "oracle": OracleHiGHSController(),
        "mpc": CohortMPCController(
            forecaster=forecaster,
            mpc_config=mpc_config,
        ),
    }
    try:
        return controllers[name]
    except KeyError as error:
        raise ValueError(f"unknown controller {name!r}; choose from {sorted(controllers)}") from error


def snapshot_controller(controller: Controller) -> dict[str, Any]:
    """JSON-safe causal controller state; diagnostic solver objects are excluded."""
    state: dict[str, Any] = {"codec_version": "controller-state/1.0", "name": controller.name}
    if isinstance(controller, PredictiveHiGHSController):
        state.update({
            "previous_policy": (
                controller._previous_policy.to_dict()
                if controller._previous_policy is not None else None
            ),
            "gate": {
                "last_applied_epoch": controller.gate.last_applied_epoch,
                "last_decision": (
                    asdict(controller.gate.last_decision)
                    if controller.gate.last_decision is not None else None
                ),
            },
        })
    if isinstance(controller, CohortMPCController):
        state.update({
            "decision_count": controller.decision_count,
            "certified_decision_count": controller.certified_decision_count,
        })
    forecaster = getattr(controller, "forecaster", None)
    alpha = getattr(forecaster, "_alpha_by_group", None)
    if alpha is not None:
        state["adaptive_conformal"] = {str(key): float(value) for key, value in alpha.items()}
    return state


def restore_controller(controller: Controller, state: dict[str, Any]) -> None:
    if state.get("codec_version") != "controller-state/1.0":
        raise ValueError("unsupported controller checkpoint codec")
    if state.get("name") != controller.name:
        raise ValueError("checkpoint controller identity mismatch")
    if isinstance(controller, PredictiveHiGHSController):
        previous = state.get("previous_policy")
        controller._previous_policy = Policy.from_dict(previous) if previous is not None else None
        gate = state.get("gate", {})
        controller.gate.last_applied_epoch = gate.get("last_applied_epoch")
        decision = gate.get("last_decision")
        controller.gate.last_decision = (
            PolicyGateDecision(**decision) if decision is not None else None
        )
    if isinstance(controller, CohortMPCController):
        controller.decision_count = int(state.get("decision_count", 0))
        controller.certified_decision_count = int(state.get("certified_decision_count", 0))
    alpha = state.get("adaptive_conformal")
    forecaster = getattr(controller, "forecaster", None)
    if alpha is not None:
        current = getattr(forecaster, "_alpha_by_group", None)
        if current is None or set(current) != set(alpha):
            raise ValueError("checkpoint adaptive-conformal groups mismatch")
        forecaster._alpha_by_group = {str(key): float(value) for key, value in alpha.items()}


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
