from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from .common import Contract, GroupKey, TimeWindow, parse_utc, require_non_negative, require_schema


@dataclass(frozen=True, slots=True)
class Quantiles:
    p50: float
    p95: float
    p90: float | None = None

    def __post_init__(self) -> None:
        for name in ("p50", "p90", "p95"):
            require_non_negative(name, getattr(self, name))
        if self.p90 is not None and not self.p50 <= self.p90 <= self.p95:
            raise ValueError("quantiles must be monotonic")
        if self.p90 is None and self.p50 > self.p95:
            raise ValueError("quantiles must be monotonic")


@dataclass(frozen=True, slots=True)
class ExistingLoad:
    upf_id: str
    surviving_sessions: Quantiles
    ul_mbps: Quantiles
    dl_mbps: Quantiles


@dataclass(slots=True)
class Forecast(Contract):
    schema_version: ClassVar[str] = "forecast/1.0"
    forecast_id: str
    issued_at: datetime
    source_window_end: datetime
    target_window: TimeWindow
    horizon_steps: int
    group: GroupKey
    new_session_count: Quantiles
    new_load_ul_mbps: Quantiles
    new_load_dl_mbps: Quantiles
    existing_load_by_upf: list[ExistingLoad]
    model_version: str
    quality_flags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.issued_at = parse_utc(self.issued_at)
        self.source_window_end = parse_utc(self.source_window_end)
        self.validate()

    def validate(self) -> None:
        if self.horizon_steps not in {1, 2}:
            raise ValueError("horizon_steps must be 1 or 2")
        if self.source_window_end > self.target_window.start:
            raise ValueError("forecast features may not overlap the target window")
        if not self.forecast_id or not self.model_version:
            raise ValueError("forecast_id and model_version are required")
        ids = [item.upf_id for item in self.existing_load_by_upf]
        if len(ids) != len(set(ids)):
            raise ValueError("existing_load_by_upf contains duplicate UPFs")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Forecast":
        require_schema(data, cls.schema_version)
        new_load = data["new_load_mbps"]
        return cls(
            forecast_id=data["forecast_id"], issued_at=data["issued_at"],
            source_window_end=data["source_window_end"], target_window=TimeWindow.from_dict(data["target_window"]),
            horizon_steps=data["horizon_steps"], group=GroupKey.from_dict(data["group"]),
            new_session_count=Quantiles(**data["new_session_count"]),
            new_load_ul_mbps=Quantiles(**new_load["ul"]), new_load_dl_mbps=Quantiles(**new_load["dl"]),
            existing_load_by_upf=[
                ExistingLoad(
                    upf_id=item["upf_id"], surviving_sessions=Quantiles(**item["surviving_sessions"]),
                    ul_mbps=Quantiles(**item["ul_mbps"]), dl_mbps=Quantiles(**item["dl_mbps"]),
                ) for item in data.get("existing_load_by_upf", [])
            ],
            model_version=data["model_version"], quality_flags=list(data.get("quality_flags", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        result = Contract.to_dict(self)
        result["new_load_mbps"] = {
            "ul": result.pop("new_load_ul_mbps"),
            "dl": result.pop("new_load_dl_mbps"),
        }
        return result

