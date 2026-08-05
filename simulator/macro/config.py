from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from schemas import GroupKey
from schemas.common import parse_utc


@dataclass(frozen=True, slots=True)
class GroupProfile:
    key: GroupKey
    arrivals_per_step: float
    lifetime_steps_min: int
    lifetime_steps_max: int
    offered_ul_mbps_per_session: float
    offered_dl_mbps_per_session: float
    eligible_upfs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.arrivals_per_step < 0:
            raise ValueError("arrivals_per_step must be non-negative")
        if self.lifetime_steps_min < 1 or self.lifetime_steps_max < self.lifetime_steps_min:
            raise ValueError("invalid lifetime range")
        if self.offered_ul_mbps_per_session < 0 or self.offered_dl_mbps_per_session < 0:
            raise ValueError("offered demand must be non-negative")
        if not self.eligible_upfs:
            raise ValueError(f"group {self.key.selection_id} must declare eligible UPFs")


@dataclass(frozen=True, slots=True)
class UPFProfile:
    upf_id: str
    zone: str
    capacity_ul_mbps: float
    capacity_dl_mbps: float
    safe_utilization_ul: float
    safe_utilization_dl: float
    session_capacity: int
    session_safe_utilization: float
    queue_limit_seconds: float
    path_latency_ms_by_zone: dict[str, float]

    def __post_init__(self) -> None:
        if self.capacity_ul_mbps <= 0 or self.capacity_dl_mbps <= 0:
            raise ValueError("UPF directional capacity must be positive")
        if not 0 < self.safe_utilization_ul <= 1 or not 0 < self.safe_utilization_dl <= 1:
            raise ValueError("safe utilization must be in (0, 1]")
        if self.session_capacity <= 0 or not 0 < self.session_safe_utilization <= 1:
            raise ValueError("session limits must be positive")
        if self.queue_limit_seconds < 0:
            raise ValueError("queue_limit_seconds must be non-negative")


@dataclass(frozen=True, slots=True)
class ScenarioEvent:
    step: int
    event_type: str
    upf_id: str
    ul_factor: float | None = None
    dl_factor: float | None = None
    health: str | None = None

    def __post_init__(self) -> None:
        if self.step < 0:
            raise ValueError("event step must be non-negative")
        if self.event_type not in {"capacity_factor", "health"}:
            raise ValueError(f"unsupported event type: {self.event_type}")
        if self.event_type == "capacity_factor":
            if self.ul_factor is None and self.dl_factor is None:
                raise ValueError("capacity event requires a directional factor")
            if any(value is not None and value < 0 for value in (self.ul_factor, self.dl_factor)):
                raise ValueError("capacity factors must be non-negative")
        if self.event_type == "health" and self.health not in {"healthy", "degraded", "unavailable", "unknown"}:
            raise ValueError("health event requires a valid state")


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    scenario_id: str
    seed: int
    start_time: datetime
    steps: int
    step_seconds: int = 30
    decision_interval_steps: int = 20
    groups: tuple[GroupProfile, ...] = field(default_factory=tuple)
    upfs: tuple[UPFProfile, ...] = field(default_factory=tuple)
    events: tuple[ScenarioEvent, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_time", parse_utc(self.start_time))
        if not self.scenario_id or self.steps <= 0 or self.step_seconds <= 0:
            raise ValueError("scenario identity, steps, and step_seconds must be positive")
        if self.decision_interval_steps <= 0:
            raise ValueError("decision_interval_steps must be positive")
        upf_ids = {upf.upf_id for upf in self.upfs}
        if len(upf_ids) != len(self.upfs) or not upf_ids:
            raise ValueError("UPF IDs must be unique and non-empty")
        group_ids = {group.key.selection_id for group in self.groups}
        if len(group_ids) != len(self.groups) or not group_ids:
            raise ValueError("selection groups must be unique and non-empty")
        for group in self.groups:
            unknown = set(group.eligible_upfs) - upf_ids
            if unknown:
                raise ValueError(f"group {group.key.selection_id} references unknown UPFs: {sorted(unknown)}")
        for event in self.events:
            if event.upf_id not in upf_ids:
                raise ValueError(f"event references unknown UPF: {event.upf_id}")
            if event.step >= self.steps:
                raise ValueError("event step falls outside the scenario")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScenarioConfig":
        groups = tuple(
            GroupProfile(
                key=GroupKey.from_dict(item["key"]), arrivals_per_step=item["arrivals_per_step"],
                lifetime_steps_min=item["lifetime_steps"]["min"],
                lifetime_steps_max=item["lifetime_steps"]["max"],
                offered_ul_mbps_per_session=item["offered_mbps_per_session"]["ul"],
                offered_dl_mbps_per_session=item["offered_mbps_per_session"]["dl"],
                eligible_upfs=tuple(item["eligible_upfs"]),
            ) for item in data["groups"]
        )
        upfs = tuple(
            UPFProfile(
                upf_id=item["upf_id"], zone=item["zone"],
                capacity_ul_mbps=item["capacity_mbps"]["ul"],
                capacity_dl_mbps=item["capacity_mbps"]["dl"],
                safe_utilization_ul=item["safe_utilization"]["ul"],
                safe_utilization_dl=item["safe_utilization"]["dl"],
                session_capacity=item["session_capacity"],
                session_safe_utilization=item["session_safe_utilization"],
                queue_limit_seconds=item.get("queue_limit_seconds", 0),
                path_latency_ms_by_zone=dict(item.get("path_latency_ms_by_zone", {})),
            ) for item in data["upfs"]
        )
        events = tuple(ScenarioEvent(**item) for item in data.get("events", []))
        return cls(
            scenario_id=data["scenario_id"], seed=data["seed"], start_time=data["start_time"],
            steps=data["steps"], step_seconds=data.get("step_seconds", 30),
            decision_interval_steps=data.get("decision_interval_steps", 20),
            groups=groups, upfs=upfs, events=events,
        )


def load_scenario(path: str | Path) -> ScenarioConfig:
    with Path(path).open(encoding="utf-8") as stream:
        return ScenarioConfig.from_dict(json.load(stream))

