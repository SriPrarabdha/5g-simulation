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
    arrival_step: int


@dataclass(slots=True)
class DirectionResult:
    offered_bytes: float
    new_session_offered_bytes: float
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
class GroupUPFBucketResult:
    """Compact joint group/UPF state emitted once per decision bucket."""

    group_id: str
    zone: str
    dnn: str
    snssai: str
    five_qi: int | None
    upf_id: str
    bucket_seconds: int
    active_sessions: int
    admitted_sessions: int
    establishment_failures: int
    offered_ul_mbps: float
    offered_dl_mbps: float


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
    group_upf_admissions: dict[str, dict[str, int]]
    unplaced_rejected_ul_bytes: float
    unplaced_rejected_dl_bytes: float
    group_upf_buckets: list[GroupUPFBucketResult]
    upfs: list[UPFStepResult]
    group_generated_load_mbps: dict[str, dict[str, float]]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        # Preserve the frozen v1 JSON contract byte-for-byte. Variable-rate
        # traffic-v2 steps populate this extension; v1 steps leave it absent.
        if not result["group_generated_load_mbps"]:
            result.pop("group_generated_load_mbps")
        result["window_start"] = iso_utc(self.window_start)
        result["window_end"] = iso_utc(self.window_end)
        return result
