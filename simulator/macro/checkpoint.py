from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .sinks import CompositeSink


CHECKPOINT_SCHEMA = "simulation-checkpoint/1.0"


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class CheckpointManager:
    """Atomic, exact-resume checkpoints over simulator and sealed sink state."""

    def __init__(
        self,
        directory: str | Path,
        *,
        step_seconds: int,
        interval_simulated_seconds: int = 6 * 3600,
        fingerprints: dict[str, Any],
        retain: int = 2,
        resume: bool = True,
        stop_event: Any | None = None,
    ) -> None:
        if step_seconds <= 0 or interval_simulated_seconds <= 0 or retain < 2:
            raise ValueError("checkpoint cadence and retention are invalid")
        if interval_simulated_seconds % step_seconds:
            raise ValueError("checkpoint interval must align with simulator steps")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.interval_steps = interval_simulated_seconds // step_seconds
        self.fingerprints = json.loads(json.dumps(fingerprints, sort_keys=True))
        self.fingerprint_sha256 = canonical_sha256(self.fingerprints)
        self.retain = retain
        self.resume = resume
        self._stop_requested = False
        self.stop_event = stop_event
        self._last_checkpoint_step: int | None = None
        self.lineage: list[dict[str, Any]] = []

    def request_stop(self) -> None:
        self._stop_requested = True

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested or bool(
            self.stop_event is not None and self.stop_event.is_set()
        )

    def should_checkpoint(self, completed_steps: int) -> bool:
        return completed_steps > 0 and completed_steps % self.interval_steps == 0

    def is_checkpoint_current(self, completed_steps: int) -> bool:
        return self._last_checkpoint_step == completed_steps

    def _paths(self) -> list[Path]:
        return sorted(self.directory.glob("checkpoint-*.json.gz"))

    def save(self, simulator: Any, sinks: CompositeSink) -> Path:
        sinks.flush()
        sink_state = sinks.snapshot_state()
        payload = {
            "schema_version": CHECKPOINT_SCHEMA,
            "current_step": simulator.current_step,
            "fingerprints": self.fingerprints,
            "fingerprint_sha256": self.fingerprint_sha256,
            "simulator": simulator.snapshot_state(),
            "sinks": sink_state,
            "parent": self.lineage[-1]["sha256"] if self.lineage else None,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
        digest = hashlib.sha256(compressed).hexdigest()
        final = self.directory / f"checkpoint-{simulator.current_step:012d}-{digest[:12]}.json.gz"
        temporary = self.directory / f".{final.name}.{os.getpid()}.tmp"
        with temporary.open("wb") as stream:
            stream.write(compressed)
            stream.flush()
            os.fsync(stream.fileno())
        if final.exists():
            if hashlib.sha256(final.read_bytes()).hexdigest() != digest:
                raise ValueError(f"immutable checkpoint collision: {final}")
            temporary.unlink()
        else:
            os.replace(temporary, final)
        directory_fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self._last_checkpoint_step = simulator.current_step
        self.lineage.append({
            "step": simulator.current_step, "file": final.name,
            "sha256": digest, "bytes": final.stat().st_size,
        })
        paths = self._paths()
        for stale in paths[:-self.retain]:
            stale.unlink()
        return final

    def restore_latest(self, simulator: Any, sinks: CompositeSink) -> Path | None:
        if not self.resume:
            return None
        paths = self._paths()
        if not paths:
            return None
        path = paths[-1]
        compressed = path.read_bytes()
        try:
            payload = json.loads(gzip.decompress(compressed))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"checkpoint is corrupt: {path}") from error
        if payload.get("schema_version") != CHECKPOINT_SCHEMA:
            raise ValueError("unsupported checkpoint schema")
        if payload.get("fingerprints") != self.fingerprints:
            raise ValueError("checkpoint fingerprints do not match this work item")
        if payload.get("fingerprint_sha256") != self.fingerprint_sha256:
            raise ValueError("checkpoint fingerprint digest mismatch")
        if int(payload.get("current_step", -1)) != int(payload["simulator"]["current_step"]):
            raise ValueError("checkpoint step metadata is inconsistent")
        # Sink restore validates every immutable segment hash before the fresh
        # simulator is made observable to the run loop.
        sinks.restore_state(payload["sinks"])
        simulator.restore_state(payload["simulator"])
        digest = hashlib.sha256(compressed).hexdigest()
        self._last_checkpoint_step = simulator.current_step
        self.lineage.append({
            "step": simulator.current_step, "file": path.name,
            "sha256": digest, "bytes": path.stat().st_size, "resumed": True,
        })
        return path
