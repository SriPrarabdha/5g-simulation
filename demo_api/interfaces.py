from __future__ import annotations

import json
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from schemas import TelemetrySample


class FlowSource(ABC):
    """Replaceable telemetry ingress seam used by the closed-loop runner."""

    @abstractmethod
    def sample(self, at: datetime) -> list[TelemetrySample]:
        raise NotImplementedError


class SyntheticFlowSource(FlowSource):
    def __init__(self, generator: Callable[[datetime], Iterable[TelemetrySample]]) -> None:
        self._generator = generator

    def sample(self, at: datetime) -> list[TelemetrySample]:
        return list(self._generator(at))


class ReplayFlowSource(FlowSource):
    """Replay canonical TelemetrySample JSONL without changing event timestamps."""

    def __init__(self, path: str | Path) -> None:
        self._by_time: dict[datetime, list[TelemetrySample]] = {}
        with Path(path).open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                item = TelemetrySample.from_dict(json.loads(line))
                self._by_time.setdefault(item.event_time, []).append(item)

    def sample(self, at: datetime) -> list[TelemetrySample]:
        return list(self._by_time.get(at, ()))


@dataclass(frozen=True, slots=True)
class PrometheusQuery:
    metric: str
    query: str
    unit: str
    is_counter: bool = False


class PrometheusFlowSource(FlowSource):
    """Configuration-driven Prometheus HTTP API adapter.

    This deliberately performs no C-DOT-specific label guessing. Query templates
    own that mapping and returned series labels are preserved in quality metadata.
    """

    def __init__(
        self,
        base_url: str,
        queries: Iterable[PrometheusQuery],
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout_seconds: float = 3.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.queries = tuple(queries)
        self._opener = opener
        self.timeout_seconds = timeout_seconds

    def sample(self, at: datetime) -> list[TelemetrySample]:
        rows: list[TelemetrySample] = []
        timestamp = at.timestamp()
        for spec in self.queries:
            params = urllib.parse.urlencode({"query": spec.query, "time": timestamp})
            request = urllib.request.Request(f"{self.base_url}/api/v1/query?{params}")
            with self._opener(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read())
            if payload.get("status") != "success":
                raise RuntimeError(f"Prometheus query failed for {spec.metric}")
            for index, result in enumerate(payload.get("data", {}).get("result", [])):
                labels = result.get("metric", {})
                raw_time, raw_value = result["value"]
                event_time = datetime.fromtimestamp(float(raw_time), tz=at.tzinfo)
                rows.append(TelemetrySample(
                    sample_id=f"prom:{spec.metric}:{index}:{raw_time}",
                    event_time=event_time,
                    received_time=at,
                    source_type="prometheus",
                    source_id=self.base_url,
                    metric=spec.metric,
                    value=float(raw_value),
                    unit=spec.unit,
                    is_counter=spec.is_counter,
                    upf_id=labels.get("upf_id"),
                    interface=labels.get("interface"),
                    direction=labels.get("direction"),
                    validity_flags=[f"label:{key}={value}" for key, value in sorted(labels.items())
                                    if key not in {"upf_id", "interface", "direction", "__name__"}],
                ))
        return rows


class ActuatorSink(ABC):
    """Replaceable policy egress seam. Implementations must be atomic."""

    @abstractmethod
    def apply(self, recommendation: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(slots=True)
class SimulationActuator(ActuatorSink):
    current: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def apply(self, recommendation: dict[str, Any]) -> dict[str, Any]:
        candidate = json.loads(json.dumps(recommendation))
        if candidate.get("fallback", {}).get("used") and self.current is not None:
            return {"applied": False, "reason": "retained_last_safe_policy", "policy": self.current}
        if not candidate.get("weights"):
            raise ValueError("recommendation contains no weights")
        self.current = candidate
        self.history.append(candidate)
        return {"applied": True, "reason": "simulation_policy_committed", "policy": candidate}


class AdvisoryFileSink(ActuatorSink):
    """Atomically publish a validated recommendation for external review."""

    def __init__(self, destination: str | Path) -> None:
        self.destination = Path(destination)

    def apply(self, recommendation: dict[str, Any]) -> dict[str, Any]:
        if not recommendation.get("weights"):
            raise ValueError("recommendation contains no weights")
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.destination.with_suffix(self.destination.suffix + ".tmp")
        temporary.write_text(json.dumps(recommendation, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.destination)
        return {"applied": True, "reason": "advisory_atomically_published", "path": str(self.destination)}


class SmfEmsActuator(ActuatorSink):
    """Contract placeholder. Autonomous production actuation is intentionally absent."""

    def apply(self, recommendation: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("C-DOT must provide a supported SMF/EMS control interface")

