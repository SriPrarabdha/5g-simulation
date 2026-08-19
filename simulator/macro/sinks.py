from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from schemas import SelectionAudit
from schemas import Policy
from schemas.common import iso_utc, parse_utc

from .model import StepResult


class StepSink(Protocol):
    def accept_step(self, step: StepResult) -> None: ...
    def flush(self) -> None: ...
    def snapshot_state(self) -> dict[str, Any]: ...
    def restore_state(self, state: dict[str, Any]) -> None: ...
    def close(self, *, success: bool) -> None: ...


class AuditConsumer(Protocol):
    def accept_audit(self, audit: SelectionAudit) -> None: ...


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    kind: str
    path: str
    sha256: str
    bytes: int
    rows: int | None = None
    row_groups: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "rows": self.rows,
            "row_groups": self.row_groups,
        }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def step_schema(*, controller: str, summary: dict[str, Any] | None = None):
    try:
        import pyarrow as pa
    except ImportError as error:
        raise RuntimeError("PyArrow is required for canonical Parquet output") from error
    direction = pa.struct([
        ("offered_bytes", pa.float64()),
        ("new_session_offered_bytes", pa.float64()),
        ("carried_bytes", pa.float64()),
        ("queued_bytes", pa.float64()),
        ("dropped_bytes", pa.float64()),
        ("rejected_bytes", pa.float64()),
        ("capacity_mbps", pa.float64()),
        ("safe_capacity_mbps", pa.float64()),
    ])
    upf = pa.struct([
        ("upf_id", pa.string()), ("health", pa.string()),
        ("active_sessions", pa.int64()), ("new_sessions", pa.int64()),
        ("departed_sessions", pa.int64()), ("establishment_failures", pa.int64()),
        ("ul", direction), ("dl", direction),
    ])
    group_count = pa.struct([("group_id", pa.string()), ("count", pa.int64())])
    group_upf_bucket = pa.struct([
        ("group_id", pa.string()), ("zone", pa.string()), ("dnn", pa.string()),
        ("snssai", pa.string()), ("five_qi", pa.int16()), ("upf_id", pa.string()),
        ("bucket_seconds", pa.int32()), ("active_sessions", pa.int64()),
        ("admitted_sessions", pa.int64()), ("establishment_failures", pa.int64()),
        ("offered_ul_mbps", pa.float64()), ("offered_dl_mbps", pa.float64()),
    ])
    group_generated_load = pa.struct([
        ("group_id", pa.string()), ("ul_mbps", pa.float64()),
        ("dl_mbps", pa.float64()),
    ])
    metadata = {b"schema_version": b"simulation-step/1.1", b"controller": controller.encode()}
    if summary is not None:
        metadata[b"summary"] = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
    return pa.schema([
        ("scenario_id", pa.string()), ("seed", pa.int64()), ("step", pa.int32()),
        ("window_start", pa.timestamp("us", tz="UTC")),
        ("window_end", pa.timestamp("us", tz="UTC")), ("policy_id", pa.string()),
        ("group_arrivals", pa.list_(group_count)),
        ("group_rejections", pa.list_(group_count)),
        ("group_upf_buckets", pa.list_(group_upf_bucket)),
        ("group_generated_load_mbps", pa.list_(group_generated_load)),
        ("unplaced_rejected_ul_bytes", pa.float64()),
        ("unplaced_rejected_dl_bytes", pa.float64()), ("upfs", pa.list_(upf)),
    ], metadata=metadata)


def audit_schema():
    try:
        import pyarrow as pa
    except ImportError as error:
        raise RuntimeError("PyArrow is required for selection-audit Parquet output") from error
    return pa.schema([
        ("schema_version", pa.string()), ("timestamp", pa.timestamp("us", tz="UTC")),
        ("session_id_hash", pa.string()), ("session_hash_value", pa.string()),
        ("zone", pa.string()), ("dnn", pa.string()), ("snssai", pa.string()),
        ("eligible_upfs", pa.list_(pa.string())),
        ("requested_weights", pa.map_(pa.string(), pa.float64())),
        ("selected_upf", pa.string()), ("policy_id", pa.string()), ("reason", pa.string()),
    ], metadata={b"schema_version": b"selection-audit/1.0"})


def _step_row(step: StepResult) -> dict[str, Any]:
    row = step.to_dict()
    row["window_start"] = step.window_start
    row["window_end"] = step.window_end
    row["group_arrivals"] = [
        {"group_id": key, "count": value} for key, value in sorted(step.group_arrivals.items())
    ]
    row["group_rejections"] = [
        {"group_id": key, "count": value} for key, value in sorted(step.group_rejections.items())
    ]
    row["group_generated_load_mbps"] = [
        {"group_id": key, "ul_mbps": value["ul"], "dl_mbps": value["dl"]}
        for key, value in sorted(step.group_generated_load_mbps.items())
    ]
    # This field supports the live demo but is intentionally not part of the
    # canonical offline schema; group_upf_buckets is the compact equivalent.
    row.pop("group_upf_admissions", None)
    return row


def _audit_row(audit: SelectionAudit) -> dict[str, Any]:
    return {
        "schema_version": audit.schema_version, "timestamp": audit.timestamp,
        "session_id_hash": audit.session_id_hash,
        "session_hash_value": audit.session_hash_value, "zone": audit.group.zone,
        "dnn": audit.group.dnn, "snssai": audit.group.snssai,
        "eligible_upfs": audit.eligible_upfs,
        "requested_weights": audit.requested_weights,
        "selected_upf": audit.selected_upf, "policy_id": audit.policy_id,
        "reason": audit.reason,
    }


@dataclass(slots=True)
class SummarySink:
    scenario_id: str
    seed: int
    step_seconds: int
    controller: str
    primary_overload_metric: str
    selection_audit_stride: int = 1
    _offered: dict[str, float] = field(default_factory=lambda: {"ul": 0.0, "dl": 0.0})
    _carried: dict[str, float] = field(default_factory=lambda: {"ul": 0.0, "dl": 0.0})
    _dropped: dict[str, float] = field(default_factory=lambda: {"ul": 0.0, "dl": 0.0})
    _rejected: dict[str, float] = field(default_factory=lambda: {"ul": 0.0, "dl": 0.0})
    _new: dict[str, float] = field(default_factory=lambda: {"ul": 0.0, "dl": 0.0})
    _duration: dict[str, float] = field(default_factory=lambda: {"ul": 0.0, "dl": 0.0})
    _area: dict[str, float] = field(default_factory=lambda: {"ul": 0.0, "dl": 0.0})
    _residual_area: dict[str, float] = field(default_factory=lambda: {"ul": 0.0, "dl": 0.0})
    _incremental_area: dict[str, float] = field(default_factory=lambda: {"ul": 0.0, "dl": 0.0})
    step_count: int = 0
    audit_count: int = 0
    failures: int = 0

    def accept_step(self, step: StepResult) -> None:
        if step.step != self.step_count:
            raise ValueError(f"summary step order violation: expected {self.step_count}, got {step.step}")
        upf_failures = 0
        for upf in step.upfs:
            upf_failures += upf.establishment_failures
            for direction in ("ul", "dl"):
                item = getattr(upf, direction)
                self._offered[direction] += item.offered_bytes
                self._carried[direction] += item.carried_bytes
                self._dropped[direction] += item.dropped_bytes
                self._rejected[direction] += item.rejected_bytes
                self._new[direction] += item.new_session_offered_bytes
                admitted_mbps = (
                    (item.offered_bytes - item.rejected_bytes) * 8
                    / self.step_seconds / 1_000_000
                )
                admitted_new = max(0.0, item.new_session_offered_bytes - item.rejected_bytes)
                residual_mbps = max(
                    0.0, admitted_mbps - admitted_new * 8 / self.step_seconds / 1_000_000
                )
                if item.safe_capacity_mbps > 0:
                    excess = max(0.0, admitted_mbps / item.safe_capacity_mbps - 1.0)
                    residual = max(0.0, residual_mbps / item.safe_capacity_mbps - 1.0)
                    incremental = excess - residual
                else:
                    excess = math.inf if admitted_mbps > 0 else 0.0
                    residual = math.inf if residual_mbps > 0 else 0.0
                    incremental = 0.0 if residual_mbps > 0 else excess
                if excess > 0:
                    self._duration[direction] += self.step_seconds
                    self._area[direction] += excess * self.step_seconds
                    self._residual_area[direction] += residual * self.step_seconds
                    self._incremental_area[direction] += incremental * self.step_seconds
        for direction, value in (
            ("ul", step.unplaced_rejected_ul_bytes),
            ("dl", step.unplaced_rejected_dl_bytes),
        ):
            self._rejected[direction] += value
            self._offered[direction] += value
            self._new[direction] += value
        self.failures += (
            upf_failures + sum(step.group_rejections.values()) - upf_failures
        )
        self.step_count += 1

    def accept_audit(self, audit: SelectionAudit) -> None:
        self.audit_count += 1

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id, "seed": self.seed,
            "controller": self.controller, "control_scope": "new_session_placement_only",
            "session_migration_supported": False,
            "primary_overload_metric": self.primary_overload_metric,
            "steps": self.step_count, "offered_bytes": dict(self._offered),
            "carried_bytes": dict(self._carried), "dropped_bytes": dict(self._dropped),
            "rejected_bytes": dict(self._rejected),
            "new_session_offered_bytes": dict(self._new),
            "controllable_load_fraction": {
                direction: self._new[direction] / self._offered[direction]
                if self._offered[direction] else 0.0 for direction in ("ul", "dl")
            },
            "overload_duration_seconds": dict(self._duration),
            "overload_area_seconds": dict(self._area),
            "residual_overload_area_seconds": dict(self._residual_area),
            "incremental_new_session_overload_area_seconds": dict(self._incremental_area),
            "establishment_failures": self.failures,
            "selection_audit_stride": self.selection_audit_stride,
            "selection_audits_retained": self.audit_count,
            "selection_audit_count": self.audit_count,
        }

    def flush(self) -> None: pass
    def close(self, *, success: bool) -> None: pass

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "offered": self._offered, "carried": self._carried,
            "dropped": self._dropped, "rejected": self._rejected, "new": self._new,
            "duration": self._duration, "area": self._area,
            "residual_area": self._residual_area, "incremental_area": self._incremental_area,
            "step_count": self.step_count, "audit_count": self.audit_count,
            "failures": self.failures,
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        for attribute, key in (
            ("_offered", "offered"), ("_carried", "carried"),
            ("_dropped", "dropped"), ("_rejected", "rejected"), ("_new", "new"),
            ("_duration", "duration"), ("_area", "area"),
            ("_residual_area", "residual_area"), ("_incremental_area", "incremental_area"),
        ):
            setattr(self, attribute, {str(k): float(v) for k, v in state[key].items()})
        self.step_count = int(state["step_count"])
        self.audit_count = int(state["audit_count"])
        self.failures = int(state["failures"])


class ParquetSink:
    """Bounded step sink that seals immutable, hash-addressed scratch segments."""

    def __init__(self, scratch_directory: str | Path, *, controller: str, row_group_size: int = 4096) -> None:
        if not 1 <= row_group_size <= 4096:
            raise ValueError("row_group_size must be in [1, 4096]")
        self.directory = Path(scratch_directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.controller = controller
        self.row_group_size = row_group_size
        self._rows: list[dict[str, Any]] = []
        self._segments: list[dict[str, Any]] = []
        self._next_step = 0

    def accept_step(self, step: StepResult) -> None:
        if step.step != self._next_step:
            raise ValueError(f"Parquet step order violation: expected {self._next_step}, got {step.step}")
        self._rows.append(_step_row(step))
        self._next_step += 1
        if len(self._rows) >= self.row_group_size:
            self.flush()

    def accept_audit(self, audit: SelectionAudit) -> None: pass

    def flush(self) -> None:
        if not self._rows:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq
        index = len(self._segments)
        temporary = self.directory / f".steps-{index:06d}.{os.getpid()}.tmp"
        schema = step_schema(controller=self.controller)
        table = pa.Table.from_pylist(self._rows, schema=schema)
        pq.write_table(table, temporary, compression="zstd", version="2.6", write_statistics=True)
        digest = file_sha256(temporary)
        first_step = self._next_step - len(self._rows)
        final = self.directory / f"steps-{first_step:012d}-{self._next_step - 1:012d}-{digest[:12]}.parquet"
        if final.exists():
            if file_sha256(final) != digest:
                raise ValueError(f"immutable Parquet segment collision: {final}")
            temporary.unlink()
        else:
            os.replace(temporary, final)
        self._segments.append({
            "path": final.name, "sha256": digest,
            "rows": len(self._rows), "first_step": first_step,
            "last_step": self._next_step - 1, "bytes": final.stat().st_size,
        })
        self._rows.clear()

    def snapshot_state(self) -> dict[str, Any]:
        if self._rows:
            raise RuntimeError("ParquetSink must be flushed before snapshot")
        return {"next_step": self._next_step, "segments": list(self._segments)}

    def restore_state(self, state: dict[str, Any]) -> None:
        segments = list(state.get("segments", []))
        expected_step = 0
        for segment in segments:
            path = self.directory / segment["path"]
            if not path.is_file() or file_sha256(path) != segment["sha256"]:
                raise ValueError(f"sealed Parquet segment failed validation: {path}")
            if int(segment["first_step"]) != expected_step:
                raise ValueError("sealed Parquet segments are not contiguous")
            expected_step = int(segment["last_step"]) + 1
        if expected_step != int(state["next_step"]):
            raise ValueError("sealed Parquet segment step count mismatch")
        self._segments = segments
        self._next_step = expected_step
        self._rows.clear()

    def finalize(self, destination: str | Path, summary: dict[str, Any]) -> ArtifactDescriptor:
        self.flush()
        import pyarrow as pa
        import pyarrow.parquet as pq
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        schema = step_schema(controller=self.controller, summary=summary)
        with pq.ParquetWriter(temporary, schema, compression="zstd", version="2.6", write_statistics=True) as writer:
            pending: list[dict[str, Any]] = []
            for segment in self._segments:
                for batch in pq.ParquetFile(self.directory / segment["path"]).iter_batches(
                    batch_size=self.row_group_size
                ):
                    for row in batch.to_pylist():
                        pending.append(row)
                        if len(pending) == self.row_group_size:
                            writer.write_table(pa.Table.from_pylist(pending, schema=schema))
                            pending.clear()
            if pending:
                writer.write_table(pa.Table.from_pylist(pending, schema=schema))
        os.replace(temporary, target)
        parquet = pq.ParquetFile(target)
        return ArtifactDescriptor(
            "detailed_steps", target.name, file_sha256(target), target.stat().st_size,
            rows=parquet.metadata.num_rows, row_groups=parquet.metadata.num_row_groups,
        )

    @property
    def sealed_segments(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._segments)

    @property
    def artifacts(self) -> list[ArtifactDescriptor]:
        return [
            ArtifactDescriptor(
                "step_segment", str(self.directory / item["path"]), item["sha256"],
                int(item["bytes"]), rows=int(item["rows"]), row_groups=1,
            ) for item in self._segments
        ]

    def close(self, *, success: bool) -> None:
        if success:
            self.flush()


class AuditSink:
    MODES = {"count", "hash_sample", "reservoir", "parquet"}
    ALIASES = {"counting": "count", "sample": "hash_sample", "full": "parquet"}

    def __init__(
        self, mode: str = "count", *, scratch_directory: str | Path | None = None,
        salt: str = "", sample_modulus: int = 100, reservoir_size: int = 1024,
        row_group_size: int = 4096,
    ) -> None:
        mode = self.ALIASES.get(mode, mode)
        if mode not in self.MODES:
            raise ValueError(f"unsupported audit mode: {mode}")
        if row_group_size < 1 or row_group_size > 4096:
            raise ValueError("audit row_group_size must be in [1, 4096]")
        if sample_modulus < 1 or reservoir_size < 1:
            raise ValueError("audit sample modulus and reservoir size must be positive")
        self.mode, self.salt = mode, salt
        self.sample_modulus, self.reservoir_size = sample_modulus, reservoir_size
        self.row_group_size = row_group_size
        self.directory = Path(scratch_directory) if scratch_directory is not None else None
        if mode != "count" and self.directory is None:
            raise ValueError("retained audit modes require scratch_directory")
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self.retained = 0
        self._rows: list[dict[str, Any]] = []
        self._reservoir: list[tuple[int, dict[str, Any]]] = []
        self._segments: list[dict[str, Any]] = []

    def accept_step(self, step: StepResult) -> None: pass

    def accept_audit(self, audit: SelectionAudit) -> None:
        self.count += 1
        row = _audit_row(audit)
        rank = int(hashlib.sha256(f"{self.salt}\x1f{audit.session_id_hash}".encode()).hexdigest(), 16)
        if self.mode == "count":
            return
        if self.mode == "hash_sample":
            if rank % self.sample_modulus:
                return
            self._rows.append(row)
        elif self.mode == "reservoir":
            self._reservoir.append((rank, row))
            self._reservoir.sort(key=lambda item: item[0])
            if len(self._reservoir) > self.reservoir_size:
                self._reservoir.pop()
            self.retained = len(self._reservoir)
            return
        else:
            self._rows.append(row)
        self.retained += 1
        if len(self._rows) >= self.row_group_size:
            self.flush()

    def flush(self) -> None:
        if self.mode == "reservoir":
            return
        if not self._rows:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq
        assert self.directory is not None
        index = len(self._segments)
        temporary = self.directory / f".audits-{index:06d}.{os.getpid()}.tmp"
        pq.write_table(pa.Table.from_pylist(self._rows, schema=audit_schema()), temporary, compression="zstd")
        digest = file_sha256(temporary)
        final = self.directory / f"audits-{index:06d}-{digest[:12]}.parquet"
        if final.exists():
            if file_sha256(final) != digest:
                raise ValueError(f"immutable audit segment collision: {final}")
            temporary.unlink()
        else:
            os.replace(temporary, final)
        self._segments.append({
            "path": final.name, "sha256": digest, "rows": len(self._rows),
            "bytes": final.stat().st_size,
        })
        self._rows.clear()

    def snapshot_state(self) -> dict[str, Any]:
        if self._rows:
            raise RuntimeError("AuditSink must be flushed before snapshot")
        reservoir = [
            [rank, {**row, "timestamp": iso_utc(row["timestamp"])}]
            for rank, row in self._reservoir
        ]
        return {
            "mode": self.mode, "count": self.count, "retained": self.retained,
            "segments": list(self._segments), "reservoir": reservoir,
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        if state.get("mode") != self.mode:
            raise ValueError("checkpoint audit mode mismatch")
        for segment in state.get("segments", []):
            assert self.directory is not None
            path = self.directory / segment["path"]
            if not path.is_file() or file_sha256(path) != segment["sha256"]:
                raise ValueError(f"sealed audit segment failed validation: {path}")
        self.count, self.retained = int(state["count"]), int(state["retained"])
        self._segments = list(state.get("segments", []))
        self._reservoir = [
            (int(rank), {**row, "timestamp": parse_utc(row["timestamp"])})
            for rank, row in state.get("reservoir", [])
        ]
        self._rows.clear()

    def finalize(self, destination: str | Path) -> ArtifactDescriptor:
        if self.mode == "reservoir":
            self._rows = [row for _, row in sorted(self._reservoir)]
            self.retained = len(self._rows)
        self.flush()
        import pyarrow as pa
        import pyarrow.parquet as pq
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        with pq.ParquetWriter(temporary, audit_schema(), compression="zstd") as writer:
            pending: list[dict[str, Any]] = []
            for segment in self._segments:
                for batch in pq.ParquetFile(self.directory / segment["path"]).iter_batches(
                    batch_size=self.row_group_size
                ):
                    for row in batch.to_pylist():
                        pending.append(row)
                        if len(pending) == self.row_group_size:
                            writer.write_table(pa.Table.from_pylist(pending, schema=audit_schema()))
                            pending.clear()
            if pending:
                writer.write_table(pa.Table.from_pylist(pending, schema=audit_schema()))
        os.replace(temporary, target)
        parquet = pq.ParquetFile(target)
        return ArtifactDescriptor(
            "selection_audits", target.name, file_sha256(target), target.stat().st_size,
            rows=parquet.metadata.num_rows, row_groups=parquet.metadata.num_row_groups,
        )

    def close(self, *, success: bool) -> None:
        if success:
            self.flush()

    @property
    def artifacts(self) -> list[ArtifactDescriptor]:
        return [
            ArtifactDescriptor(
                "audit_segment", str(self.directory / item["path"]), item["sha256"],
                int(item["bytes"]), rows=int(item["rows"]), row_groups=1,
            ) for item in self._segments
        ]


class JsonlSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a", encoding="utf-8", newline="\n")
        self.steps = self.audits = 0

    def accept_step(self, step: StepResult) -> None:
        self._stream.write(json.dumps(
            {"record_type": "simulation_step", "schema_version": "simulation-step/0.2", **step.to_dict()},
            sort_keys=True, separators=(",", ":"),
        ) + "\n")
        self.steps += 1

    def accept_audit(self, audit: SelectionAudit) -> None:
        self._stream.write(json.dumps(
            {"record_type": "selection_audit", **audit.to_dict()},
            sort_keys=True, separators=(",", ":"),
        ) + "\n")
        self.audits += 1

    def flush(self) -> None:
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def snapshot_state(self) -> dict[str, Any]:
        self.flush()
        return {"steps": self.steps, "audits": self.audits, "bytes": self.path.stat().st_size}

    def restore_state(self, state: dict[str, Any]) -> None:
        self.flush()
        if self.path.stat().st_size != int(state["bytes"]):
            raise ValueError("JSONL checkpoint length mismatch")
        self.steps, self.audits = int(state["steps"]), int(state["audits"])

    def close(self, *, success: bool) -> None:
        if not self._stream.closed:
            self.flush()
            self._stream.close()

    @property
    def artifacts(self) -> list[ArtifactDescriptor]:
        if not self.path.is_file():
            return []
        return [ArtifactDescriptor(
            "debug_jsonl", str(self.path), file_sha256(self.path), self.path.stat().st_size,
            rows=self.steps + self.audits,
        )]


class CompositeSink:
    def __init__(self, sinks: Iterable[Any]) -> None:
        self.sinks = tuple(sinks)
        if not self.sinks:
            raise ValueError("at least one sink is required")

    def accept_step(self, step: StepResult) -> None:
        for sink in self.sinks:
            sink.accept_step(step)

    def accept_audit(self, audit: SelectionAudit) -> None:
        for sink in self.sinks:
            sink.accept_audit(audit)

    def accept_policy(
        self, policy: Policy | None, step: int, details: dict[str, Any] | None = None
    ) -> None:
        for sink in self.sinks:
            callback = getattr(sink, "accept_policy", None)
            if callback is not None:
                callback(policy, step, details)

    def flush(self) -> None:
        for sink in self.sinks:
            sink.flush()

    def snapshot_state(self) -> dict[str, Any]:
        self.flush()
        return {
            f"{type(sink).__name__}:{index}": sink.snapshot_state()
            for index, sink in enumerate(self.sinks)
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        for index, sink in enumerate(self.sinks):
            key = f"{type(sink).__name__}:{index}"
            if key not in state:
                raise ValueError(f"checkpoint is missing sink state {key}")
            sink.restore_state(state[key])

    def close(self, *, success: bool) -> None:
        errors: list[BaseException] = []
        for sink in self.sinks:
            try:
                sink.close(success=success)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]

    @property
    def artifacts(self) -> list[ArtifactDescriptor]:
        result: list[ArtifactDescriptor] = []
        for sink in self.sinks:
            result.extend(getattr(sink, "artifacts", ()))
        return result


class BoundedMemorySink:
    """Explicit fixed-size collector for the 100-tick causal demo and unit probes."""

    def __init__(self, summary_sink: SummarySink, *, max_steps: int = 100, max_audits: int = 0) -> None:
        if max_steps < 1 or max_audits < 0:
            raise ValueError("bounded collector limits are invalid")
        self.summary_sink = summary_sink
        self.max_steps, self.max_audits = max_steps, max_audits
        self.steps: list[StepResult] = []
        self.selection_audits: list[SelectionAudit] = []

    @property
    def summary(self) -> dict[str, Any]:
        return self.summary_sink.summary

    def accept_step(self, step: StepResult) -> None:
        if len(self.steps) >= self.max_steps:
            raise RuntimeError("bounded in-memory step capacity exceeded")
        self.steps.append(step)
        self.summary_sink.accept_step(step)

    def accept_audit(self, audit: SelectionAudit) -> None:
        self.summary_sink.accept_audit(audit)
        if len(self.selection_audits) < self.max_audits:
            self.selection_audits.append(audit)

    def flush(self) -> None: pass
    def close(self, *, success: bool) -> None: pass
    def snapshot_state(self) -> dict[str, Any]:
        raise RuntimeError("the demo collector is rewound by the demo's bounded deepcopy checkpoint")
    def restore_state(self, state: dict[str, Any]) -> None:
        raise RuntimeError("the demo collector is rewound by the demo's bounded deepcopy checkpoint")

    def write_jsonl(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({
                "record_type": "simulation_metadata",
                "schema_version": "simulation-metadata/0.1", **self.summary,
            }, sort_keys=True, separators=(",", ":")) + "\n")
            for step in self.steps:
                stream.write(json.dumps({
                    "record_type": "simulation_step", "schema_version": "simulation-step/0.2",
                    **step.to_dict(),
                }, sort_keys=True, separators=(",", ":")) + "\n")
            for audit in self.selection_audits:
                stream.write(json.dumps({
                    "record_type": "selection_audit", **audit.to_dict(),
                }, sort_keys=True, separators=(",", ":")) + "\n")

    def write_parquet(self, path: str | Path) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        schema = step_schema(controller=self.summary_sink.controller, summary=self.summary)
        with pq.ParquetWriter(destination, schema, compression="zstd", version="2.6") as writer:
            for start in range(0, len(self.steps), 4096):
                writer.write_table(pa.Table.from_pylist(
                    [_step_row(step) for step in self.steps[start:start + 4096]], schema=schema
                ))

    def write_selection_audits_parquet(self, path: str | Path) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with pq.ParquetWriter(destination, audit_schema(), compression="zstd") as writer:
            for start in range(0, len(self.selection_audits), 4096):
                writer.write_table(pa.Table.from_pylist(
                    [_audit_row(audit) for audit in self.selection_audits[start:start + 4096]],
                    schema=audit_schema(),
                ))


class DecisionTraceSink:
    """Bounded Gold-tier stream of every controller decision and full policy."""

    def __init__(self, scratch_directory: str | Path, *, row_group_size: int = 4096) -> None:
        if not 1 <= row_group_size <= 4096:
            raise ValueError("decision trace row_group_size must be in [1, 4096]")
        self.directory = Path(scratch_directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.row_group_size = row_group_size
        self._rows: list[dict[str, Any]] = []
        self._segments: list[dict[str, Any]] = []
        self.count = 0

    @staticmethod
    def _schema():
        import pyarrow as pa
        return pa.schema([
            ("step", pa.int32()), ("policy_id", pa.string()),
            ("policy_version", pa.int32()), ("policy_json", pa.string()),
            ("decision_json", pa.string()),
        ], metadata={b"schema_version": b"decision-trace/1.0"})

    def accept_policy(
        self, policy: Policy | None, step: int, details: dict[str, Any] | None = None
    ) -> None:
        payload = policy.to_dict() if policy is not None else None
        self._rows.append({
            "step": step, "policy_id": policy.policy_id if policy else "none",
            "policy_version": policy.policy_version if policy else 0,
            "policy_json": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            "decision_json": json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
        })
        self.count += 1
        if len(self._rows) >= self.row_group_size:
            self.flush()

    def accept_step(self, step: StepResult) -> None: pass
    def accept_audit(self, audit: SelectionAudit) -> None: pass

    def flush(self) -> None:
        if not self._rows:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq
        index = len(self._segments)
        temporary = self.directory / f".decisions-{index:06d}.{os.getpid()}.tmp"
        pq.write_table(pa.Table.from_pylist(self._rows, schema=self._schema()), temporary, compression="zstd")
        digest = file_sha256(temporary)
        final = self.directory / f"decisions-{index:06d}-{digest[:12]}.parquet"
        if final.exists():
            if file_sha256(final) != digest:
                raise ValueError(f"immutable decision segment collision: {final}")
            temporary.unlink()
        else:
            os.replace(temporary, final)
        self._segments.append({
            "path": final.name, "sha256": digest,
            "rows": len(self._rows), "bytes": final.stat().st_size,
        })
        self._rows.clear()

    def snapshot_state(self) -> dict[str, Any]:
        if self._rows:
            raise RuntimeError("DecisionTraceSink must be flushed before snapshot")
        return {"count": self.count, "segments": list(self._segments)}

    def restore_state(self, state: dict[str, Any]) -> None:
        for segment in state.get("segments", []):
            path = self.directory / segment["path"]
            if not path.is_file() or file_sha256(path) != segment["sha256"]:
                raise ValueError(f"sealed decision segment failed validation: {path}")
        self.count = int(state["count"])
        self._segments = list(state.get("segments", []))
        self._rows.clear()

    def finalize(self, destination: str | Path) -> ArtifactDescriptor:
        self.flush()
        import pyarrow as pa
        import pyarrow.parquet as pq
        target = Path(destination)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        with pq.ParquetWriter(temporary, self._schema(), compression="zstd") as writer:
            pending: list[dict[str, Any]] = []
            for segment in self._segments:
                for batch in pq.ParquetFile(self.directory / segment["path"]).iter_batches(
                    batch_size=self.row_group_size
                ):
                    for row in batch.to_pylist():
                        pending.append(row)
                        if len(pending) == self.row_group_size:
                            writer.write_table(pa.Table.from_pylist(pending, schema=self._schema()))
                            pending.clear()
            if pending:
                writer.write_table(pa.Table.from_pylist(pending, schema=self._schema()))
        os.replace(temporary, target)
        parquet = pq.ParquetFile(target)
        return ArtifactDescriptor(
            "decision_traces", target.name, file_sha256(target), target.stat().st_size,
            rows=parquet.metadata.num_rows, row_groups=parquet.metadata.num_row_groups,
        )

    def close(self, *, success: bool) -> None:
        if success:
            self.flush()

    @property
    def artifacts(self) -> list[ArtifactDescriptor]:
        return [
            ArtifactDescriptor(
                "decision_segment", str(self.directory / item["path"]), item["sha256"],
                int(item["bytes"]), rows=int(item["rows"]), row_groups=1,
            ) for item in self._segments
        ]
