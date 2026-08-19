from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import atomic_json


RUNGS = (8, 16, 32, 64)


def assess(run: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    memory = run.get("allocated_memory_bytes")
    scratch = run.get("scratch_allocation_bytes")
    if not memory or run["aggregate_peak_rss_bytes"] > 0.75 * memory:
        reasons.append("aggregate_peak_rss_exceeds_75_percent_or_allocation_unknown")
    if float(run["cpu_efficiency"]) < 0.70:
        reasons.append("cpu_efficiency_below_70_percent")
    if run.get("failures") or any(value not in {0, None} for value in run.get("exit_status", {}).values()):
        reasons.append("worker_failure_oom_incomplete_or_hash_failure")
    if int(run.get("peak_swap_bytes", 0)):
        reasons.append("swap_activity_detected")
    if not scratch or run["scratch_peak_bytes"] > 0.70 * scratch:
        reasons.append("scratch_exceeds_70_percent_or_allocation_unknown")
    if float(run.get("stage_out_wall_fraction", 1.0)) > 0.20:
        reasons.append("shared_stage_out_exceeds_20_percent")
    return not reasons, reasons


def build_report(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_rung: dict[int, list[dict[str, Any]]] = {rung: [] for rung in RUNGS}
    for run in runs:
        if run.get("schema_version") != "packed-node-run/1.0":
            raise ValueError("unsupported packed-run report")
        worker_count = int(run["worker_count"])
        if worker_count not in by_rung:
            raise ValueError(f"unexpected worker rung: {worker_count}")
        passed, reasons = assess(run)
        by_rung[worker_count].append({
            "repetition": run.get("repetition"), "passed": passed,
            "rejected_reasons": reasons, "metrics": run,
        })
    rung_rows: list[dict[str, Any]] = []
    individually_passing: set[int] = set()
    for rung in RUNGS:
        repetitions = sorted(by_rung[rung], key=lambda row: row["repetition"] or 0)
        observed_repetitions = {row["repetition"] for row in repetitions}
        missing_repetitions = sorted({1, 2, 3} - observed_repetitions)
        complete = len(repetitions) == 3 and not missing_repetitions
        passed = complete and all(row["passed"] for row in repetitions)
        reasons = [] if complete else ["requires_exactly_three_repetitions"]
        reasons.extend(sorted({reason for row in repetitions for reason in row["rejected_reasons"]}))
        rung_rows.append({
            "worker_count": rung, "passed": passed,
            "missing_repetitions": missing_repetitions,
            "rejected_reasons": reasons, "repetitions": repetitions,
        })
        if passed:
            individually_passing.add(rung)
    selected = None
    for rung in RUNGS:
        if rung not in individually_passing:
            break
        selected = rung
    for row in rung_rows:
        if row["passed"] and row["worker_count"] != selected:
            row["rejected_reasons"].append("lower_density_rung_did_not_pass")
    stage1_passed = selected is not None
    selected_runs = [row["metrics"] for row in by_rung.get(selected or -1, [])]
    throughput = (
        statistics.median(row["work_items"] / row["wall_seconds"] for row in selected_runs)
        if selected_runs else 0.0
    )
    memory = selected_runs[0].get("allocated_memory_bytes") if selected_runs else None
    scratch = selected_runs[0].get("scratch_allocation_bytes") if selected_runs else None
    return {
        "schema_version": "stage1-characterization-report/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "passed" if stage1_passed else "failed",
        "rungs": rung_rows, "selected_worker_count": selected,
        "selection_policy": "highest contiguous passing rung beginning at 8 workers",
        "recommended_pbs_resources": {
            "nodes": 1, "ncpus": selected, "memory_bytes": memory,
            "job_local_scratch_bytes": scratch, "stage_out_concurrency": 2,
            "threads_per_worker": 1,
        } if selected is not None else None,
        "projected_eight_node_throughput_shards_per_hour": throughput * 3600 * 8,
        "projection_basis": "linear eight-node projection from median selected-rung one-node throughput",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the versioned Stage 1 characterization report")
    parser.add_argument("--run", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report([json.loads(path.read_text(encoding="utf-8")) for path in args.run])
    atomic_json(args.output, report)
    print(json.dumps({key: value for key, value in report.items() if key != "rungs"}, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
