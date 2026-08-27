"""Unattended closed loop over C-DOT's live Prometheus and SMF.

This is the piece C-DOT asked for after seeing the replay console: not "load
three hours and play it back", but a background job that keeps running against
their live plane.

Two loops, two clocks, on purpose:

* **Telemetry poll** -- every ``telemetry_poll_seconds`` (30 s by default) the
  loop pulls the newest class rates out of Prometheus into a rolling in-memory
  buffer.  Fast, cheap, and the only thing that decides whether the Prometheus
  API is healthy.  Each poll is logged, so an outage shows up in the log within
  one scrape instead of one control period.
* **Control cycle** -- every ``control_interval_seconds`` (600 s by default) the
  loop fits the forecaster on that buffer, runs the joint HiGHS solve, and POSTs
  the resulting per-UPF weights to their SMF, then GETs to verify.  Slow,
  because each write re-steers live PDU-session establishment.

The poller never actuates and the controller never queries Prometheus: if their
Prometheus goes down mid-window the buffer simply stops growing, the freshness
guard trips, and the control cycle holds the last applied weights rather than
solving on a stale picture.

Health is a first-class output, not a side effect of a stack trace.  Every poll
records latency, how many series Prometheus returned, and how many of them
survived label normalisation -- because on this deployment "the API is up and
answering with zero matching series" (a wrong metric name) is a far more likely
failure than "the API is down", and the two must not look alike.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import LiveConfig
from .demand import DemandCube, build_demand_cube, group_id, parse_group_id
from .history import HistoryWriter
from .sources import ClassRate, SourceError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .service import CdotLiveService


LOGGER = logging.getLogger("cdot.autopilot")

ROOT = Path(__file__).resolve().parents[2]


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def configure_logging(config: LiveConfig) -> None:
    """Give the autopilot a console line and, optionally, its own file.

    Called from ``create_app``.  Uvicorn owns the root handlers, so this only
    adds what is missing and never re-adds a handler on reload.
    """

    # Never attach the rotating file handler under pytest.  ``create_app()``
    # calls this at import time, so a test run would otherwise append its
    # replay-mode cycles -- different units, fake SMF hashes -- straight into
    # the log of a loop that is steering live traffic, where they are
    # indistinguishable from real ones.
    if "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules:
        return
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = True
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [cdot.autopilot] %(message)s", "%Y-%m-%dT%H:%M:%S%z"
    )
    if not any(isinstance(handler, logging.StreamHandler) and
               not isinstance(handler, logging.FileHandler)
               for handler in LOGGER.handlers):
        # stdout, not the default stderr.  A supervised background run sends
        # stdout to /dev/null (the rotating file already has it all) and keeps
        # stderr for genuine crashes -- which only works if the routine health
        # stream and a traceback go to different places.
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        LOGGER.addHandler(stream)
    target = config.autopilot.log_file
    if not target:
        return
    path = Path(target)
    if not path.is_absolute():
        path = ROOT / path
    if any(isinstance(handler, logging.FileHandler) and
           getattr(handler, "baseFilename", None) == str(path)
           for handler in LOGGER.handlers):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rotating, not plain: at a 30 s poll cadence this file gains roughly a
        # third of a megabyte a day and the job is meant to run for weeks.
        handler = logging.handlers.RotatingFileHandler(
            path,
            encoding="utf-8",
            maxBytes=config.autopilot.log_max_bytes,
            backupCount=config.autopilot.log_backups,
        )
        handler.setFormatter(fmt)
        LOGGER.addHandler(handler)
        LOGGER.info(
            "autopilot log file: %s (rotating at %.1f MB, keeping %d)",
            path,
            config.autopilot.log_max_bytes / 1e6,
            config.autopilot.log_backups,
        )
    except OSError as error:  # a read-only checkout must not break startup
        LOGGER.warning("could not open autopilot log file %s: %s", path, error)


# --------------------------------------------------------------------- buffer


class TelemetryBuffer:
    """Rolling window of class rates, fed incrementally by the poller.

    Keyed by ``(t, upf, tac, dnn, dscp)`` so an overlapping re-fetch -- which is
    deliberate, to catch scrapes that landed late -- overwrites rather than
    duplicates.  Anything older than ``history_seconds`` behind the newest
    sample is dropped, so memory is bounded by the window, not by uptime.
    """

    def __init__(self, history_seconds: int) -> None:
        self.history_seconds = int(history_seconds)
        self._rows: dict[tuple[datetime, str, int, str, int], ClassRate] = {}

    def __len__(self) -> int:
        return len(self._rows)

    def ingest(self, rows: list[ClassRate]) -> int:
        fresh = 0
        for row in rows:
            key = (row.t, row.upf, row.tac, row.dnn, row.dscp)
            if key not in self._rows:
                fresh += 1
            self._rows[key] = row
        self._prune()
        return fresh

    def _prune(self) -> None:
        if not self._rows:
            return
        newest = max(key[0] for key in self._rows)
        cutoff = newest - timedelta(seconds=self.history_seconds)
        if cutoff <= min(key[0] for key in self._rows):
            return
        self._rows = {key: row for key, row in self._rows.items() if key[0] >= cutoff}

    def clear(self) -> None:
        self._rows.clear()

    def span(self) -> tuple[datetime, datetime] | None:
        if not self._rows:
            return None
        times = [key[0] for key in self._rows]
        return min(times), max(times)

    @property
    def latest(self) -> datetime | None:
        span = self.span()
        return span[1] if span else None

    @property
    def coverage_seconds(self) -> float:
        span = self.span()
        return (span[1] - span[0]).total_seconds() if span else 0.0

    def cube(self, config: LiveConfig) -> DemandCube:
        span = self.span()
        if span is None:
            raise SourceError("telemetry buffer is empty -- no Prometheus samples yet")
        rows = sorted(self._rows.values(), key=lambda row: row.t)
        return build_demand_cube(
            rows,
            upfs=config.upf_ids,
            step_seconds=config.cadence.telemetry_step_seconds,
            start=span[0],
            end=span[1],
        )

    def describe(self) -> dict[str, Any]:
        span = self.span()
        return {
            "samples": len(self._rows),
            "coverage_seconds": round(self.coverage_seconds, 1),
            "history_seconds": self.history_seconds,
            "oldest": _iso(span[0]) if span else None,
            "newest": _iso(span[1]) if span else None,
        }


# --------------------------------------------------------------------- records


@dataclass(slots=True)
class PollRecord:
    """One Prometheus touch.  This is the health evidence, per scrape."""

    sequence: int
    at: str
    ok: bool
    latency_ms: int
    samples: int
    new_samples: int
    latest_sample: str | None
    endpoint_reachable: bool | None = None
    series_returned: int | None = None
    series_matched: int | None = None
    verdict: str = "ok"
    error: str | None = None
    diagnosis: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "at": self.at,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "samples": self.samples,
            "new_samples": self.new_samples,
            "latest_sample": self.latest_sample,
            "endpoint_reachable": self.endpoint_reachable,
            "series_returned": self.series_returned,
            "series_matched": self.series_matched,
            "verdict": self.verdict,
            "error": self.error,
            "diagnosis": self.diagnosis,
        }


@dataclass(slots=True)
class CycleRecord:
    """One control cycle: forecast, solve, and what happened at the SMF."""

    cycle: int
    started_at: str
    trigger: str
    outcome: str = "running"
    reason: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    window: dict[str, Any] = field(default_factory=dict)
    solver: dict[str, Any] = field(default_factory=dict)
    weights: dict[str, dict[str, int]] = field(default_factory=dict)
    # Per-UPF totals under the current routing vs. the proposed one, so the
    # history can plot what the solve was actually trying to fix.
    loads: dict[str, dict[str, float]] = field(default_factory=dict)
    changed_selection_ids: list[str] = field(default_factory=list)
    # Per-group demand: what the forecaster predicted for one horizon ahead, and
    # what was actually flowing when the cycle ran.  The control interval and the
    # forecast horizon are both 600 s, so cycle N's forecast targets cycle N+1's
    # observation -- that pairing is the forecaster's accuracy record.
    forecast: dict[str, dict[str, float]] = field(default_factory=dict)
    observed_demand: dict[str, dict[str, float]] = field(default_factory=dict)
    posted: list[dict[str, Any]] = field(default_factory=list)
    smf_state_hash: str | None = None
    verified: bool | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "trigger": self.trigger,
            "outcome": self.outcome,
            "reason": self.reason,
            "window": dict(self.window),
            "solver": dict(self.solver),
            "weights": {key: dict(value) for key, value in self.weights.items()},
            "loads": {key: dict(value) for key, value in self.loads.items()},
            "changed_selection_ids": list(self.changed_selection_ids),
            "forecast": {k: dict(v) for k, v in self.forecast.items()},
            "observed_demand": {k: dict(v) for k, v in self.observed_demand.items()},
            "posted": list(self.posted),
            "smf_state_hash": self.smf_state_hash,
            "verified": self.verified,
            "error": self.error,
        }


def _totals(table: dict[str, dict[str, float]]) -> dict[str, float]:
    """Flatten a per-UPF {ul, dl, total} table down to one number per UPF."""
    return {
        upf: float(value.get("total", value.get("ul", 0.0) + value.get("dl", 0.0)))
        for upf, value in table.items()
        if isinstance(value, dict)
    }


# ------------------------------------------------------------------ autopilot


class Autopilot:
    """The background job: poll Prometheus, solve every N minutes, write to SMF."""

    def __init__(self, service: "CdotLiveService") -> None:
        self.service = service
        self.config = service.config
        self.settings = service.config.autopilot
        self.buffer = TelemetryBuffer(service.config.cadence.history_seconds)
        self.history = HistoryWriter(
            self.settings.history_dir,
            enabled=self.settings.history_enabled,
            max_bytes=self.settings.history_max_bytes,
            backups=self.settings.history_backups,
            telemetry_every_n_polls=self.settings.history_telemetry_every_n_polls,
        )

        self._poll_task: asyncio.Task[None] | None = None
        self._control_task: asyncio.Task[None] | None = None
        self._cycle_lock = asyncio.Lock()
        self._running = False
        self._started_at: str | None = None
        self._stop_reason: str | None = None

        self._polls: deque[PollRecord] = deque(maxlen=self.settings.poll_log_limit)
        self._cycles: deque[CycleRecord] = deque(maxlen=self.settings.cycle_log_limit)
        self._poll_sequence = 0
        self._cycle_sequence = 0
        self._polls_ok = 0
        self._consecutive_failures = 0
        self._last_success_at: str | None = None
        self._last_failure_at: str | None = None
        self._last_error: str | None = None
        self._latencies: deque[int] = deque(maxlen=60)
        self._primed = False
        self._next_control_at: datetime | None = None
        self._smf_diagnosis: dict[str, Any] | None = None
        self._last_applied_weights: dict[str, dict[str, int]] = {}
        self._last_applied_at: str | None = None

    # ----------------------------------------------------------- lifecycle

    @property
    def running(self) -> bool:
        return self._running

    async def start(self, *, actor: str = "system") -> dict[str, Any]:
        if self._running:
            return self.status()
        self._running = True
        self._stop_reason = None
        self._started_at = _iso(_now())
        self._next_control_at = _now() + timedelta(
            seconds=self.settings.control_interval_seconds
        )
        LOGGER.info(
            "STARTING autopilot | source=%s prometheus=%s smf=%s | poll every %ss, "
            "optimise + actuate every %ss (%.1f min)%s",
            self.config.source_mode,
            self.config.prometheus_url,
            self.config.smf_url,
            self.settings.telemetry_poll_seconds,
            self.settings.control_interval_seconds,
            self.settings.control_interval_seconds / 60.0,
            "  [DRY RUN -- no SMF writes]" if self.settings.dry_run else "",
        )
        if self.history.enabled:
            LOGGER.info(
                "history: %s (cycles + telemetry as JSONL)", self.history.root
            )
        self.history.run_event("started", self.config, self.settings_payload(), actor=actor)
        self._poll_task = asyncio.create_task(self._poll_loop(), name="cdot-autopilot-poll")
        self._control_task = asyncio.create_task(
            self._control_loop(), name="cdot-autopilot-control"
        )
        self.service._audit(actor, "cdot-live.autopilot_started", self.settings_payload())
        await self.service._emit("autopilot.state", self.status())
        return self.status()

    async def stop(self, *, actor: str = "system", reason: str = "operator stop") -> dict[str, Any]:
        if not self._running:
            return self.status()
        self._running = False
        self._stop_reason = reason
        await self._await_quiet_cycle()
        for task in (self._poll_task, self._control_task):
            if task is not None:
                task.cancel()
        for task in (self._poll_task, self._control_task):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._poll_task = self._control_task = None
        self._next_control_at = None
        self.history.run_event(
            "stopped", self.config, self.settings_payload(), actor=actor, reason=reason,
            cycles_run=self._cycle_sequence, polls_total=self._poll_sequence,
            polls_ok=self._polls_ok,
        )
        LOGGER.info("STOPPED autopilot | reason=%s", reason)
        self.service._audit(actor, "cdot-live.autopilot_stopped", {"reason": reason})
        await self.service._emit("autopilot.state", self.status())
        return self.status()

    async def _await_quiet_cycle(self) -> None:
        """Wait out an in-flight control cycle before tearing the loop down.

        Cancelling mid-cycle can land between the POST and the GET that verifies
        it, which leaves their SMF holding weights this process never recorded
        and cannot roll back.  Waiting for the cycle lock costs a few seconds at
        shutdown and removes that window entirely.
        """
        if self._cycle_lock.locked():
            LOGGER.info("waiting for the in-flight control cycle before stopping")
        grace = min(30.0, max(5.0, self.config.timeout_seconds * 3))
        try:
            await asyncio.wait_for(self._cycle_lock.acquire(), timeout=grace)
        except asyncio.TimeoutError:
            LOGGER.error(
                "control cycle did not finish within %.0fs -- cancelling it; "
                "check %s/upf-admin for a write this process could not verify",
                grace,
                self.config.smf_url,
            )
            return
        self._cycle_lock.release()

    def settings_payload(self) -> dict[str, Any]:
        return {
            "telemetry_poll_seconds": self.settings.telemetry_poll_seconds,
            "control_interval_seconds": self.settings.control_interval_seconds,
            "require_fresh_seconds": self.settings.require_fresh_seconds,
            "min_history_seconds": self.settings.min_history_seconds,
            "dry_run": self.settings.dry_run,
            "prometheus_url": self.config.prometheus_url,
            "smf_url": self.config.smf_url,
            "source_mode": self.config.source_mode,
        }

    # -------------------------------------------------------- telemetry poll

    async def _poll_loop(self) -> None:
        try:
            await self.poll_once(prime=True)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # priming must never kill the loop
            LOGGER.error("initial Prometheus prime failed: %s", error)
        while True:
            await asyncio.sleep(self.settings.telemetry_poll_seconds)
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # pragma: no cover - defensive
                LOGGER.exception("telemetry poll raised unexpectedly: %s", error)

    def _snap(self, moment: datetime) -> datetime:
        """Floor a timestamp onto the telemetry step grid.

        ``query_range`` returns points at ``start + k*step``, so a start that
        drifts by a few seconds each poll returns the *same* samples under
        slightly different timestamps.  The buffer keys on the timestamp, so
        unsnapped polls would stack four near-duplicate copies of every
        overlapped sample and make "new samples" useless as a health signal.
        """
        step = max(1, self.config.cadence.telemetry_step_seconds)
        return datetime.fromtimestamp(
            (int(moment.timestamp()) // step) * step, timezone.utc
        )

    def _poll_window(self, prime: bool) -> tuple[datetime, datetime]:
        """How far back to ask for.

        Priming pulls the whole history window so the first control cycle has a
        real cycle to fit.  Steady state pulls only since the newest sample we
        hold, plus an overlap, because Prometheus back-fills late scrapes.
        """
        end = self._snap(self.service._now_trace())
        floor = end - timedelta(seconds=self.config.cadence.history_seconds)
        if prime or self.buffer.latest is None:
            return floor, end
        start = self._snap(
            self.buffer.latest - timedelta(seconds=self.settings.poll_overlap_seconds)
        )
        return max(start, floor), end

    async def poll_once(self, *, prime: bool = False) -> PollRecord:
        start, end = self._poll_window(prime)
        self._poll_sequence += 1
        began = time.perf_counter()
        rows: list[ClassRate] = []
        error: str | None = None
        try:
            rows = await self.service.source.window(start, end)
        except asyncio.CancelledError:
            raise
        except Exception as failure:
            error = f"{type(failure).__name__}: {failure}"
        latency_ms = int((time.perf_counter() - began) * 1000)

        stats = dict(getattr(self.service.source, "last_stats", {}) or {})
        returned = stats.get("series_returned")
        matched = stats.get("series_matched")
        series_returned = sum(returned.values()) if isinstance(returned, dict) else None
        series_matched = sum(matched.values()) if isinstance(matched, dict) else None
        soft_error = stats.get("error") if error is None else None

        new_samples = self.buffer.ingest(rows) if rows else 0
        record = PollRecord(
            sequence=self._poll_sequence,
            at=_iso(_now()),
            ok=error is None and (bool(rows) or not prime),
            latency_ms=latency_ms,
            samples=len(rows),
            new_samples=new_samples,
            latest_sample=_iso(self.buffer.latest) if self.buffer.latest else None,
            series_returned=series_returned,
            series_matched=series_matched,
        )

        if error is not None:
            record.ok = False
            record.error = error
            record.verdict = "unreachable"
            record.endpoint_reachable = await self._probe_endpoint()
            if record.endpoint_reachable:
                record.verdict = "query_failed"
        elif series_returned == 0 or (not rows and series_matched == 0):
            # The API answered; it just had nothing in the window.  Two very
            # different causes look identical here -- a wrong metric name and a
            # dead exporter -- and C-DOT needs opposite fixes for them, so ask.
            record.ok = False
            record.verdict = "no_series"
            record.endpoint_reachable = True
            record.diagnosis = await self._diagnose()
            record.error = self._explain_no_series(record.diagnosis) or soft_error or (
                f"Prometheus returned no series for {self.config.queries.get('ul')!r} / "
                f"{self.config.queries.get('dl')!r}"
            )
        elif series_returned and series_matched == 0:
            record.ok = False
            record.verdict = "labels_unmatched"
            record.error = soft_error or (
                f"{series_returned} series returned but none carried usable upf/loc/dnn/dscp labels"
            )
            record.endpoint_reachable = True
        elif not rows:
            record.verdict = "empty_window"
            record.error = soft_error

        self._record_poll(record, prime=prime)
        if record.ok and new_samples:
            # Refresh the cube the console draws from at *poll* cadence rather
            # than control cadence, so the operator's chart is 30 s behind the
            # network instead of up to ten minutes behind it.
            try:
                self.service._cube = self.buffer.cube(self.config)
                self.service._last_poll = record.at
            except (SourceError, ValueError) as error:
                LOGGER.warning("could not rebuild the demand cube from the buffer: %s", error)
        self.history.telemetry(
            record,
            unit=self.config.traffic_unit,
            upf_load=self._latest_upf_load(),
            buffer=self.buffer.describe(),
            health_state=self.health()["state"],
        )
        await self.service._emit("autopilot.poll", record.as_dict())
        return record

    def _latest_upf_load(self) -> dict[str, dict[str, float]]:
        """Newest carried load per UPF, from the cube the poller just rebuilt."""
        cube = self.service._cube
        if cube is None:
            return {}
        try:
            return cube.latest_upf_load()
        except (IndexError, ValueError):  # a cube with no usable rows yet
            return {}

    async def _diagnose_smf(self) -> dict[str, Any] | None:
        probe = getattr(self.service.smf, "diagnose", None)
        if probe is None:
            return None
        try:
            return await probe()
        except Exception as error:
            return {"verdict": "error", "detail": f"{type(error).__name__}: {error}"}

    async def _diagnose(self) -> dict[str, Any] | None:
        probe = getattr(self.service.source, "diagnose", None)
        if probe is None:
            return None
        try:
            return await probe()
        except Exception as error:  # diagnosis must never break the poll
            return {"error": f"{type(error).__name__}: {error}"}

    @staticmethod
    def _explain_no_series(diagnosis: dict[str, Any] | None) -> str | None:
        """One sentence naming the actual cause, for the log and the console."""
        if not diagnosis:
            return None
        scrape = diagnosis.get("scrape")
        if isinstance(scrape, dict) and scrape.get("active_targets") is not None:
            if not scrape.get("scraping_anything_but_itself"):
                return (
                    "Prometheus is scraping nothing but itself -- its config lists only "
                    f"{scrape.get('jobs')}, so the UPF exporters are not registered as "
                    "targets at all. This is a Prometheus configuration problem, not an "
                    "exporter outage"
                )
            if scrape.get("unhealthy"):
                return (
                    "Prometheus has the targets but cannot scrape them: "
                    + "; ".join(
                        f"{item['job']}@{item['instance']}: {item['error']}"
                        for item in scrape["unhealthy"][:3]
                    )
                )
        missing = [
            entry["metric"] for key, entry in diagnosis.items()
            if isinstance(entry, dict) and entry.get("metric") and not entry.get("metric_exists")
        ]
        if missing:
            return (
                "Prometheus has never heard of "
                + ", ".join(repr(name) for name in missing)
                + " -- the configured metric name is wrong"
            )
        ages = [
            entry["last_sample_age_hours"] for entry in diagnosis.values()
            if isinstance(entry, dict) and entry.get("last_sample_age_hours") is not None
        ]
        # The coarse 14-day scan resolves to about an hour, so say "at least".
        if ages:
            return (
                f"the metrics exist but have had no data for at least {min(ages):.0f} h "
                "-- Prometheus is up and the UPF telemetry has stopped reaching it"
            )
        return None

    async def _probe_endpoint(self) -> bool | None:
        probe = getattr(self.service.source, "ready", None)
        if probe is None:
            return None
        try:
            return bool(await probe())
        except Exception:
            return False

    def _record_poll(self, record: PollRecord, *, prime: bool) -> None:
        self._polls.append(record)
        self._latencies.append(record.latency_ms)
        label = "PRIME" if prime else "poll"
        if record.ok:
            recovered = self._consecutive_failures
            self._polls_ok += 1
            self._consecutive_failures = 0
            self._last_success_at = record.at
            self._last_error = None
            if not self._primed:
                self._primed = True
            if recovered:
                LOGGER.warning(
                    "Prometheus RECOVERED after %d consecutive failures", recovered
                )
            LOGGER.info(
                "%s ok  %4d ms | %d samples (%d new) | series %s/%s matched | latest %s | "
                "buffer %d samples over %.1f min",
                label,
                record.latency_ms,
                record.samples,
                record.new_samples,
                record.series_matched if record.series_matched is not None else "-",
                record.series_returned if record.series_returned is not None else "-",
                record.latest_sample or "none",
                len(self.buffer),
                self.buffer.coverage_seconds / 60.0,
            )
            self.service._last_error = None
        else:
            self._consecutive_failures += 1
            self._last_failure_at = record.at
            self._last_error = record.error
            self.service._last_error = record.error
            level = (
                LOGGER.error
                if self._consecutive_failures >= self.settings.unhealthy_after_failures
                else LOGGER.warning
            )
            level(
                "%s FAILED (%s, failure %d in a row, %d ms): %s",
                label,
                record.verdict,
                self._consecutive_failures,
                record.latency_ms,
                record.error,
            )
            if record.verdict == "unreachable":
                LOGGER.error("  -> %s is not answering", self.config.prometheus_url)
            elif record.verdict in {"no_series", "labels_unmatched"}:
                stats = dict(getattr(self.service.source, "last_stats", {}) or {})
                rejected = stats.get("rejected_labels") or []
                if rejected:
                    LOGGER.error("  -> sample of rejected label sets: %s", rejected[:2])
                explained = False
                scrape = (record.diagnosis or {}).get("scrape")
                if isinstance(scrape, dict) and scrape.get("jobs") is not None:
                    LOGGER.error(
                        "  -> Prometheus scrape targets: %d active, jobs=%s",
                        scrape.get("active_targets", 0), scrape.get("jobs"),
                    )
                for key, entry in (record.diagnosis or {}).items():
                    if isinstance(entry, dict) and "metric" in entry:
                        explained = True
                        LOGGER.error(
                            "  -> %s %r: known to Prometheus=%s, %s",
                            key,
                            entry["metric"],
                            entry.get("metric_exists"),
                            f"last sample {entry['last_sample_age_hours']:.1f} h ago"
                            if entry.get("last_sample_age_hours") is not None
                            else "no sample ever recorded",
                        )
                if not explained:
                    LOGGER.error(
                        "  -> confirm the metric names with C-DOT; currently ul=%r dl=%r",
                        self.config.queries.get("ul"),
                        self.config.queries.get("dl"),
                    )

    # -------------------------------------------------------- control cycle

    def _ensure_poll_task(self) -> None:
        """Revive the poller if it ever died.

        Nothing else is watching it, and a dead poller is silent: the buffer
        just stops growing and every control cycle holds on stale data.  For a
        job expected to run unattended for weeks, restarting it beats that.
        """
        if not self._running:
            return
        task = self._poll_task
        if task is None or not task.done():
            return
        if not task.cancelled():
            LOGGER.error(
                "telemetry poll loop exited unexpectedly (%r) -- restarting it",
                task.exception(),
            )
        self._poll_task = asyncio.create_task(self._poll_loop(), name="cdot-autopilot-poll")

    async def _wait_for_next_cycle(self, interval: int) -> None:
        """Sleep to the next control cycle, checking on the poller as we go."""
        deadline = _now() + timedelta(seconds=interval)
        self._next_control_at = deadline
        tick = max(5, min(interval, self.settings.telemetry_poll_seconds))
        while True:
            remaining = (deadline - _now()).total_seconds()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, tick))
            self._ensure_poll_task()

    async def _control_loop(self) -> None:
        interval = self.settings.control_interval_seconds
        while True:
            await self._wait_for_next_cycle(interval)
            try:
                await self.run_cycle(trigger="schedule")
            except asyncio.CancelledError:
                raise
            except Exception as error:  # pragma: no cover - defensive
                LOGGER.exception("control cycle raised unexpectedly: %s", error)

    def _hold_reason(self) -> str | None:
        """Every reason the loop refuses to actuate, checked before it solves."""
        latest = self.buffer.latest
        if latest is None:
            return "no Prometheus samples in the buffer yet"
        if self._consecutive_failures >= self.settings.unhealthy_after_failures:
            return (
                f"Prometheus unhealthy -- {self._consecutive_failures} consecutive failed polls "
                f"({self._last_error})"
            )
        age = (self.service._now_trace() - latest).total_seconds()
        if age > self.settings.require_fresh_seconds:
            return (
                f"newest sample is {age:.0f}s old, older than the "
                f"{self.settings.require_fresh_seconds}s freshness limit"
            )
        coverage = self.buffer.coverage_seconds
        if coverage < self.settings.min_history_seconds:
            return (
                f"buffer covers {coverage / 60.0:.1f} min, less than the "
                f"{self.settings.min_history_seconds / 60.0:.1f} min the forecaster needs"
            )
        if self.service.proposal_frozen:
            return "a presenter has the review drawer open; not overwriting their proposal"
        return None

    async def run_cycle(self, *, trigger: str = "manual", actor: str = "autopilot") -> CycleRecord:
        """One full ingest -> forecast -> solve -> POST -> GET-verify pass."""
        async with self._cycle_lock:
            self._cycle_sequence += 1
            began = time.perf_counter()
            record = CycleRecord(
                cycle=self._cycle_sequence, started_at=_iso(_now()), trigger=trigger
            )
            LOGGER.info(
                "---- control cycle #%d (%s) ----", record.cycle, trigger
            )
            try:
                await self._run_cycle_body(record, actor=actor)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                record.outcome = "failed"
                record.error = f"{type(error).__name__}: {error}"
                LOGGER.exception("cycle #%d FAILED: %s", record.cycle, error)
            record.finished_at = _iso(_now())
            record.duration_ms = int((time.perf_counter() - began) * 1000)
            self._cycles.append(record)
            self.history.cycle(
                record,
                unit=self.config.traffic_unit,
                observed={
                    upf: value.get("ul", 0.0) + value.get("dl", 0.0)
                    for upf, value in self._latest_upf_load().items()
                },
            )
            self.service._audit(actor, "cdot-live.autopilot_cycle", record.as_dict())
            await self.service._emit("autopilot.cycle", record.as_dict())
            LOGGER.info(
                "---- cycle #%d %s in %d ms ----",
                record.cycle,
                record.outcome.upper(),
                record.duration_ms,
            )
            return record

    async def _run_cycle_body(self, record: CycleRecord, *, actor: str) -> None:
        hold = self._hold_reason()
        if hold is not None:
            record.outcome = "held"
            record.reason = hold
            LOGGER.warning("cycle #%d HELD: %s", record.cycle, hold)
            return

        span = self.buffer.span()
        assert span is not None  # guaranteed by _hold_reason
        record.window = {
            "from": _iso(span[0]),
            "to": _iso(span[1]),
            "coverage_minutes": round(self.buffer.coverage_seconds / 60.0, 1),
            "samples": len(self.buffer),
        }
        LOGGER.info(
            "cycle #%d window %s .. %s (%.1f min, %d class samples)",
            record.cycle,
            record.window["from"],
            record.window["to"],
            record.window["coverage_minutes"],
            record.window["samples"],
        )

        # Read the SMF before solving: the proposal is bound to this exact state
        # hash, and apply() refuses to write if anything moved underneath it.
        await self.service.refresh_status()
        smf_blocked: str | None = None
        if not self.service._smf_ready:
            self._smf_diagnosis = await self._diagnose_smf()
            detail = (self._smf_diagnosis or {}).get("detail")
            verdict = (self._smf_diagnosis or {}).get("verdict", "unreachable")
            smf_blocked = (
                f"SMF {verdict} at {self.config.smf_url}"
                + (f" -- {detail}" if detail else "")
            )
            # Solve anyway, and hold only the write.
            #
            # Returning here used to throw away the forecast and the solve as
            # well as the POST, so an SMF outage left no optimizer record at
            # all -- and their /upf-admin is mute for long stretches, which
            # would have starved the history of exactly the forecaster and
            # solver evidence this loop exists to produce.  Nothing below
            # writes to the SMF while ``smf_blocked`` is set, so this costs no
            # actuation safety.
            LOGGER.warning(
                "cycle #%d: %s -- solving anyway so the forecast and the "
                "proposed weights are still recorded; the write is held",
                record.cycle,
                smf_blocked,
            )

        cube = self.buffer.cube(self.config)
        snapshot = await self.service.evaluate(actor=actor, audit=False, cube=cube)
        proposal = snapshot.get("proposal")
        if not proposal:
            record.outcome = "failed"
            record.error = "evaluate produced no proposal"
            LOGGER.error("cycle #%d: %s", record.cycle, record.error)
            return

        record.forecast = {
            str(row["selection_id"]): {
                "ul_p50": float((row.get("ul") or {}).get("p50", 0.0)),
                "dl_p50": float((row.get("dl") or {}).get("p50", 0.0)),
                "total_p50": float(row.get("total_p50", 0.0)),
            }
            for row in ((snapshot.get("forecast") or {}).get("rows") or [])
        }
        record.observed_demand = {
            group_id(*group): {
                "ul": float(cube.group_series(group, "ul")[-1]),
                "dl": float(cube.group_series(group, "dl")[-1]),
            }
            for group in cube.groups
            if len(cube) > 0
        }
        missing = sorted(set(record.observed_demand) - set(record.forecast))
        if missing:
            LOGGER.warning(
                "cycle #%d: %d group(s) carried traffic but are absent from the "
                "forecast, so they were not steered: %s",
                record.cycle,
                len(missing),
                ", ".join(missing),
            )

        summary = proposal.get("summary", {})
        record.solver = {
            "status": proposal.get("status"),
            "solver_runtime_ms": summary.get("solver_runtime_ms"),
            "hottest_baseline": summary.get("hottest_baseline"),
            "hottest_projected": summary.get("hottest_projected"),
            "peak_reduction": summary.get("peak_reduction"),
            "max_safe_utilization": summary.get("max_safe_utilization"),
            "forecast_model": (snapshot.get("forecast") or {}).get("model", {}).get("model"),
        }
        record.weights = {
            row["selection_id"]: dict(row["proposed_weights"]) for row in proposal["rows"]
        }
        record.changed_selection_ids = [
            row["selection_id"] for row in proposal["rows"] if row["changed"]
        ]
        record.loads = {
            "baseline": _totals(proposal.get("baseline_load_pps") or {}),
            "projected": _totals(proposal.get("projected_load_pps") or {}),
        }
        LOGGER.info(
            "cycle #%d solved (%s) in %s ms | hottest %s %s %s -> %s %s %s%s",
            record.cycle,
            proposal.get("status"),
            summary.get("solver_runtime_ms"),
            (summary.get("hottest_baseline") or {}).get("upf"),
            (summary.get("hottest_baseline") or {}).get("pps"),
            self.config.traffic_unit,
            (summary.get("hottest_projected") or {}).get("upf"),
            (summary.get("hottest_projected") or {}).get("pps"),
            self.config.traffic_unit,
            " [still over capacity]" if summary.get("projected_overloaded") else "",
        )
        for row in proposal["rows"]:
            dnn, tac = parse_group_id(row["selection_id"])
            LOGGER.info(
                "    %-9s tac %-3s  %s%s",
                dnn,
                tac,
                " ".join(f"{k}={v}" for k, v in sorted(row["proposed_weights"].items())) or "-",
                "" if row["changed"] else "   (unchanged)",
            )

        if smf_blocked is not None:
            record.outcome = "held"
            record.reason = smf_blocked
            LOGGER.error(
                "cycle #%d HELD (solved, not written): %s", record.cycle, smf_blocked
            )
            return

        if not record.changed_selection_ids:
            record.outcome = "no_change"
            record.reason = "solver reproduced the weights already in the SMF"
            record.smf_state_hash = self.service._smf_hash
            LOGGER.info("cycle #%d: no weight change to write", record.cycle)
            return

        if self.settings.dry_run:
            record.outcome = "dry_run"
            record.reason = "CDOT_LIVE_AUTOPILOT_DRY_RUN is set -- nothing written to the SMF"
            record.posted = [
                row["outgoing_json"] for row in proposal["rows"] if row["changed"]
            ]
            LOGGER.warning(
                "cycle #%d DRY RUN: would POST %d tuple(s) to %s/upf-admin",
                record.cycle,
                len(record.posted),
                self.config.smf_url,
            )
            return

        LOGGER.info(
            "cycle #%d POST %d changed tuple(s) -> %s/upf-admin",
            record.cycle,
            len(record.changed_selection_ids),
            self.config.smf_url,
        )
        applied = await self.service.apply(
            proposal["proposal_id"],
            proposal["base_smf_state_hash"] or "",
            True,
            actor=actor,
        )
        verification = (applied.get("smf") or {}).get("verification") or {}
        record.outcome = "applied"
        record.verified = bool(verification.get("verified"))
        record.smf_state_hash = (applied.get("smf") or {}).get("state_hash")
        record.posted = [row["outgoing_json"] for row in proposal["rows"] if row["changed"]]
        self._last_applied_weights = {
            selection_id: dict(record.weights[selection_id])
            for selection_id in record.changed_selection_ids
        }
        self._last_applied_at = record.finished_at or _iso(_now())
        LOGGER.info(
            "cycle #%d APPLIED and GET-verified | %d tuple(s) | new SMF state hash %s",
            record.cycle,
            len(record.changed_selection_ids),
            (record.smf_state_hash or "")[:12],
        )

    # -------------------------------------------------------------- status

    def health(self) -> dict[str, Any]:
        total = self._poll_sequence
        failures = total - self._polls_ok
        unhealthy = self._consecutive_failures >= self.settings.unhealthy_after_failures
        latest = self.buffer.latest
        age = (self.service._now_trace() - latest).total_seconds() if latest else None
        if total == 0:
            state = "unknown"
        elif unhealthy:
            state = "down"
        elif self._consecutive_failures:
            state = "degraded"
        else:
            state = "up"
        last = self._polls[-1] if self._polls else None
        return {
            "state": state,
            "healthy": state == "up",
            "url": self.config.prometheus_url,
            "in_use": self.config.source_mode == "prometheus",
            "polls_total": total,
            "polls_ok": self._polls_ok,
            "polls_failed": failures,
            "success_rate": round(self._polls_ok / total, 4) if total else None,
            "consecutive_failures": self._consecutive_failures,
            "unhealthy_after_failures": self.settings.unhealthy_after_failures,
            "last_success_at": self._last_success_at,
            "last_failure_at": self._last_failure_at,
            "last_error": self._last_error,
            "last_verdict": last.verdict if last else None,
            "mean_latency_ms": (
                round(sum(self._latencies) / len(self._latencies)) if self._latencies else None
            ),
            "last_latency_ms": last.latency_ms if last else None,
            "latest_sample": _iso(latest) if latest else None,
            "latest_sample_age_seconds": round(age, 1) if age is not None else None,
            "fresh": age is not None and age <= self.settings.require_fresh_seconds,
        }

    def status(self) -> dict[str, Any]:
        next_control = self._next_control_at
        seconds_to_next = (
            max(0.0, (next_control - _now()).total_seconds()) if next_control and self._running else None
        )
        last_cycle = self._cycles[-1] if self._cycles else None
        return {
            "schema_version": "cdot-live-autopilot/1.0",
            "enabled": self.settings.enabled,
            "running": self._running,
            "dry_run": self.settings.dry_run,
            "started_at": self._started_at,
            "stop_reason": self._stop_reason,
            "hold_reason": self._hold_reason() if self._running else None,
            "settings": self.settings_payload(),
            "prometheus": self.health(),
            "smf": {
                "url": self.config.smf_url,
                "ready": self.service._smf_ready,
                "state_hash": self.service._smf_hash,
                "protocol": "h2c-prior-knowledge",
                "diagnosis": self._smf_diagnosis,
            },
            "buffer": self.buffer.describe(),
            "history": self.history.describe(),
            "control": {
                "interval_seconds": self.settings.control_interval_seconds,
                "cycles_run": self._cycle_sequence,
                "next_run_at": _iso(next_control) if next_control and self._running else None,
                "seconds_to_next_run": round(seconds_to_next, 1) if seconds_to_next is not None else None,
                "last_outcome": last_cycle.outcome if last_cycle else None,
                "last_cycle_at": last_cycle.started_at if last_cycle else None,
                "last_applied_at": self._last_applied_at,
                "last_applied_weights": {
                    key: dict(value) for key, value in self._last_applied_weights.items()
                },
            },
            "polls": [record.as_dict() for record in list(self._polls)[-40:]],
            "cycles": [record.as_dict() for record in list(self._cycles)[-20:]],
        }
