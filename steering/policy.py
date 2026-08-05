from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from schemas import Forecast, Policy, UPFState
from schemas.common import parse_utc


class PolicyValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    planning_quantile: str = "p95"
    allow_feasible_with_slack: bool = False
    weight_tolerance: float = 1e-7
    projection_tolerance: float = 1e-5
    max_total_variation: float | None = None
    validator_version: str = "independent-policy-validator/1.0"

    def __post_init__(self) -> None:
        if self.planning_quantile not in {"p50", "p90", "p95"}:
            raise ValueError("planning_quantile must be p50, p90, or p95")
        if self.max_total_variation is not None and self.max_total_variation < 0:
            raise ValueError("max_total_variation must be non-negative")


@dataclass(frozen=True, slots=True)
class ValidationReport:
    validator_version: str
    projected_ul_mbps_by_upf: dict[str, float]
    projected_dl_mbps_by_upf: dict[str, float]
    projected_sessions_by_upf: dict[str, float]
    total_variation: float


def _q(value, quantile: str) -> float:
    result = getattr(value, quantile)
    if result is None:
        raise PolicyValidationError(f"forecast lacks planning quantile {quantile}")
    return float(result)


def validate_policy(
    policy: Policy,
    forecasts: Iterable[Forecast],
    upf_states: Iterable[UPFState],
    *,
    activation_time: datetime,
    previous_policy: Policy | None = None,
    config: ValidationConfig | None = None,
) -> ValidationReport:
    """Independently validate a policy and reproduce its load projections."""

    settings = config or ValidationConfig()
    policy.validate()
    forecasts = list(forecasts)
    states = list(upf_states)
    activation_time = parse_utc(activation_time)
    if not forecasts or not states:
        raise PolicyValidationError("forecasts and UPF states are required")
    state_by_id = {state.upf_id: state for state in states}
    if len(state_by_id) != len(states):
        raise PolicyValidationError("duplicate UPF states")
    forecast_by_group = {item.group.selection_id: item for item in forecasts}
    if len(forecast_by_group) != len(forecasts):
        raise PolicyValidationError("duplicate group forecasts")
    target = forecasts[0].target_window
    if any(item.target_window != target for item in forecasts):
        raise PolicyValidationError("forecast target windows differ")
    if policy.validity != target:
        raise PolicyValidationError("policy validity does not exactly match forecast target")
    if activation_time != policy.validity.start:
        raise PolicyValidationError("policy activation must equal valid_from")
    if policy.created_at > activation_time:
        raise PolicyValidationError("policy was created after activation")
    if set(forecast_by_group) != {item.key.selection_id for item in policy.groups}:
        raise PolicyValidationError("policy groups do not exactly match forecast groups")
    if previous_policy is not None:
        if policy.policy_version != previous_policy.policy_version + 1:
            raise PolicyValidationError("policy version is not monotonic")
        if previous_policy.validity.end > policy.validity.start:
            raise PolicyValidationError("policy validity overlaps the previous policy")
    if policy.solver.status not in {"optimal", "feasible_with_slack"}:
        raise PolicyValidationError(f"solver status {policy.solver.status} is not publishable")
    if policy.solver.status == "feasible_with_slack" and not settings.allow_feasible_with_slack:
        raise PolicyValidationError("degraded-mode slack publication is disabled")

    for state in states:
        age = (activation_time - state.measurement_time).total_seconds()
        if age < 0:
            raise PolicyValidationError(f"UPF state for {state.upf_id} is from the future")
        if age > state.state_ttl_seconds:
            raise PolicyValidationError(f"UPF state for {state.upf_id} is stale")

    residual_ul = {upf_id: 0.0 for upf_id in state_by_id}
    residual_dl = {upf_id: 0.0 for upf_id in state_by_id}
    residual_sessions = {upf_id: 0.0 for upf_id in state_by_id}
    for forecast in forecasts:
        for existing in forecast.existing_load_by_upf:
            if existing.upf_id not in state_by_id:
                raise PolicyValidationError(f"forecast references unknown UPF {existing.upf_id}")
            residual_ul[existing.upf_id] = max(
                residual_ul[existing.upf_id], _q(existing.ul_mbps, settings.planning_quantile)
            )
            residual_dl[existing.upf_id] = max(
                residual_dl[existing.upf_id], _q(existing.dl_mbps, settings.planning_quantile)
            )
            residual_sessions[existing.upf_id] = max(
                residual_sessions[existing.upf_id],
                _q(existing.surviving_sessions, settings.planning_quantile),
            )

    projected_ul = dict(residual_ul)
    projected_dl = dict(residual_dl)
    projected_sessions = dict(residual_sessions)
    total_variation = 0.0
    for group_policy in policy.groups:
        group_id = group_policy.key.selection_id
        forecast = forecast_by_group[group_id]
        group_policy.validate(settings.weight_tolerance)
        previous_weights: dict[str, float] = {}
        if previous_policy is not None:
            try:
                previous_weights = previous_policy.weights_for(group_policy.key)
            except KeyError:
                previous_weights = {}
        all_churn_ids = set(previous_weights) | set(group_policy.weights)
        total_variation += 0.5 * sum(
            abs(group_policy.weights.get(upf_id, 0.0) - previous_weights.get(upf_id, 0.0))
            for upf_id in all_churn_ids
        )
        for upf_id, weight in group_policy.weights.items():
            state = state_by_id.get(upf_id)
            if state is None:
                raise PolicyValidationError(f"policy references unknown UPF {upf_id}")
            if state.health not in {"healthy", "degraded"}:
                raise PolicyValidationError(f"policy weights unhealthy UPF {upf_id}")
            if group_id not in state.eligible_groups:
                raise PolicyValidationError(f"policy weights ineligible UPF {upf_id}")
            if forecast.group.zone not in state.path_latency_ms_by_zone:
                raise PolicyValidationError(f"policy uses UPF {upf_id} without a locality path")
            projected_ul[upf_id] += weight * _q(forecast.new_load_ul_mbps, settings.planning_quantile)
            projected_dl[upf_id] += weight * _q(forecast.new_load_dl_mbps, settings.planning_quantile)
            projected_sessions[upf_id] += weight * _q(
                forecast.new_session_count, settings.planning_quantile
            )

    if settings.max_total_variation is not None and total_variation > settings.max_total_variation:
        raise PolicyValidationError("policy exceeds configured churn limit")

    expected_slack = {
        "ul": {
            upf_id: max(0.0, projected_ul[upf_id] - state.safe_capacity_mbps.ul)
            for upf_id, state in state_by_id.items()
        },
        "dl": {
            upf_id: max(0.0, projected_dl[upf_id] - state.safe_capacity_mbps.dl)
            for upf_id, state in state_by_id.items()
        },
        "sessions": {
            upf_id: max(0.0, projected_sessions[upf_id] - state.safe_session_capacity)
            for upf_id, state in state_by_id.items()
        },
    }
    reported = {
        "ul": policy.constraint_slack.ul_mbps_by_upf,
        "dl": policy.constraint_slack.dl_mbps_by_upf,
        "sessions": policy.constraint_slack.sessions_by_upf,
    }
    for category, expected_by_upf in expected_slack.items():
        for upf_id, expected in expected_by_upf.items():
            actual = reported[category].get(upf_id, 0.0)
            if not math.isclose(actual, expected, abs_tol=settings.projection_tolerance):
                raise PolicyValidationError(
                    f"reported {category} slack for {upf_id} does not match projection"
                )
    has_slack = any(
        value > settings.projection_tolerance
        for values in expected_slack.values()
        for value in values.values()
    )
    if has_slack != (policy.solver.status == "feasible_with_slack"):
        raise PolicyValidationError("solver status does not match projected capacity slack")
    policy.validator_version = settings.validator_version
    return ValidationReport(
        validator_version=settings.validator_version,
        projected_ul_mbps_by_upf=projected_ul,
        projected_dl_mbps_by_upf=projected_dl,
        projected_sessions_by_upf=projected_sessions,
        total_variation=total_variation,
    )


class AtomicPolicyStore:
    """In-process compare-and-swap publication with atomic reader snapshots."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._policy: Policy | None = None

    def read(self) -> Policy | None:
        with self._lock:
            return self._policy

    def publish(self, policy: Policy, *, expected_current_version: int) -> None:
        policy.validate()
        with self._lock:
            current_version = self._policy.policy_version if self._policy is not None else 0
            if current_version != expected_current_version:
                raise PolicyValidationError(
                    f"compare-and-swap failed: expected {expected_current_version}, got {current_version}"
                )
            if policy.policy_version != current_version + 1:
                raise PolicyValidationError("published policy version must increment by one")
            self._policy = policy
