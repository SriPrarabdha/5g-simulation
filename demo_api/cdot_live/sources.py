"""Class-rate ingestion for the C-DOT live plane.

Two sources sit behind one interface so the whole demo can be built, tested and
rehearsed with the C-DOT lab down:

* :class:`ReplaySource` reads the recorded Grafana CSV drop.
* :class:`PrometheusSource` reads their live Prometheus.

Both emit :class:`ClassRate` -- one packet-rate sample for one
``(upf, tac, dnn, dscp)`` session class.  Everything downstream consumes only
this record, so switching source never changes the analysis.
"""

from __future__ import annotations

import asyncio
import csv
import math
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol
from zoneinfo import ZoneInfo

from .config import LiveConfig


RATE_RE = re.compile(r"^([-0-9.]+)\s*([kMG]?)p/s$")
CLASS_RE = re.compile(r"^upf=(upf-\d+):loc=(\d+):dnn=(\d+):dscp(\d+)$")
_MULTIPLIER = {"": 1.0, "k": 1e3, "M": 1e6, "G": 1e9}


class SourceError(RuntimeError):
    pass


def parse_rate(value: str | float | int) -> float:
    """Parse a Grafana rate cell such as ``6.55 kp/s`` into packets/second."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"null", "nan", "-"}:
        return float("nan")
    match = RATE_RE.fullmatch(text)
    if match:
        return float(match.group(1)) * _MULTIPLIER[match.group(2)]
    try:
        return float(text)
    except ValueError:
        return float("nan")


@dataclass(frozen=True, slots=True)
class ClassRate:
    """One packet-rate sample for one session class, in packets/second."""

    t: datetime
    upf: str
    tac: int
    dnn: str
    dscp: int
    ul_pps: float
    dl_pps: float

    @property
    def total_pps(self) -> float:
        return self.ul_pps + self.dl_pps


class ClassRateSource(Protocol):
    async def window(self, start: datetime, end: datetime) -> list[ClassRate]: ...
    def describe(self) -> dict[str, Any]: ...


# --------------------------------------------------------------------- replay


class ReplaySource:
    """Serve the recorded C-DOT CSV drop as class rates.

    The trace is loaded once and kept in memory (roughly 700 timestamps x 32
    classes).  Timestamps in the export are naive local time; the tz is
    configurable because Grafana stamps them in the browser's zone.
    """

    def __init__(self, config: LiveConfig, root: str | Path | None = None) -> None:
        self.config = config
        self.root = Path(root or config.replay_root)
        self._rows: list[ClassRate] = []
        self._times: list[datetime] = []
        self._load_error: str | None = None
        try:
            self._load()
        except (OSError, ValueError, StopIteration, IndexError) as error:
            self._load_error = f"{type(error).__name__}: {error}"

    # -- loading

    def _one_direction(self, pattern: str) -> dict[tuple[datetime, tuple[str, int, str, int]], float]:
        matches = sorted(self.root.glob(pattern))
        if not matches:
            raise SourceError(f"no C-DOT export matching {pattern!r} under {self.root}")
        path = matches[0]
        zone = ZoneInfo(self.config.replay_timezone)
        with path.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.reader(stream))
        if len(rows) < 2:
            raise SourceError(f"{path.name} has no data rows")
        header, body = rows[0], rows[1:]

        columns: list[tuple[int, tuple[str, int, str, int]]] = []
        for index, label in enumerate(header[1:], start=1):
            match = CLASS_RE.fullmatch(label.strip().strip('"'))
            if not match:
                continue
            upf, tac, dnn_id, dscp = match.groups()
            dnn = self.config.dnn.get(dnn_id)
            upf = self.config.apply_permutation(upf)
            if dnn and upf in self.config.mappings:
                columns.append((index, (upf, int(tac), dnn, int(dscp))))
        if not columns:
            raise SourceError(f"{path.name} exposed no recognisable session-class columns")

        out: dict[tuple[datetime, tuple[str, int, str, int]], float] = {}
        for row in body:
            if not row or not row[0].strip():
                continue
            try:
                stamp = datetime.fromisoformat(row[0].strip())
            except ValueError:
                continue
            stamp = stamp.replace(tzinfo=zone).astimezone(timezone.utc)
            for index, key in columns:
                if index >= len(row):
                    continue
                value = parse_rate(row[index])
                if math.isfinite(value) and value >= 0:
                    out[(stamp, key)] = value
        return out

    def _load(self) -> None:
        ul = self._one_direction("UPF wise Uplink*.csv")
        dl = self._one_direction("UPF wise Downlink*.csv")
        # UL and DL are exported on slightly offset grids; snap DL onto the UL
        # grid by nearest timestamp rather than dropping non-matching rows.
        dl_times = sorted({stamp for stamp, _ in dl})
        rows: list[ClassRate] = []
        for (stamp, key), ul_value in sorted(ul.items(), key=lambda item: (item[0][0], item[0][1])):
            dl_value = dl.get((stamp, key))
            if dl_value is None and dl_times:
                dl_value = dl.get((_nearest(dl_times, stamp), key))
            upf, tac, dnn, dscp = key
            rows.append(ClassRate(
                stamp, upf, tac, dnn, dscp,
                ul_pps=ul_value if ul_value and math.isfinite(ul_value) else 0.0,
                dl_pps=dl_value if dl_value and math.isfinite(dl_value) else 0.0,
            ))
        rows.sort(key=lambda item: item.t)
        self._rows = rows
        self._times = [item.t for item in rows]

    # -- interface

    async def window(self, start: datetime, end: datetime) -> list[ClassRate]:
        if self._load_error:
            raise SourceError(self._load_error)
        left = bisect_left(self._times, start)
        right = bisect_right(self._times, end)
        return self._rows[left:right]

    def span(self) -> tuple[datetime, datetime] | None:
        if not self._times:
            return None
        return self._times[0], self._times[-1]

    def describe(self) -> dict[str, Any]:
        span = self.span()
        return {
            "mode": "replay",
            "root": str(self.root),
            "timezone": self.config.replay_timezone,
            "samples": len(self._rows),
            "trace_start": span[0].isoformat().replace("+00:00", "Z") if span else None,
            "trace_end": span[1].isoformat().replace("+00:00", "Z") if span else None,
            "load_error": self._load_error,
        }


def _nearest(ordered: list[datetime], target: datetime) -> datetime:
    index = bisect_left(ordered, target)
    if index == 0:
        return ordered[0]
    if index >= len(ordered):
        return ordered[-1]
    before, after = ordered[index - 1], ordered[index]
    return before if (target - before) <= (after - target) else after


class ReplayClock:
    """Map wall-clock time onto trace time so a recording can drive a live UI."""

    def __init__(self, trace_start: datetime, trace_end: datetime, *, speed: float = 1.0) -> None:
        self.trace_start = trace_start
        self.trace_end = trace_end
        self.speed = max(0.1, float(speed))
        self._anchor_wall = datetime.now(timezone.utc)
        self._anchor_trace = trace_start

    def seek(self, trace_time: datetime) -> None:
        self._anchor_wall = datetime.now(timezone.utc)
        self._anchor_trace = max(self.trace_start, min(self.trace_end, trace_time))

    def now(self) -> datetime:
        elapsed = (datetime.now(timezone.utc) - self._anchor_wall).total_seconds() * self.speed
        position = self._anchor_trace + timedelta(seconds=elapsed)
        if position > self.trace_end:  # loop the recording, as C-DOT's generator does
            span = (self.trace_end - self.trace_start).total_seconds()
            if span > 0:
                overshoot = (position - self.trace_start).total_seconds() % span
                return self.trace_start + timedelta(seconds=overshoot)
        return position


# ----------------------------------------------------------------- prometheus


class PrometheusSource:
    """Read live per-class packet rates from C-DOT's Prometheus.

    ``rate()`` is evaluated server-side rather than differencing counters here:
    Prometheus already handles resets, staleness and irregular scrapes.
    """

    def __init__(self, config: LiveConfig, *, client: Any | None = None) -> None:
        self.config = config
        self._client = client
        self._owned: Any | None = None
        self.last_error: str | None = None
        # Per-window diagnostics.  The autopilot's health log needs to tell
        # "Prometheus is down" apart from "Prometheus is up and answering with
        # zero series", which is what a wrong metric name looks like.
        self.last_stats: dict[str, Any] = {}

    async def _http(self) -> Any:
        if self._client is not None:
            return self._client
        if self._owned is None:
            import httpx

            self._owned = httpx.AsyncClient(timeout=self.config.timeout_seconds)
        return self._owned

    async def aclose(self) -> None:
        if self._owned is not None:
            await self._owned.aclose()
            self._owned = None

    async def _range(self, query: str, start: datetime, end: datetime, step: int) -> list[dict[str, Any]]:
        import httpx

        client = await self._http()
        try:
            response = await client.get(
                self.config.prometheus_url + "/api/v1/query_range",
                params={"query": query, "start": start.timestamp(), "end": end.timestamp(), "step": step},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise SourceError(f"Prometheus query_range failed: {error}") from error
        if payload.get("status") != "success":
            raise SourceError(str(payload.get("error", "Prometheus query failed")))
        return list(payload.get("data", {}).get("result", []))

    def _rate_query(self, direction: str) -> str:
        raw = self.config.queries[direction]
        if "rate(" in raw or "irate(" in raw or "increase(" in raw:
            return raw
        window = max(60, self.config.cadence.telemetry_step_seconds * 2)
        return f"rate({raw}[{window}s])"

    def _key(self, labels: dict[str, Any]) -> tuple[str, int, str, int] | None:
        upf = str(labels.get("upf") or labels.get("upf_id") or "")
        upf = self.config.apply_permutation(upf)
        if upf not in self.config.mappings:
            return None
        raw_tac = labels.get("loc", labels.get("tac", labels.get("locid")))
        raw_dnn = labels.get("dnn", labels.get("dnnid"))
        try:
            tac = int(raw_tac)
            dscp = int(str(labels.get("dscp", "0")).removeprefix("dscp") or 0)
        except (TypeError, ValueError):
            return None
        dnn = self.config.dnn.get(str(raw_dnn), str(raw_dnn) if raw_dnn else None)
        if not dnn:
            return None
        return upf, tac, dnn, dscp

    async def window(self, start: datetime, end: datetime) -> list[ClassRate]:
        step = self.config.cadence.telemetry_step_seconds
        ul_result, dl_result = await asyncio.gather(
            self._range(self._rate_query("ul"), start, end, step),
            self._range(self._rate_query("dl"), start, end, step),
        )
        merged: dict[tuple[datetime, tuple[str, int, str, int]], list[float]] = {}
        stats: dict[str, Any] = {
            "series_returned": {"ul": len(ul_result), "dl": len(dl_result)},
            "series_matched": {"ul": 0, "dl": 0},
            "rejected_labels": [],
        }
        self.last_error = None
        for result, slot, direction in ((ul_result, 0, "ul"), (dl_result, 1, "dl")):
            unmatched = 0
            for series in result:
                labels = dict(series.get("metric", {}))
                key = self._key(labels)
                if key is None:
                    unmatched += 1
                    if len(stats["rejected_labels"]) < 3:
                        stats["rejected_labels"].append(labels)
                    continue
                stats["series_matched"][direction] += 1
                for point in series.get("values", []):
                    try:
                        stamp = datetime.fromtimestamp(float(point[0]), timezone.utc)
                        value = float(point[1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if not math.isfinite(value) or value < 0:
                        continue
                    merged.setdefault((stamp, key), [0.0, 0.0])[slot] = value
            if result and unmatched == len(result):
                self.last_error = (
                    "every returned series failed label normalisation -- expected labels "
                    "upf/loc/dnn/dscp; check the metric names with C-DOT"
                )
            elif not result:
                self.last_error = (
                    f"Prometheus answered the {direction} query with zero series -- the metric "
                    f"name {self.config.queries.get(direction)!r} probably does not exist"
                )
        rows = [
            ClassRate(stamp, key[0], key[1], key[2], key[3], values[0], values[1])
            for (stamp, key), values in merged.items()
        ]
        rows.sort(key=lambda item: item.t)
        stats["samples"] = len(rows)
        stats["window"] = [
            start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        ]
        stats["error"] = self.last_error
        self.last_stats = stats
        return rows

    async def diagnose(self) -> dict[str, Any]:
        """Explain a zero-series answer: wrong name, or dead exporter?

        Both look identical in a ``query_range`` result -- an empty list -- but
        they need opposite responses from C-DOT: fix the config, or restart the
        exporter.  ``/api/v1/series`` over a wide window separates them, and the
        newest timestamp says how long the feed has been silent.
        """
        import httpx

        out: dict[str, Any] = {}
        try:
            client = await self._http()
            now = datetime.now(timezone.utc)
            # Whether the UPF exporters are even being scraped.  A stale metric
            # has three causes needing three different fixes: the exporter is
            # down, the metric name is wrong, or -- as happened here --
            # Prometheus restarted with a config that no longer lists the
            # target at all.  The first two look identical without this.
            out["scrape"] = await self._scrape_targets(client)
            for direction in ("ul", "dl"):
                name = self.config.queries.get(direction, "")
                entry: dict[str, Any] = {"metric": name}
                response = await client.get(
                    self.config.prometheus_url + "/api/v1/series",
                    params={
                        "match[]": name,
                        "start": (now - timedelta(days=14)).timestamp(),
                        "end": now.timestamp(),
                    },
                )
                series = response.json().get("data", []) if response.status_code == 200 else []
                entry["known_series"] = len(series)
                entry["metric_exists"] = bool(series)
                if series:
                    # A range query, not timestamp(last_over_time(...)): the
                    # latter reports the *evaluation* time, so a dead exporter
                    # comes back looking perfectly fresh.  The newest point in a
                    # coarse 14-day scan is the real answer.
                    last = await client.get(
                        self.config.prometheus_url + "/api/v1/query_range",
                        params={
                            "query": name,
                            "start": (now - timedelta(days=14)).timestamp(),
                            "end": now.timestamp(),
                            "step": 3600,
                        },
                    )
                    result = last.json().get("data", {}).get("result", []) if last.status_code == 200 else []
                    stamps = [
                        float(point[0])
                        for item in result
                        for point in item.get("values", [])
                    ]
                    if stamps:
                        newest = max(stamps)
                        entry["last_sample"] = datetime.fromtimestamp(newest, timezone.utc).isoformat().replace("+00:00", "Z")
                        entry["last_sample_age_hours"] = round((now.timestamp() - newest) / 3600.0, 2)
                out[direction] = entry
        except (httpx.HTTPError, ValueError, KeyError, OSError) as error:
            out["error"] = f"{type(error).__name__}: {error}"
        return out

    async def _scrape_targets(self, client: Any) -> dict[str, Any]:
        try:
            response = await client.get(self.config.prometheus_url + "/api/v1/targets",
                                        params={"state": "any"})
            active = response.json().get("data", {}).get("activeTargets", [])
        except Exception as error:  # pragma: no cover - diagnostic only
            return {"error": f"{type(error).__name__}: {error}"}
        jobs = sorted({str(item.get("labels", {}).get("job", "")) for item in active} - {""})
        # Everything except Prometheus scraping itself.
        external = [job for job in jobs if job != "prometheus"]
        return {
            "active_targets": len(active),
            "jobs": jobs,
            "scraping_anything_but_itself": bool(external),
            "unhealthy": [
                {
                    "job": item.get("labels", {}).get("job"),
                    "instance": item.get("labels", {}).get("instance"),
                    "error": item.get("lastError"),
                }
                for item in active
                if item.get("health") != "up"
            ],
        }

    async def ready(self) -> bool:
        import httpx

        try:
            client = await self._http()
            response = await client.get(self.config.prometheus_url + "/-/ready")
            return response.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    def describe(self) -> dict[str, Any]:
        return {
            "mode": "prometheus",
            "url": self.config.prometheus_url,
            "step_seconds": self.config.cadence.telemetry_step_seconds,
            "ul_query": self._rate_query("ul"),
            "dl_query": self._rate_query("dl"),
            "queries_confirmed_by_cdot": self.config.queries_confirmed,
            "last_error": self.last_error,
            "last_stats": dict(self.last_stats),
        }


def build_source(config: LiveConfig) -> ClassRateSource:
    if config.source_mode == "prometheus":
        return PrometheusSource(config)
    return ReplaySource(config)
