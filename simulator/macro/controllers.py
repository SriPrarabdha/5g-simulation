from __future__ import annotations

from datetime import datetime, timedelta

from schemas import (
    ConstraintSlack,
    Fallback,
    GroupKey,
    Policy,
    PolicyGroup,
    SolverReport,
    TimeWindow,
    UPFState,
)

from .config import GroupProfile, ScenarioConfig


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

