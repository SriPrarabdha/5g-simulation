from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .artifacts import atomic_json


def aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not reports:
        raise ValueError("at least one node report is required")
    if any(report.get("schema_version") != "packed-node-run/1.0" for report in reports):
        raise ValueError("unsupported packed node report")
    partition_counts = {int(report.get("partition_count", 1)) for report in reports}
    if len(partition_counts) != 1 or partition_counts.pop() != len(reports):
        raise ValueError("node reports do not form one complete partition set")
    indices = {int(report.get("partition_index", 0)) for report in reports}
    if indices != set(range(len(reports))):
        raise ValueError("node report partition indices are incomplete or duplicated")
    campaigns = {report["campaign_id"] for report in reports}
    repetitions = {report.get("repetition") for report in reports}
    worker_counts = {int(report["worker_count"]) for report in reports}
    if len(campaigns) != 1 or len(repetitions) != 1 or len(worker_counts) != 1:
        raise ValueError("node reports disagree on campaign, repetition, or workers per node")
    work_list_hashes = {report.get("work_list_sha256") for report in reports}
    campaign_input_hashes = {report.get("campaign_input_sha256") for report in reports}
    if len(work_list_hashes) != 1 or len(campaign_input_hashes) != 1:
        raise ValueError("node reports disagree on frozen work list or campaign inputs")

    ordered = sorted(reports, key=lambda report: int(report["partition_index"]))
    node_count = len(ordered)
    workers_per_node = worker_counts.pop()
    wall = max(float(report["wall_seconds"]) for report in ordered)
    cpu_seconds = sum(float(report["cpu_seconds"]) for report in ordered)
    stage_out_seconds = sum(float(report["stage_out_seconds"]) for report in ordered)
    artifact_bytes = sum(int(report["artifact_bytes"]) for report in ordered)
    results = [item for report in ordered for item in report.get("results", [])]
    return {
        "schema_version": "packed-multinode-run/1.0",
        "campaign_id": campaigns.pop(),
        "work_list_sha256": work_list_hashes.pop(),
        "campaign_input_sha256": campaign_input_hashes.pop(),
        "repetition": repetitions.pop(),
        "node_count": node_count,
        "workers_per_node": workers_per_node,
        "total_worker_count": workers_per_node * node_count,
        "work_items": sum(int(report["work_items"]) for report in ordered),
        "wall_seconds": wall,
        "cpu_seconds": cpu_seconds,
        "cpu_efficiency": cpu_seconds / (wall * workers_per_node * node_count) if wall else 0.0,
        "aggregate_peak_rss_bytes": sum(int(report["aggregate_peak_rss_bytes"]) for report in ordered),
        "allocated_memory_bytes": sum(int(report.get("allocated_memory_bytes") or 0) for report in ordered),
        "peak_swap_bytes": sum(int(report.get("peak_swap_bytes", 0)) for report in ordered),
        "scratch_peak_bytes": sum(int(report.get("scratch_peak_bytes", 0)) for report in ordered),
        "scratch_allocation_bytes": sum(int(report.get("scratch_allocation_bytes") or 0) for report in ordered),
        "artifact_bytes": artifact_bytes,
        "stage_out_seconds": stage_out_seconds,
        "shared_stage_out_throughput_bytes_per_second": (
            artifact_bytes / stage_out_seconds if stage_out_seconds else 0.0
        ),
        "stage_out_wall_fraction": stage_out_seconds / (node_count * wall) if wall else 0.0,
        "stage_out_measurement": "mean per-node conservative summed worker service fraction",
        "failures": sum(int(report["failures"]) for report in ordered),
        "hosts": [report.get("host") for report in ordered],
        "node_reports": ordered,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Combine a complete set of Stage 1 node reports")
    parser.add_argument("--report", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    combined = aggregate([json.loads(path.read_text(encoding="utf-8")) for path in args.report])
    atomic_json(args.output, combined)
    print(json.dumps({key: value for key, value in combined.items() if key not in {"node_reports", "results"}}, indent=2, sort_keys=True))
    return 1 if combined["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
