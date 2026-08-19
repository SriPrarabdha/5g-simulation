from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from .common import Contract, GroupKey, TimeWindow, parse_utc, require_non_negative, require_schema


@dataclass(frozen=True, slots=True)
class SolverReport:
    name: str
    status: str
    runtime_ms: int

    def __post_init__(self) -> None:
        if self.status not in {
            "optimal", "feasible_with_slack", "infeasible", "timeout", "error", "skipped",
        }:
            raise ValueError(f"unsupported solver status: {self.status}")
        require_non_negative("runtime_ms", self.runtime_ms)


@dataclass(slots=True)
class ConstraintSlack:
    ul_mbps_by_upf: dict[str, float] = field(default_factory=dict)
    dl_mbps_by_upf: dict[str, float] = field(default_factory=dict)
    sessions_by_upf: dict[str, float] = field(default_factory=dict)

    def validate(self) -> None:
        for category in (self.ul_mbps_by_upf, self.dl_mbps_by_upf, self.sessions_by_upf):
            for upf_id, value in category.items():
                require_non_negative(f"slack for {upf_id}", value)


@dataclass(slots=True)
class PolicyGroup:
    key: GroupKey
    weights: dict[str, float]

    def validate(self, tolerance: float = 1e-9) -> None:
        if self.key.five_qi is not None:
            raise ValueError("5QI is not part of the v1 policy selection key")
        if not self.weights:
            raise ValueError(f"group {self.key.selection_id} has no weights")
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in self.weights.values()):
            raise ValueError("weights must be finite and in [0, 1]")
        if not math.isclose(sum(self.weights.values()), 1.0, abs_tol=tolerance):
            raise ValueError("weights must sum to one")


@dataclass(frozen=True, slots=True)
class Fallback:
    used: bool = False
    reason: str | None = None
    source_policy_id: str | None = None


@dataclass(slots=True)
class Policy(Contract):
    schema_version: ClassVar[str] = "policy/1.0"
    policy_id: str
    policy_version: int
    created_at: datetime
    validity: TimeWindow
    forecast_id: str
    upf_state_time: datetime
    solver: SolverReport
    constraint_slack: ConstraintSlack
    groups: list[PolicyGroup]
    fallback: Fallback
    validator_version: str

    def __post_init__(self) -> None:
        self.created_at = parse_utc(self.created_at)
        self.upf_state_time = parse_utc(self.upf_state_time)
        self.validate()

    def validate(self) -> None:
        if not self.policy_id or self.policy_version < 1:
            raise ValueError("policy identity and positive version are required")
        self.constraint_slack.validate()
        if not self.groups:
            raise ValueError("policy must contain at least one group")
        seen: set[str] = set()
        for group in self.groups:
            group.validate()
            if group.key.selection_id in seen:
                raise ValueError("policy contains duplicate group keys")
            seen.add(group.key.selection_id)

    def weights_for(self, group: GroupKey) -> dict[str, float]:
        for policy_group in self.groups:
            if policy_group.key.selection_id == group.selection_id:
                return dict(policy_group.weights)
        raise KeyError(group.selection_id)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Policy":
        require_schema(data, cls.schema_version)
        return cls(
            policy_id=data["policy_id"], policy_version=data["policy_version"], created_at=data["created_at"],
            validity=TimeWindow.from_dict(data["validity"]), forecast_id=data["forecast_id"],
            upf_state_time=data["upf_state_time"], solver=SolverReport(**data["solver"]),
            constraint_slack=ConstraintSlack(**data["constraint_slack"]),
            groups=[PolicyGroup(key=GroupKey.from_dict(item["key"]), weights=dict(item["weights"])) for item in data["groups"]],
            fallback=Fallback(**data["fallback"]), validator_version=data["validator_version"],
        )


@dataclass(slots=True)
class SelectionAudit(Contract):
    schema_version: ClassVar[str] = "selection-audit/1.0"
    timestamp: datetime
    session_id_hash: str
    session_hash_value: str
    group: GroupKey
    eligible_upfs: list[str]
    requested_weights: dict[str, float]
    selected_upf: str | None
    policy_id: str | None
    reason: str

    def __post_init__(self) -> None:
        self.timestamp = parse_utc(self.timestamp)
        self.validate()

    def validate(self) -> None:
        if self.reason not in {
            "optimizer_weighted", "fallback_last_safe", "fallback_static", "no_eligible_upf"
        }:
            raise ValueError(f"unsupported selection reason: {self.reason}")
        if self.selected_upf is not None and self.selected_upf not in self.eligible_upfs:
            raise ValueError("selected UPF is not eligible")
        if self.group.five_qi is not None:
            raise ValueError("5QI is not part of the v1 selection key")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SelectionAudit":
        require_schema(data, cls.schema_version)
        return cls(
            timestamp=data["timestamp"], session_id_hash=data["session_id_hash"],
            session_hash_value=data["session_hash_value"], group=GroupKey.from_dict(data["group"]),
            eligible_upfs=list(data["eligible_upfs"]), requested_weights=dict(data["requested_weights"]),
            selected_upf=data.get("selected_upf"), policy_id=data.get("policy_id"), reason=data["reason"],
        )
