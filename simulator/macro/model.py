from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from schemas.common import iso_utc


@dataclass(slots=True)
class Cohort:
    group_id: str
    upf_id: str
    remaining_steps: int
    count: int
    ul_mbps_per_session: float
    dl_mbps_per_session: float


@dataclass(slots=True)
class DirectionResult:
    offered_bytes: float
    carried_bytes: float
    queued_bytes: float
    dropped_bytes: float
    rejected_bytes: float
    capacity_mbps: float
    safe_capacity_mbps: float


@dataclass(slots=True)
class UPFStepResult:
    upf_id: str
    health: str
    active_sessions: int
    new_sessions: int
    departed_sessions: int
    establishment_failures: int
    ul: DirectionResult
    dl: DirectionResult


@dataclass(slots=True)
class StepResult:
    scenario_id: str
    seed: int
    step: int
    window_start: datetime
    window_end: datetime
    policy_id: str
    group_arrivals: dict[str, int]
    group_rejections: dict[str, int]
    upfs: list[UPFStepResult]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["window_start"] = iso_utc(self.window_start)
        result["window_end"] = iso_utc(self.window_end)
        return result

