from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from optimization import (
    OracleBoundResult,
    OracleMetrics,
    bucket_arrivals_from_steps,
    evaluate_allocation,
    solve_bounded_migration_bound,
    solve_new_session_bound,
    static_capacity_allocation,
)
from simulator.macro import load_scenario
from simulator.macro.config import ScenarioConfig


SCHEMA_VERSION = "oracle-bound-evaluation/1.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _arrival_trace(parquet_path: Path) -> list[dict[str, int]]:
    table = pq.read_table(parquet_path, columns=["step", "group_arrivals"])
    rows = sorted(table.to_pylist(), key=lambda row: row["step"])
    return [
        {item["group_id"]: int(item["count"]) for item in row["group_arrivals"]}
        for row in rows
    ]


def _apply_knowledge_overlay(
    config: ScenarioConfig,
    overlay: dict[str, Any] | None,
) -> ScenarioConfig:
    if overlay is None:
        return config
    if overlay.get("schema_version") != "scenario-event-knowledge-overlay/1.0":
        raise ValueError("unsupported event knowledge overlay schema")
    declarations = {
        (item["event_type"], item.get("upf_id"), int(item["step"])): int(item["known_at_step"])
        for item in overlay.get("events", [])
    }
    if len(declarations) != len(overlay.get("events", [])):
        raise ValueError("event knowledge overlay contains duplicate declarations")
    matched: set[tuple[str, str | None, int]] = set()
    events = []
    for event in config.events:
        key = (event.event_type, event.upf_id, event.step)
        if key in declarations:
            matched.add(key)
            events.append(replace(event, known_at_step=declarations[key]))
        else:
            events.append(event)
    missing = set(declarations) - matched
    if missing:
        raise ValueError(f"knowledge overlay does not match scenario events: {sorted(missing)}")
    return replace(config, events=tuple(events))


def _bound_record(
    result: OracleBoundResult,
    modeled_static: OracleMetrics,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "regime": result.regime,
        "status": result.status,
        "message": result.message,
        "runtime_ms": result.runtime_ms,
        "continuous_relaxation": result.continuous_relaxation,
        "deployable": result.deployable,
        "allocation_bucket_count": len(result.allocation),
        "metrics": asdict(result.metrics) if result.metrics is not None else None,
    }
    if result.metrics is None:
        record.update({
            "ul_overload_area_relative_reduction": None,
            "reaches_20_percent_ul_gate": False,
            "guardrails_satisfied": False,
        })
        return record
    baseline = modeled_static.overload_area_seconds["ul"]
    reduction = (
        (baseline - result.metrics.overload_area_seconds["ul"]) / baseline
        if baseline > 0
        else None
    )
    tolerance = 1e-7
    guardrails = (
        result.metrics.overload_area_seconds["dl"]
        <= modeled_static.overload_area_seconds["dl"] + tolerance
        and all(
            result.metrics.dropped_bytes[direction]
            <= modeled_static.dropped_bytes[direction] + tolerance
            for direction in ("ul", "dl")
        )
    )
    record.update({
        "ul_overload_area_relative_reduction": reduction,
        "reaches_20_percent_ul_gate": (
            reduction is not None and reduction >= 0.2 and guardrails
        ),
        "guardrails_satisfied": guardrails,
    })
    return record


def evaluate_pair(
    manifest: Path,
    static_metadata_path: Path,
    *,
    migration_fraction: float,
    timeout_seconds: float,
    knowledge_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = load_scenario(manifest)
    metadata = json.loads(static_metadata_path.read_text(encoding="utf-8"))
    if metadata.get("controller") != "static-capacity-v1":
        raise ValueError(f"not a static shard: {static_metadata_path}")
    if metadata.get("scenario_id") != config.scenario_id or int(metadata.get("seed")) != config.seed:
        raise ValueError("static shard is not paired with the manifest scenario and seed")
    if metadata.get("manifest_sha256") != _sha256(manifest):
        raise ValueError("static shard manifest checksum does not match")
    parquet_path = static_metadata_path.parent / metadata["canonical_file"]
    if metadata.get("parquet_file_sha256") != _sha256(parquet_path):
        raise ValueError("static shard Parquet checksum does not match")
    arrivals = bucket_arrivals_from_steps(config, _arrival_trace(parquet_path))
    modeled_static = evaluate_allocation(
        config, arrivals, static_capacity_allocation(config)
    )
    actual_static = {
        metric: {
            direction: float(metadata["summary"][metric][direction])
            for direction in ("ul", "dl")
        }
        for metric in ("overload_area_seconds", "dropped_bytes")
    }
    scheduled_config = _apply_knowledge_overlay(config, knowledge_overlay)
    bounds = [
        solve_new_session_bound(
            scheduled_config if regime == "scheduled_fault" else config,
            arrivals,
            regime=regime,
            guardrail_metrics=modeled_static,
            timeout_seconds=timeout_seconds,
        )
        for regime in ("arrival_only", "scheduled_fault", "clairvoyant_fault")
    ]
    bounds.append(solve_bounded_migration_bound(
        config,
        arrivals,
        migration_fraction_per_bucket=migration_fraction,
        guardrail_metrics=modeled_static,
        timeout_seconds=timeout_seconds,
    ))
    scheduled_fault_events = [
        event for event in scheduled_config.events
        if event.event_type in {"capacity_factor", "health"}
        and event.known_at_step is not None
    ]
    return {
        "scenario_id": config.scenario_id,
        "seed": config.seed,
        "manifest": str(manifest.resolve()),
        "manifest_sha256": _sha256(manifest),
        "static_metadata": str(static_metadata_path.resolve()),
        "arrival_source": "exact paired static shard; aggregated to decision buckets",
        "cohort_survival": "expected uniform-lifetime continuous relaxation",
        "scheduled_capacity_health_events_declared": len(scheduled_fault_events),
        "modeled_static_metrics": asdict(modeled_static),
        "actual_static_metrics": actual_static,
        "bounds": [_bound_record(result, modeled_static) for result in bounds],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate offline oracle action-space bounds on paired static shards"
    )
    parser.add_argument("--manifest", action="append", type=Path, required=True)
    parser.add_argument("--static-metadata", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--migration-fraction", type=float, default=0.1)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--fault-knowledge-overlay", type=Path)
    args = parser.parse_args()
    if len(args.manifest) != len(args.static_metadata):
        parser.error("--manifest and --static-metadata counts must match")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite oracle evaluation: {args.output}")
    knowledge_overlay = (
        json.loads(args.fault_knowledge_overlay.read_text(encoding="utf-8"))
        if args.fault_knowledge_overlay is not None
        else None
    )
    scenarios = [
        evaluate_pair(
            manifest,
            metadata,
            migration_fraction=args.migration_fraction,
            timeout_seconds=args.timeout_seconds,
            knowledge_overlay=knowledge_overlay,
        )
        for manifest, metadata in zip(args.manifest, args.static_metadata)
    ]
    clairvoyant = [
        next(bound for bound in scenario["bounds"] if bound["regime"] == "clairvoyant_fault")
        for scenario in scenarios
    ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "non_deployable": True,
        "test_seeds_consumed": False,
        "migration_fraction_per_decision_bucket": args.migration_fraction,
        "fault_knowledge_overlay": (
            {
                "path": str(args.fault_knowledge_overlay.resolve()),
                "sha256": _sha256(args.fault_knowledge_overlay),
            }
            if args.fault_knowledge_overlay is not None
            else None
        ),
        "decision": (
            "new_session_gate_reachable_in_continuous_relaxation"
            if all(item["reaches_20_percent_ul_gate"] for item in clairvoyant)
            else "new_session_gate_unreachable_in_continuous_relaxation"
        ),
        "scenarios": scenarios,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "decision": payload["decision"],
        "scenarios": [
            {
                "scenario_id": scenario["scenario_id"],
                "bounds": [
                    {
                        "regime": bound["regime"],
                        "status": bound["status"],
                        "ul_reduction": bound["ul_overload_area_relative_reduction"],
                        "gate": bound["reaches_20_percent_ul_gate"],
                    }
                    for bound in scenario["bounds"]
                ],
            }
            for scenario in scenarios
        ],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
