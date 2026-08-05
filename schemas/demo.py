from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from .common import Contract, GroupKey, TimeWindow, parse_utc, require_non_negative, require_schema
from .forecast import Quantiles


@dataclass(frozen=True, slots=True)
class ForecastTarget:
    window: TimeWindow
    horizon_minutes: int
    offered_ul_mbps: Quantiles
    offered_dl_mbps: Quantiles
    new_sessions: Quantiles
    active_sessions: Quantiles
    residual_mbps: Quantiles

    def __post_init__(self) -> None:
        if self.horizon_minutes not in range(10, 81, 10):
            raise ValueError("forecast horizon must be 10 through 80 minutes")


@dataclass(slots=True)
class ForecastBundle(Contract):
    schema_version: ClassVar[str] = "forecast-bundle/1.0"
    forecast_id: str
    model_version: str
    issued_at: datetime
    source_window_end: datetime
    group: GroupKey
    targets: list[ForecastTarget]
    feature_names: list[str]
    calibration_state: dict[str, Any]
    quality_flags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.issued_at = parse_utc(self.issued_at)
        self.source_window_end = parse_utc(self.source_window_end)
        self.validate()

    def validate(self) -> None:
        if not self.forecast_id or not self.model_version or not self.targets:
            raise ValueError("forecast identity, model version, and targets are required")
        horizons = [item.horizon_minutes for item in self.targets]
        if horizons != sorted(set(horizons)):
            raise ValueError("forecast horizons must be unique and ordered")
        if any(self.source_window_end > item.window.start for item in self.targets):
            raise ValueError("forecast features may not overlap a target window")
        if not self.calibration_state.get("method"):
            raise ValueError("calibration method is required")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ForecastBundle":
        require_schema(data, cls.schema_version)
        targets = []
        for item in data["targets"]:
            targets.append(ForecastTarget(
                window=TimeWindow.from_dict(item["window"]), horizon_minutes=item["horizon_minutes"],
                offered_ul_mbps=Quantiles(**item["offered_ul_mbps"]),
                offered_dl_mbps=Quantiles(**item["offered_dl_mbps"]),
                new_sessions=Quantiles(**item["new_sessions"]),
                active_sessions=Quantiles(**item["active_sessions"]),
                residual_mbps=Quantiles(**item["residual_mbps"]),
            ))
        return cls(
            forecast_id=data["forecast_id"], model_version=data["model_version"],
            issued_at=data["issued_at"], source_window_end=data["source_window_end"],
            group=GroupKey.from_dict(data["group"]), targets=targets,
            feature_names=list(data.get("feature_names", [])),
            calibration_state=dict(data["calibration_state"]),
            quality_flags=list(data.get("quality_flags", [])),
        )


@dataclass(frozen=True, slots=True)
class ReplicaAction:
    upf_id: str
    action: str
    replicas: int
    ready_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "ready_at", parse_utc(self.ready_at))
        if self.action not in {"scale_out", "scale_in", "hold"}:
            raise ValueError("unsupported replica action")
        require_non_negative("replicas", self.replicas)


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    simulation_only: bool
    budget_sessions: int
    session_types: tuple[str, ...] = ()
    disruption_cost: float = 0.0

    def __post_init__(self) -> None:
        if not self.simulation_only and self.budget_sessions:
            raise ValueError("migration is supported only inside the simulator")
        require_non_negative("budget_sessions", self.budget_sessions)
        require_non_negative("disruption_cost", self.disruption_cost)


@dataclass(slots=True)
class OptimizationRecommendation(Contract):
    schema_version: ClassVar[str] = "optimization-recommendation/1.0"
    recommendation_id: str
    policy_epoch: int
    created_at: datetime
    forecast_id: str
    weights: dict[str, dict[str, float]]
    replica_actions: list[ReplicaAction]
    migration_plan: MigrationPlan | None
    expected_utilization: dict[str, float]
    binding_constraints: list[str]
    slack: dict[str, float]
    objective: float
    fallback_used: bool = False
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        self.created_at = parse_utc(self.created_at)
        self.validate()

    def validate(self) -> None:
        if not self.recommendation_id or self.policy_epoch < 0 or not self.forecast_id:
            raise ValueError("recommendation identity, epoch, and forecast are required")
        for group, values in self.weights.items():
            if not group or not values or any(value < 0 or value > 1 for value in values.values()):
                raise ValueError("weights must be non-empty and in [0, 1]")
            if abs(sum(values.values()) - 1.0) > 1e-9:
                raise ValueError("weights must sum to one")
        for mapping in (self.expected_utilization, self.slack):
            for name, value in mapping.items():
                require_non_negative(name, value)
        if self.fallback_used and not self.fallback_reason:
            raise ValueError("fallback reason is required when fallback is used")


@dataclass(frozen=True, slots=True)
class DecisionTraceEvent:
    sequence: int
    stage: str
    simulated_time: datetime
    wall_time: datetime
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "simulated_time", parse_utc(self.simulated_time))
        object.__setattr__(self, "wall_time", parse_utc(self.wall_time))
        if self.sequence < 1 or not self.stage or not self.message:
            raise ValueError("positive sequence, stage, and message are required")
        if self.status not in {"complete", "warning", "error", "skipped"}:
            raise ValueError("unsupported decision trace status")


@dataclass(slots=True)
class DecisionTrace(Contract):
    schema_version: ClassVar[str] = "decision-trace/1.0"
    trace_id: str
    run_id: str
    policy_epoch: int
    events: list[DecisionTraceEvent]

    def validate(self) -> None:
        if not self.trace_id or not self.run_id or self.policy_epoch < 0:
            raise ValueError("trace identity, run, and epoch are required")
        sequences = [item.sequence for item in self.events]
        if sequences != sorted(set(sequences)):
            raise ValueError("decision events must have unique monotonic sequence numbers")

