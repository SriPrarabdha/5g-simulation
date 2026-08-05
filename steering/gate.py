from __future__ import annotations

from dataclasses import asdict, dataclass

from schemas import Policy, UPFState


@dataclass(frozen=True, slots=True)
class PolicyGateConfig:
    min_hold_epochs: int = 2
    min_objective_improvement: float = 0.03
    max_group_total_variation: float = 0.18
    emergency_objective_threshold: float = 1.0

    def __post_init__(self) -> None:
        if self.min_hold_epochs < 0:
            raise ValueError("min_hold_epochs must be non-negative")
        if self.min_objective_improvement < 0:
            raise ValueError("min_objective_improvement must be non-negative")
        if not 0 <= self.max_group_total_variation <= 1:
            raise ValueError("max_group_total_variation must be in [0, 1]")
        if self.emergency_objective_threshold <= 0:
            raise ValueError("emergency_objective_threshold must be positive")


@dataclass(frozen=True, slots=True)
class PolicyGateDecision:
    action: str
    reason: str
    epoch: int
    hold_remaining_epochs: int
    current_objective: float | None
    candidate_objective: float
    objective_improvement: float | None
    max_group_total_variation: float
    emergency_override: bool = False

    @property
    def applied(self) -> bool:
        return self.action in {"apply", "emergency_apply"}

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"applied": self.applied}


class PolicyGate:
    """Stateful hold, hysteresis, churn, and emergency policy gate."""

    def __init__(self, config: PolicyGateConfig | None = None) -> None:
        self.config = config or PolicyGateConfig()
        self.last_applied_epoch: int | None = None
        self.last_decision: PolicyGateDecision | None = None

    @staticmethod
    def _max_group_variation(current: Policy, candidate: Policy) -> float:
        current_by_group = {item.key.selection_id: item.weights for item in current.groups}
        candidate_by_group = {item.key.selection_id: item.weights for item in candidate.groups}
        maximum = 0.0
        for group_id in set(current_by_group) | set(candidate_by_group):
            old = current_by_group.get(group_id, {})
            new = candidate_by_group.get(group_id, {})
            destinations = set(old) | set(new)
            maximum = max(
                maximum,
                0.5 * sum(abs(new.get(upf_id, 0.0) - old.get(upf_id, 0.0)) for upf_id in destinations),
            )
        return maximum

    @staticmethod
    def _requires_emergency_override(current: Policy, states: list[UPFState]) -> bool:
        state_by_id = {state.upf_id: state for state in states}
        for group in current.groups:
            group_id = group.key.selection_id
            for upf_id, weight in group.weights.items():
                if weight <= 0:
                    continue
                state = state_by_id.get(upf_id)
                if (
                    state is None
                    or state.health not in {"healthy", "degraded"}
                    or group_id not in state.eligible_groups
                ):
                    return True
        return False

    def evaluate(
        self,
        candidate: Policy,
        *,
        epoch: int,
        candidate_objective: float,
        states: list[UPFState],
        current: Policy | None = None,
        current_objective: float | None = None,
    ) -> PolicyGateDecision:
        if epoch < 0 or candidate_objective < 0:
            raise ValueError("epoch and candidate objective must be non-negative")
        if current is None:
            decision = PolicyGateDecision(
                "apply", "initial_safe_policy", epoch, 0, None, candidate_objective,
                None, 0.0,
            )
            self.last_applied_epoch = epoch
            self.last_decision = decision
            return decision

        variation = self._max_group_variation(current, candidate)
        improvement = current_objective - candidate_objective if current_objective is not None else None
        unsafe_route = self._requires_emergency_override(current, states)
        emergency = unsafe_route or (
            current_objective is not None
            and current_objective > self.config.emergency_objective_threshold
        )
        if emergency:
            decision = PolicyGateDecision(
                "emergency_apply",
                "current_policy_routes_to_unavailable_or_ineligible_upf"
                if unsafe_route else "current_policy_exceeds_emergency_operating_threshold",
                epoch, 0, current_objective, candidate_objective, improvement, variation, True,
            )
            self.last_applied_epoch = epoch
            self.last_decision = decision
            return decision

        applied_epoch = self.last_applied_epoch if self.last_applied_epoch is not None else epoch - 1
        remaining = max(0, self.config.min_hold_epochs - (epoch - applied_epoch))
        if remaining:
            decision = PolicyGateDecision(
                "hold", "minimum_hold", epoch, remaining, current_objective,
                candidate_objective, improvement, variation,
            )
        elif variation > self.config.max_group_total_variation:
            decision = PolicyGateDecision(
                "hold", "churn_budget", epoch, 0, current_objective,
                candidate_objective, improvement, variation,
            )
        elif improvement is not None and improvement < self.config.min_objective_improvement:
            decision = PolicyGateDecision(
                "hold", "hysteresis", epoch, 0, current_objective,
                candidate_objective, improvement, variation,
            )
        else:
            decision = PolicyGateDecision(
                "apply", "material_safe_improvement", epoch, 0, current_objective,
                candidate_objective, improvement, variation,
            )
            self.last_applied_epoch = epoch
        self.last_decision = decision
        return decision
