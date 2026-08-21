"""Fit deployable survival tables from lifecycle JSONL and nothing else.

This module is the distribution-blind side of the Phase 3.1 interface.  It
does not import scenario configuration or the simulator and accepts no hidden
holding-time parameters.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import heapq
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from optimization import (
    EmpiricalSurvivalProvider,
    SessionTelemetry,
    extract_session_lifecycles,
    write_survival_tables,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _priority(session_id: str) -> int:
    return int.from_bytes(hashlib.sha256(session_id.encode()).digest()[:8], "big")


def load_lifecycle_export(
    path: Path, *, max_per_group: int | None = None,
) -> tuple[dict[str, Any], tuple[SessionTelemetry, ...], dict[str, str]]:
    """Read and deterministically cap deployment-shaped lifecycle telemetry."""

    if max_per_group is not None and max_per_group < 1:
        raise ValueError("max_per_group must be positive")
    metadata: dict[str, Any] | None = None
    heaps: dict[str, list[tuple[int, int, SessionTelemetry]]] = defaultdict(list)
    all_rows: list[SessionTelemetry] = []
    group_to_service: dict[str, str] = {}
    sequence = 0
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            payload = json.loads(line)
            record_type = payload.get("record_type")
            if record_type == "lifecycle_export_metadata":
                if metadata is not None or payload.get("schema_version") != "lifecycle-export/1.0":
                    raise ValueError(f"invalid lifecycle metadata at line {line_number}")
                metadata = payload
                continue
            if record_type != "session_lifecycle" or payload.get("schema_version") != "session-lifecycle/1.0":
                raise ValueError(f"unsupported lifecycle record at line {line_number}")
            row = SessionTelemetry(
                session_id=str(payload["session_id"]),
                group_id=str(payload["group_id"]),
                started_step=int(payload["started_step"]),
                ended_step=(
                    int(payload["ended_step"])
                    if payload.get("ended_step") is not None else None
                ),
                service_class=str(payload["service_class"]),
            )
            previous = group_to_service.setdefault(row.group_id, row.service_class or "")
            if previous != (row.service_class or ""):
                raise ValueError(f"group {row.group_id} has inconsistent service classes")
            if max_per_group is None:
                all_rows.append(row)
            else:
                # A max-heap encoded with negative priorities retains the
                # smallest stable SHA-256 ranks without arrival-order bias.
                heap = heaps[row.group_id]
                candidate = (-_priority(row.session_id), -sequence, row)
                if len(heap) < max_per_group:
                    heapq.heappush(heap, candidate)
                elif candidate > heap[0]:
                    heapq.heapreplace(heap, candidate)
            sequence += 1
    if metadata is None:
        raise ValueError("lifecycle export metadata is missing")
    if max_per_group is not None:
        all_rows = [
            item[2]
            for group_id in sorted(heaps)
            for item in sorted(heaps[group_id], reverse=True)
        ]
    return metadata, tuple(all_rows), group_to_service


def fit_lifecycle_file(
    telemetry_path: Path,
    output_path: Path,
    *,
    bucket_steps: int,
    bucket_count: int,
    minimum_group_samples: int = 100,
    max_per_group: int | None = None,
    generated_at: datetime | None = None,
    now: datetime | None = None,
    stale_after_seconds: float = 172_800,
) -> dict[str, Any]:
    metadata, telemetry, group_to_service = load_lifecycle_export(
        telemetry_path, max_per_group=max_per_group
    )
    cutoff = int(metadata["observed_through_step"])
    lifecycles = extract_session_lifecycles(
        telemetry, observed_through_step=cutoff
    )
    if not lifecycles:
        raise ValueError("lifecycle export contains no causally usable records")
    fitted_at = generated_at or datetime.now(timezone.utc)
    provider = EmpiricalSurvivalProvider(
        lifecycles,
        bucket_steps=bucket_steps,
        bucket_count=bucket_count,
        minimum_group_samples=minimum_group_samples,
        stale_after_seconds=stale_after_seconds,
        generated_at=fitted_at,
    )
    tables = provider.tables(group_to_service, now=now or fitted_at)
    sources = Counter(table.source for table in tables.values())
    selected_by_group = Counter(row.group_id for row in lifecycles)
    completed = sum(row.completed for row in lifecycles)
    telemetry_hash = _sha256(telemetry_path)
    provenance = {
        "input_contract": "lifecycle-export/1.0",
        "telemetry_sha256": telemetry_hash,
        "observed_through_step": cutoff,
        "fit_uses_hidden_lifetime_parameters": False,
        "bucket_steps": bucket_steps,
        "bucket_count": bucket_count,
        "minimum_group_samples": minimum_group_samples,
        "max_per_group": max_per_group,
    }
    guardrail = {
        "measured": False,
        "passed": False,
        "comparison_sha256": None,
        "criteria": {},
        "reason": "pending_closed_loop_relative_equivalence_and_operational_checks",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    write_survival_tables(
        str(temporary), tables, guardrail_evidence=guardrail, provenance=provenance
    )
    os.replace(temporary, output_path)
    return {
        "schema_version": "lifecycle-survival-fit-report/1.0",
        "telemetry": str(telemetry_path.resolve()),
        "telemetry_sha256": telemetry_hash,
        "output": str(output_path.resolve()),
        "groups": len(tables),
        "records_selected": len(lifecycles),
        "completed": completed,
        "right_censored": len(lifecycles) - completed,
        "censor_fraction": (len(lifecycles) - completed) / len(lifecycles),
        "source_counts": dict(sorted(sources.items())),
        "pooling_coverage": sources.get("pooled-service-class-kaplan-meier", 0) / len(tables),
        "selected_samples_by_group": dict(sorted(selected_by_group.items())),
        "stale_groups": sum(table.stale for table in tables.values()),
        "distribution_blind": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--bucket-steps", type=int, required=True)
    parser.add_argument("--bucket-count", type=int, required=True)
    parser.add_argument("--minimum-group-samples", type=int, default=100)
    parser.add_argument("--max-per-group", type=int)
    parser.add_argument("--stale-after-seconds", type=float, default=172_800)
    parser.add_argument("--generated-at")
    parser.add_argument("--now")
    args = parser.parse_args()
    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    report = fit_lifecycle_file(
        args.telemetry,
        args.output,
        bucket_steps=args.bucket_steps,
        bucket_count=args.bucket_count,
        minimum_group_samples=args.minimum_group_samples,
        max_per_group=args.max_per_group,
        stale_after_seconds=args.stale_after_seconds,
        generated_at=parse(args.generated_at),
        now=parse(args.now),
    )
    if args.report:
        from experiments.artifacts import atomic_json
        atomic_json(args.report, report)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
