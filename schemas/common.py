from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, ClassVar


def parse_utc(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return parse_utc(value).isoformat().replace("+00:00", "Z")


class Contract:
    schema_version: ClassVar[str]

    def validate(self) -> None:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        self.validate()

        def convert(value: Any) -> Any:
            if isinstance(value, datetime):
                return iso_utc(value)
            if hasattr(value, "__dataclass_fields__"):
                return {key: convert(item) for key, item in asdict(value).items()}
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [convert(item) for item in value]
            return value

        result = convert(self)
        result["schema_version"] = self.schema_version
        return result


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", parse_utc(self.start))
        object.__setattr__(self, "end", parse_utc(self.end))
        if self.end <= self.start:
            raise ValueError("window end must be after start")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TimeWindow":
        return cls(start=parse_utc(data["start"]), end=parse_utc(data["end"]))


@dataclass(frozen=True, slots=True)
class GroupKey:
    zone: str
    dnn: str
    snssai: str
    five_qi: int | None = None

    def __post_init__(self) -> None:
        if not self.zone or not self.dnn or not self.snssai:
            raise ValueError("zone, dnn, and snssai are required")
        if self.five_qi is not None and self.five_qi <= 0:
            raise ValueError("five_qi must be positive")

    @property
    def selection_id(self) -> str:
        return f"{self.zone}|{self.dnn}|{self.snssai}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupKey":
        return cls(
            zone=data["zone"],
            dnn=data["dnn"],
            snssai=data["snssai"],
            five_qi=data.get("five_qi"),
        )


def require_schema(data: dict[str, Any], expected: str) -> None:
    if data.get("schema_version") != expected:
        raise ValueError(f"expected {expected}, got {data.get('schema_version')!r}")


def require_non_negative(name: str, value: float | int | None) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{name} must be non-negative")

