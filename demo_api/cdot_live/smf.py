from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from functools import reduce
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urlparse


def canonical_state_hash(state: Any) -> str:
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def integer_weights(weights: dict[str, float]) -> dict[str, int]:
    """Largest-remainder conversion, deterministic by UPF id, summing to 100."""
    clean = {str(key): max(0.0, float(value)) for key, value in weights.items() if float(value) > 0}
    total = sum(clean.values())
    if not clean or not math.isfinite(total) or total <= 0:
        raise ValueError("weights must contain a positive finite value")
    scaled = {key: value / total * 100 for key, value in clean.items()}
    result = {key: math.floor(value) for key, value in scaled.items()}
    remaining = 100 - sum(result.values())
    order = sorted(scaled, key=lambda key: (-(scaled[key] - result[key]), key))
    for key in order[:remaining]:
        result[key] += 1
    return result


def reduced_ratio(weights_100: dict[str, int]) -> dict[str, int]:
    divisor = reduce(math.gcd, weights_100.values()) if weights_100 else 1
    return {key: value // divisor for key, value in weights_100.items()} if divisor > 1 else dict(weights_100)


def tuple_key(item: dict[str, Any]) -> str:
    tac = item.get("tac", item.get("loc", item.get("tacid")))
    dnn = item.get("dnn", item.get("dnnid"))
    dscp = item.get("dscp", 0)
    return f"tac-{tac}|{dnn}|dscp-{dscp}"


def extract_tuples(state: Any) -> list[dict[str, Any]]:
    if isinstance(state, list):
        return [dict(item) for item in state if isinstance(item, dict)]
    if not isinstance(state, dict):
        return []
    for key in ("rules", "items", "data", "policies", "config"):
        value = state.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
    if all(isinstance(value, dict) for value in state.values()):
        rows = []
        for key, value in state.items():
            item = dict(value)
            item.setdefault("selection", key)
            rows.append(item)
        return rows
    return []


def extract_weights(item: dict[str, Any]) -> dict[str, int]:
    value = item.get("weights", item.get("upf_weights", item.get("upfs", {})))
    if isinstance(value, dict):
        return {str(key): int(weight) for key, weight in value.items()}
    if isinstance(value, list):
        result = {}
        for row in value:
            if isinstance(row, dict):
                key = row.get("upf", row.get("upf_id", row.get("name")))
                weight = row.get("weight", row.get("value"))
                if key is not None and weight is not None:
                    result[str(key)] = int(weight)
        return result
    return {}


def with_weights(item: dict[str, Any], weights: dict[str, int]) -> dict[str, Any]:
    result = json.loads(json.dumps(item))
    field = "weights" if "weights" in result or not any(key in result for key in ("upf_weights", "upfs")) else (
        "upf_weights" if "upf_weights" in result else "upfs"
    )
    existing = result.get(field)
    if isinstance(existing, list):
        result[field] = [{"upf": key, "weight": value} for key, value in sorted(weights.items())]
    else:
        result[field] = dict(sorted(weights.items()))
    result["weight_ratio"] = reduced_ratio(weights)
    return result


@dataclass(slots=True)
class H2Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body or b"null")


class H2CSmfClient:
    """Minimal cleartext HTTP/2 prior-knowledge client for `/upf-admin`."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 3.0,
        connector: Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]] | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError("SMF URL must be a cleartext http URL")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.base_path = parsed.path.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._connector = connector or asyncio.open_connection

    async def request(self, method: str, path: str, payload: Any | None = None) -> H2Response:
        try:
            import h2.config
            import h2.connection
            import h2.events
        except ImportError as error:  # pragma: no cover - dependency contract
            raise RuntimeError("h2 is required for the C-DOT SMF client") from error

        async def exchange() -> H2Response:
            reader, writer = await self._connector(self.host, self.port)
            configuration = h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
            connection = h2.connection.H2Connection(config=configuration)
            connection.initiate_connection()
            writer.write(connection.data_to_send())
            stream_id = connection.get_next_available_stream_id()
            body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
            target = self.base_path.rstrip("/") + "/" + path.lstrip("/")
            headers = [
                (":method", method.upper()), (":scheme", "http"),
                (":authority", f"{self.host}:{self.port}"), (":path", target),
                ("accept", "application/json"),
            ]
            if body:
                headers.extend((("content-type", "application/json"), ("content-length", str(len(body)))))
            connection.send_headers(stream_id, headers, end_stream=not body)
            if body:
                connection.send_data(stream_id, body, end_stream=True)
            writer.write(connection.data_to_send())
            await writer.drain()
            response_headers: dict[str, str] = {}
            response_body = bytearray()
            ended = False
            try:
                while not ended:
                    data = await reader.read(65535)
                    if not data:
                        break
                    for event in connection.receive_data(data):
                        if isinstance(event, h2.events.ResponseReceived):
                            response_headers.update((str(key), str(value)) for key, value in event.headers)
                        elif isinstance(event, h2.events.DataReceived):
                            response_body.extend(event.data)
                            connection.acknowledge_received_data(event.flow_controlled_length, stream_id)
                        elif isinstance(event, h2.events.StreamEnded) and event.stream_id == stream_id:
                            ended = True
                    pending = connection.data_to_send()
                    if pending:
                        writer.write(pending)
                        await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
            if not ended:
                raise RuntimeError("SMF closed the h2c stream before a complete response")
            return H2Response(int(response_headers.get(":status", "0")), response_headers, bytes(response_body))

        return await asyncio.wait_for(exchange(), timeout=self.timeout_seconds)

    async def get_state(self) -> Any:
        response = await self.request("GET", "/upf-admin")
        if not 200 <= response.status < 300:
            raise RuntimeError(f"SMF GET /upf-admin returned HTTP {response.status}")
        return response.json()

    async def post_tuple(self, payload: dict[str, Any]) -> Any:
        response = await self.request("POST", "/upf-admin", payload)
        if not 200 <= response.status < 300:
            raise RuntimeError(f"SMF POST /upf-admin returned HTTP {response.status}")
        return response.json() if response.body else None
