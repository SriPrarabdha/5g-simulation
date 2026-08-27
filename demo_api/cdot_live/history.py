"""Append-only JSONL record of what the closed loop saw and decided.

The autopilot's rotating text log is for a human watching a terminal.  This is
the machine-readable counterpart: one JSON object per line, stable field names,
so a week of unattended running can be turned into plots without re-deriving
anything from log regexes.

Three streams, deliberately separate, because they have very different rates and
very different lifetimes:

``runs.jsonl``
    One line each time the loop starts or stops.  Small, and it is what lets a
    plot mark "the process restarted here" instead of drawing a straight line
    across a gap.

``cycles.jsonl``
    One line per control cycle -- every ten minutes.  The weights the optimizer
    allocated to each UPF, what it expected them to do, and whether the SMF
    accepted them.  This is the record C-DOT asked for.

``telemetry.jsonl``
    One line per Prometheus poll -- every thirty seconds.  Carried load per UPF
    plus the health of that poll, so the load curve and the API's reliability
    can be plotted on the same time axis as the decisions.

Every write is best-effort.  A full disk or a read-only mount must degrade to a
warning, never take down a loop that is steering live traffic.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("cdot.autopilot")

ROOT = Path(__file__).resolve().parents[2]

SCHEMA = "cdot-live-history/1.0"

STREAMS = ("runs", "cycles", "telemetry")


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _round(value: Any, digits: int = 1) -> Any:
    """Round floats for readability; leave everything else exactly as it is."""
    if isinstance(value, float):
        return round(value, digits)
    return value


@dataclass(slots=True)
class HistoryPaths:
    root: Path
    runs: Path
    cycles: Path
    telemetry: Path


class HistoryWriter:
    """JSONL sink for the autopilot's decisions and observations."""

    def __init__(
        self,
        directory: str | Path,
        *,
        enabled: bool = True,
        max_bytes: int = 50_000_000,
        backups: int = 3,
        telemetry_every_n_polls: int = 1,
    ) -> None:
        root = Path(directory)
        if not root.is_absolute():
            root = ROOT / root
        self.root = root
        self.enabled = bool(enabled)
        self.max_bytes = int(max_bytes)
        self.backups = int(backups)
        self.telemetry_every_n_polls = max(1, int(telemetry_every_n_polls))
        self._counts = {name: 0 for name in STREAMS}
        self._telemetry_seen = 0
        self._error: str | None = None
        self._warned = False
        if self.enabled:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                self.enabled = False
                self._error = f"{type(error).__name__}: {error}"
                LOGGER.warning("history disabled -- cannot create %s: %s", self.root, error)

    # ------------------------------------------------------------------ paths

    def path(self, stream: str) -> Path:
        return self.root / f"{stream}.jsonl"

    def paths(self) -> HistoryPaths:
        return HistoryPaths(
            root=self.root,
            runs=self.path("runs"),
            cycles=self.path("cycles"),
            telemetry=self.path("telemetry"),
        )

    # ------------------------------------------------------------------ write

    def _rotate_if_needed(self, path: Path) -> None:
        try:
            if not path.exists() or path.stat().st_size < self.max_bytes:
                return
        except OSError:
            return
        # Plain numbered rotation.  These files are read by a plotting script,
        # not tailed, so keeping whole files beats interleaving.
        for index in range(self.backups - 1, 0, -1):
            older, newer = path.with_suffix(f".jsonl.{index + 1}"), path.with_suffix(f".jsonl.{index}")
            if newer.exists():
                try:
                    newer.replace(older)
                except OSError:
                    pass
        try:
            path.replace(path.with_suffix(".jsonl.1"))
            LOGGER.info("history: rotated %s at %.0f MB", path.name, self.max_bytes / 1e6)
        except OSError:
            pass

    def write(self, stream: str, record: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        path = self.path(stream)
        payload = {"schema": SCHEMA, "stream": stream, "ts": _iso(), **record}
        try:
            self._rotate_if_needed(path)
            # One line, one os-level append: concurrent readers never see a
            # half-written record, and a tail -f stays valid JSONL throughout.
            line = json.dumps(payload, separators=(",", ":"), default=str) + "\n"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, TypeError, ValueError) as error:
            self._error = f"{type(error).__name__}: {error}"
            if not self._warned:  # once per process, not once per poll
                LOGGER.warning("history write to %s failed: %s", path.name, error)
                self._warned = True
            return False
        self._counts[stream] = self._counts.get(stream, 0) + 1
        return True

    # ---------------------------------------------------------------- records

    def run_event(self, event: str, config: Any, settings: dict[str, Any], **extra: Any) -> None:
        """A start or a stop.  Carries the config so a plot can be self-describing."""
        self.write("runs", {
            "event": event,
            "settings": dict(settings),
            "capacity": {
                "per_upf": config.capacity.per_upf_pps,
                "safe": config.capacity.safe_pps,
                "unit": config.traffic_unit,
                "confirmed_by_cdot": config.capacity.confirmed_by_cdot,
            },
            "unit": config.traffic_unit,
            "upfs": {upf: config.smf_name(upf) for upf in config.upf_ids},
            "queries": dict(config.queries),
            **extra,
        })

    def cycle(self, record: Any, *, unit: str, observed: dict[str, float] | None = None) -> None:
        """One control cycle: the weights allocated, and what happened to them."""
        solver = dict(record.solver or {})
        proposal = dict(getattr(record, "loads", {}) or {})
        self.write("cycles", {
            "cycle": record.cycle,
            "trigger": record.trigger,
            "outcome": record.outcome,
            "reason": record.reason,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "duration_ms": record.duration_ms,
            "unit": unit,
            "window": dict(record.window or {}),
            # The headline: what each UPF was allocated, per (dnn, tac) tuple.
            "weights": {key: dict(value) for key, value in (record.weights or {}).items()},
            "changed_selection_ids": list(record.changed_selection_ids or ()),
            "changed_count": len(record.changed_selection_ids or ()),
            # Per-group forecast for one horizon ahead, and the demand actually
            # flowing when this cycle ran.  Pair cycle N's "forecast" with cycle
            # N+1's "observed_demand" to score the forecaster.
            "forecast": {
                key: {k: _round(v) for k, v in value.items()}
                for key, value in (getattr(record, "forecast", None) or {}).items()
            },
            "observed_demand": {
                key: _ul_dl_total(value)
                for key, value in (getattr(record, "observed_demand", None) or {}).items()
            },
            # Per-UPF share of the whole network, so a single stacked plot works
            # without re-deriving it from the per-tuple weights.
            "upf_share": _upf_share(record.weights or {}),
            "solver_status": solver.get("status"),
            "solver_runtime_ms": solver.get("solver_runtime_ms"),
            "hottest_baseline_upf": (solver.get("hottest_baseline") or {}).get("upf"),
            "hottest_baseline_load": _round((solver.get("hottest_baseline") or {}).get("pps")),
            "hottest_projected_upf": (solver.get("hottest_projected") or {}).get("upf"),
            "hottest_projected_load": _round((solver.get("hottest_projected") or {}).get("pps")),
            "peak_reduction": solver.get("peak_reduction"),
            "max_safe_utilization": solver.get("max_safe_utilization"),
            "forecast_model": solver.get("forecast_model"),
            "baseline_load": {k: _round(v) for k, v in (proposal.get("baseline") or {}).items()},
            "projected_load": {k: _round(v) for k, v in (proposal.get("projected") or {}).items()},
            "observed_load": {k: _round(v) for k, v in (observed or {}).items()},
            "verified": record.verified,
            "smf_state_hash": record.smf_state_hash,
            "posted": record.posted,
            "error": record.error,
        })

    def telemetry(
        self,
        poll: Any,
        *,
        unit: str,
        upf_load: dict[str, dict[str, float]],
        buffer: dict[str, Any],
        health_state: str,
    ) -> None:
        """One poll: carried load per UPF, and whether the API behaved."""
        self._telemetry_seen += 1
        if self._telemetry_seen % self.telemetry_every_n_polls:
            return
        self.write("telemetry", {
            "poll": poll.sequence,
            "unit": unit,
            "ok": poll.ok,
            "verdict": poll.verdict,
            "latency_ms": poll.latency_ms,
            "samples": poll.samples,
            "new_samples": poll.new_samples,
            "series_returned": poll.series_returned,
            "series_matched": poll.series_matched,
            "latest_sample": poll.latest_sample,
            "prometheus_state": health_state,
            "error": poll.error,
            "buffer_samples": buffer.get("samples"),
            "buffer_coverage_seconds": buffer.get("coverage_seconds"),
            # Sum the rounded parts rather than rounding the sum, so ul + dl
            # equals total exactly in the file and a stacked plot of the two
            # never disagrees with a line plot of the third.
            "upf_load": {
                upf: _ul_dl_total(value)
                for upf, value in (upf_load or {}).items()
            },
        })

    # --------------------------------------------------------------- describe

    def describe(self) -> dict[str, Any]:
        sizes: dict[str, int | None] = {}
        for name in STREAMS:
            try:
                sizes[name] = self.path(name).stat().st_size
            except OSError:
                sizes[name] = None
        return {
            "enabled": self.enabled,
            "directory": str(self.root),
            "files": {name: str(self.path(name)) for name in STREAMS},
            "records_written": dict(self._counts),
            "bytes": sizes,
            "telemetry_every_n_polls": self.telemetry_every_n_polls,
            "last_error": self._error,
        }


def _ul_dl_total(value: dict[str, float]) -> dict[str, float]:
    ul = _round(float(value.get("ul", 0.0)))
    dl = _round(float(value.get("dl", 0.0)))
    return {"ul": ul, "dl": dl, "total": round(ul + dl, 1)}


def _upf_share(weights: dict[str, dict[str, int]]) -> dict[str, float]:
    """Mean allocated share per UPF across every tuple, as a fraction.

    Each tuple's weights sum to 100 already, so this is the unweighted mean of
    the per-tuple shares -- a "how much of the network did each UPF get" line
    that plots directly, without needing the demand cube to weight it.
    """
    totals: dict[str, float] = {}
    for allocation in weights.values():
        total = sum(allocation.values()) or 1
        for upf, value in allocation.items():
            totals[upf] = totals.get(upf, 0.0) + value / total
    count = len(weights) or 1
    return {upf: round(value / count, 4) for upf, value in sorted(totals.items())}
