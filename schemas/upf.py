from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from .common import Contract, parse_utc, require_non_negative, require_schema


@dataclass(frozen=True, slots=True)
class Capacity:
    ul: float
    dl: float

    def __post_init__(self) -> None:
        if self.ul <= 0 or self.dl <= 0:
            raise ValueError("directional capacities must be positive")


@dataclass(slots=True)
class UPFState(Contract):
    schema_version: ClassVar[str] = "upf-state/1.0"
    measurement_time: datetime
    upf_id: str
    capacity_mbps: Capacity
    safe_utilization: Capacity
    session_capacity: int
    session_safe_utilization: float
    health: str
    zone: str
    eligible_groups: list[str]
    path_latency_ms_by_zone: dict[str, float]
    state_ttl_seconds: int
    calibration_version: str

    def __post_init__(self) -> None:
        self.measurement_time = parse_utc(self.measurement_time)
        self.validate()

    def validate(self) -> None:
        if self.health not in {"healthy", "degraded", "unavailable", "unknown"}:
            raise ValueError(f"unsupported health state: {self.health}")
        if not 0 < self.safe_utilization.ul <= 1 or not 0 < self.safe_utilization.dl <= 1:
            raise ValueError("safe utilization must be in (0, 1]")
        if self.session_capacity <= 0 or not 0 < self.session_safe_utilization <= 1:
            raise ValueError("session capacity and safe utilization must be positive")
        if self.state_ttl_seconds <= 0:
            raise ValueError("state_ttl_seconds must be positive")
        for zone, latency in self.path_latency_ms_by_zone.items():
            require_non_negative(f"latency for {zone}", latency)

    @property
    def safe_capacity_mbps(self) -> Capacity:
        return Capacity(
            ul=self.capacity_mbps.ul * self.safe_utilization.ul,
            dl=self.capacity_mbps.dl * self.safe_utilization.dl,
        )

    @property
    def safe_session_capacity(self) -> int:
        return int(self.session_capacity * self.session_safe_utilization)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UPFState":
        require_schema(data, cls.schema_version)
        return cls(
            measurement_time=data["measurement_time"], upf_id=data["upf_id"],
            capacity_mbps=Capacity(**data["capacity_mbps"]), safe_utilization=Capacity(**data["safe_utilization"]),
            session_capacity=data["session_capacity"], session_safe_utilization=data["session_safe_utilization"],
            health=data["health"], zone=data["zone"], eligible_groups=list(data["eligible_groups"]),
            path_latency_ms_by_zone=dict(data["path_latency_ms_by_zone"]),
            state_ttl_seconds=data["state_ttl_seconds"], calibration_version=data["calibration_version"],
        )

