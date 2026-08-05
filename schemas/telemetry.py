from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from .common import Contract, GroupKey, TimeWindow, parse_utc, require_non_negative, require_schema


TELEMETRY_UNITS = {
    "bytes_total", "packets_total", "bytes", "packets", "sessions", "ratio", "milliseconds"
}


@dataclass(slots=True)
class TelemetrySample(Contract):
    schema_version: ClassVar[str] = "telemetry-sample/1.0"
    sample_id: str
    event_time: datetime
    received_time: datetime
    source_type: str
    source_id: str
    metric: str
    value: float | None
    unit: str
    is_counter: bool
    upf_id: str | None = None
    zone: str | None = None
    dnn: str | None = None
    snssai: str | None = None
    five_qi: int | None = None
    site: str | None = None
    interface: str | None = None
    direction: str | None = None
    reset_epoch: int | None = None
    valid: bool = True
    validity_flags: list[str] = field(default_factory=list)
    restart_id: str | None = None

    def __post_init__(self) -> None:
        self.event_time = parse_utc(self.event_time)
        self.received_time = parse_utc(self.received_time)
        self.validate()

    def validate(self) -> None:
        if not self.sample_id or not self.source_type or not self.source_id or not self.metric:
            raise ValueError("sample and source identity are required")
        if self.unit not in TELEMETRY_UNITS:
            raise ValueError(f"unsupported unit: {self.unit}")
        if self.interface not in {None, "n3", "n6"}:
            raise ValueError("interface must be n3 or n6")
        if self.direction not in {None, "ul", "dl"}:
            raise ValueError("direction must be ul or dl")
        if self.five_qi is not None and self.five_qi <= 0:
            raise ValueError("five_qi must be positive")
        require_non_negative("reset_epoch", self.reset_epoch)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TelemetrySample":
        require_schema(data, cls.schema_version)
        source = data["source"]
        dimensions = data.get("dimensions", {})
        return cls(
            sample_id=data["sample_id"], event_time=data["event_time"], received_time=data["received_time"],
            source_type=source["type"], source_id=source["id"], metric=data["metric"],
            value=data.get("value"), unit=data["unit"], is_counter=data["is_counter"],
            upf_id=dimensions.get("upf_id"), zone=dimensions.get("zone"),
            dnn=dimensions.get("dnn"), snssai=dimensions.get("snssai"),
            five_qi=dimensions.get("five_qi"), site=dimensions.get("site"),
            interface=dimensions.get("interface"),
            direction=dimensions.get("direction"), reset_epoch=data.get("reset_epoch"),
            valid=data.get("valid", True), validity_flags=list(data.get("validity_flags", [])),
            restart_id=data.get("restart_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        result = Contract.to_dict(self)
        result["source"] = {"type": result.pop("source_type"), "id": result.pop("source_id")}
        result["dimensions"] = {
            "upf_id": result.pop("upf_id"), "interface": result.pop("interface"),
            "direction": result.pop("direction"),
        }
        for name in ("zone", "dnn", "snssai", "five_qi", "site"):
            value = result.pop(name)
            if value is not None:
                result["dimensions"][name] = value
        return result


@dataclass(slots=True)
class TrafficSummary:
    offered_ul_bytes: int | None = None
    offered_dl_bytes: int | None = None
    carried_ul_bytes: int | None = None
    carried_dl_bytes: int | None = None
    queued_ul_bytes: int | None = None
    queued_dl_bytes: int | None = None
    dropped_ul_bytes: int | None = None
    dropped_dl_bytes: int | None = None
    rejected_ul_bytes: int | None = None
    rejected_dl_bytes: int | None = None

    def validate(self) -> None:
        for name in self.__dataclass_fields__:
            require_non_negative(name, getattr(self, name))


@dataclass(slots=True)
class SessionSummary:
    active_mean: float | None = None
    active_max: int | None = None
    new: int | None = None
    surviving: int | None = None
    departed: int | None = None
    establishment_failures: int | None = None

    def validate(self) -> None:
        for name in self.__dataclass_fields__:
            require_non_negative(name, getattr(self, name))


@dataclass(slots=True)
class QosSummary:
    latency_p95_ms: float | None = None
    latency_max_ms: float | None = None


@dataclass(slots=True)
class RateStatistics:
    mean_mbps: float | None = None
    p95_mbps: float | None = None
    max_mbps: float | None = None


@dataclass(slots=True)
class DataQuality:
    missing_fraction: float = 0.0
    reset_count: int = 0
    restart_count: int = 0
    restarted: bool = False
    late_sample_count: int = 0
    validity_flags: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not 0.0 <= self.missing_fraction <= 1.0:
            raise ValueError("missing_fraction must be in [0, 1]")
        for name in ("reset_count", "restart_count", "late_sample_count"):
            require_non_negative(name, getattr(self, name))


@dataclass(slots=True)
class DemandBucket(Contract):
    schema_version: ClassVar[str] = "demand-bucket/1.0"
    window: TimeWindow
    group: GroupKey
    upf_id: str | None
    traffic: TrafficSummary
    sessions: SessionSummary
    qos: QosSummary = field(default_factory=QosSummary)
    rate_statistics: dict[str, RateStatistics] = field(default_factory=dict)
    data_quality: DataQuality = field(default_factory=DataQuality)

    def validate(self) -> None:
        self.traffic.validate()
        self.sessions.validate()
        self.data_quality.validate()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DemandBucket":
        require_schema(data, cls.schema_version)
        return cls(
            window=TimeWindow.from_dict(data["window"]), group=GroupKey.from_dict(data["group"]),
            upf_id=data.get("upf_id"), traffic=TrafficSummary(**data["traffic"]),
            sessions=SessionSummary(**data["sessions"]), qos=QosSummary(**data.get("qos", {})),
            rate_statistics={key: RateStatistics(**value) for key, value in data.get("rate_statistics", {}).items()},
            data_quality=DataQuality(**data.get("data_quality", {})),
        )
