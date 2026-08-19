from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import atomic_json
from .build_stage1_report import assess


def build(combined: dict[str, Any]) -> dict[str, Any]:
    if combined.get("schema_version") != "packed-multinode-run/1.0":
        raise ValueError("unsupported packed multi-node report")
    reasons: list[str] = []
    node_reports = combined.get("node_reports", [])
    if len(node_reports) != int(combined.get("node_count", 0)):
        reasons.append("incomplete_node_report_set")
    for report in node_reports:
        passed, node_reasons = assess(report)
        if not passed:
            index = report.get("partition_index")
            reasons.extend(f"node_{index}:{reason}" for reason in node_reasons)
    if int(combined.get("failures", 0)):
        reasons.append("multinode_failures_detected")
    if not combined.get("work_list_sha256") or not combined.get("campaign_input_sha256"):
        reasons.append("frozen_campaign_identity_missing")
    return {
        "schema_version": "stage2-pilot-report/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "passed" if not reasons else "failed",
        "reasons": sorted(set(reasons)),
        "campaign_id": combined.get("campaign_id"),
        "node_count": combined.get("node_count"),
        "worker_count": combined.get("workers_per_node"),
        "work_list_sha256": combined.get("work_list_sha256"),
        "campaign_input_sha256": combined.get("campaign_input_sha256"),
        "metrics": {
            key: combined.get(key) for key in (
                "work_items", "wall_seconds", "cpu_efficiency",
                "aggregate_peak_rss_bytes", "allocated_memory_bytes",
                "peak_swap_bytes", "scratch_peak_bytes", "scratch_allocation_bytes",
                "artifact_bytes", "stage_out_seconds", "stage_out_wall_fraction",
                "failures", "hosts",
            )
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate a Stage 2 multi-node pilot")
    parser.add_argument("--combined", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build(json.loads(args.combined.read_text(encoding="utf-8")))
    atomic_json(args.output, report)
    print(json.dumps({key: value for key, value in report.items() if key != "metrics"}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
