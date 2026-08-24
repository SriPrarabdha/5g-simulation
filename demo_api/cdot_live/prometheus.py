from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import LiveConfig


class PrometheusError(RuntimeError):
    pass


class PrometheusClient:
    def __init__(self, config: LiveConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.config.timeout_seconds)
        try:
            response = await client.get(self.config.prometheus_url + path, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise PrometheusError(str(error)) from error
        finally:
            if owns:
                await client.aclose()
        if isinstance(payload, dict) and payload.get("status") not in {None, "success"}:
            raise PrometheusError(str(payload.get("error", "Prometheus query failed")))
        return payload

    async def ready(self) -> bool:
        try:
            owns = self._client is None
            client = self._client or httpx.AsyncClient(timeout=self.config.timeout_seconds)
            response = await client.get(self.config.prometheus_url + "/-/ready")
            if owns:
                await client.aclose()
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def range_query(
        self, query: str, start: datetime, end: datetime, *, step_seconds: int = 15
    ) -> list[dict[str, Any]]:
        payload = await self._get("/api/v1/query_range", {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step_seconds,
        })
        return list(payload.get("data", {}).get("result", []))

    async def instant_query(self, query: str, at: datetime | None = None) -> list[dict[str, Any]]:
        payload = await self._get("/api/v1/query", {
            "query": query,
            "time": (at or datetime.now(timezone.utc)).timestamp(),
        })
        return list(payload.get("data", {}).get("result", []))

    async def traffic_history(self, now: datetime | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        now = now or datetime.now(timezone.utc)
        start = now - timedelta(hours=self.config.history_hours)
        ul_query = self.config.queries["ul"]
        dl_query = self.config.queries["dl"]
        return tuple(await asyncio.gather(
            self.range_query(ul_query, start, now),
            self.range_query(dl_query, start, now),
        ))  # type: ignore[return-value]

    def _upf_for_labels(self, labels: dict[str, Any]) -> str | None:
        direct = labels.get("upf") or labels.get("upf_id")
        if direct in self.config.mappings:
            return str(direct)
        for metric, mapping in self.config.mappings.items():
            if labels.get("job") == mapping.job or labels.get("pod") == mapping.pod:
                return metric
        return None

    async def operational_state(self, now: datetime | None = None) -> dict[str, dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        names = ("sessions", "cpu", "memory", "tsi", "drop", "forwarding_efficiency")
        results = await asyncio.gather(
            *(self.instant_query(self.config.queries[name], now) for name in names),
            return_exceptions=True,
        )
        state = {
            metric: {
                "upf": metric, "smf": mapping.smf, "job": mapping.job, "pod": mapping.pod,
                "sessions": None, "cpu": None, "memory_bytes": None, "tsi": None,
                "drop_rate_percent": None, "forwarding_efficiency_percent": None,
                "health": "unknown", "measurement_time": None,
            }
            for metric, mapping in self.config.mappings.items()
        }
        fields = {
            "sessions": "sessions", "cpu": "cpu", "memory": "memory_bytes", "tsi": "tsi",
            "drop": "drop_rate_percent", "forwarding_efficiency": "forwarding_efficiency_percent",
        }
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                continue
            for series in result:
                upf = self._upf_for_labels(dict(series.get("metric", {})))
                value = series.get("value")
                if upf is None or not isinstance(value, list) or len(value) != 2:
                    continue
                try:
                    measured = datetime.fromtimestamp(float(value[0]), timezone.utc)
                    state[upf][fields[name]] = float(value[1])
                    previous = state[upf]["measurement_time"]
                    if previous is None or measured > datetime.fromisoformat(previous.replace("Z", "+00:00")):
                        state[upf]["measurement_time"] = measured.isoformat().replace("+00:00", "Z")
                except (TypeError, ValueError):
                    continue
        for item in state.values():
            measured = item["measurement_time"]
            age = (now - datetime.fromisoformat(measured.replace("Z", "+00:00"))).total_seconds() if measured else None
            item["age_seconds"] = age
            complete = all(item[field] is not None for field in (
                "sessions", "cpu", "memory_bytes", "tsi", "drop_rate_percent",
                "forwarding_efficiency_percent",
            ))
            item["operational_state_complete"] = complete
            item["health"] = "healthy" if complete and age is not None and age <= self.config.stale_seconds else "unknown"
        return state
