from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import queue
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .artifacts import ArtifactPolicy, atomic_json, topology_identity


@dataclass(frozen=True, slots=True)
class WorkItem:
    topology_id: str
    scenario_manifest: str
    seed: int
    controller: str
    model: str | None
    optimizer_profile: str | None
    artifact_policy: dict[str, Any]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkItem":
        payload = dict(value)
        schema = payload.pop("schema_version", "stage1-work-item/1.0")
        if schema != "stage1-work-item/1.0":
            raise ValueError("unsupported work-item schema")
        item = cls(**payload)
        ArtifactPolicy.from_dict(item.artifact_policy)
        if item.controller not in {"static", "reactive", "forecast-capacity", "predictive", "mpc", "oracle"}:
            raise ValueError(f"unsupported work-item controller: {item.controller}")
        return item

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "stage1-work-item/1.0", **asdict(self)}


def _set_single_thread_environment() -> None:
    for name in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
    ):
        os.environ[name] = "1"


def _worker(
    worker_id: int,
    cpu: int,
    work_queue: Any,
    result_queue: Any,
    stop_event: Any,
    stage_out_semaphore: Any,
    output_root: str,
    campaign_id: str,
    scratch_root: str,
) -> None:
    _set_single_thread_environment()
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {cpu})
    signal.signal(signal.SIGTERM, lambda _number, _frame: stop_event.set())
    signal.signal(signal.SIGINT, lambda _number, _frame: stop_event.set())
    from .run_campaign_shard import run_shard

    while not stop_event.is_set():
        item = work_queue.get()
        if item is None:
            return
        work = WorkItem.from_dict(item)
        started = time.monotonic()
        try:
            manifest = Path(work.scenario_manifest)
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            if topology_identity(manifest_payload) != work.topology_id:
                raise ValueError("work-item topology_id does not match its manifest")
            profile = Path(work.optimizer_profile) if work.optimizer_profile else None
            destination = run_shard(
                manifest, Path(output_root), campaign_id, work.seed,
                controller=work.controller,
                forecast_bundle=Path(work.model) if work.model else None,
                predictive_profile=profile if work.controller in {"predictive", "forecast-capacity"} else None,
                mpc_profile=profile if work.controller == "mpc" else None,
                artifact_policy=ArtifactPolicy.from_dict(work.artifact_policy),
                scratch_root=Path(scratch_root) / f"worker-{worker_id:03d}",
                stage_out_semaphore=stage_out_semaphore, stop_event=stop_event,
                progress_every_simulated_hours=None,
            )
            metadata = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
            result_queue.put({
                "status": "complete", "worker_id": worker_id, "cpu": cpu,
                "work_item": work.to_dict(), "destination": str(destination),
                "wall_seconds": time.monotonic() - started,
                "peak_rss_bytes": metadata["peak_rss_bytes"],
                "cpu_seconds": metadata["cpu_seconds"],
                "scratch_bytes": metadata["scratch_bytes"],
                "stage_out_seconds": metadata["stage_out_seconds"],
                "artifacts": metadata["artifacts"],
            })
        except BaseException as error:
            result_queue.put({
                "status": "incomplete" if isinstance(error, InterruptedError) else "failed",
                "worker_id": worker_id, "cpu": cpu, "work_item": work.to_dict(),
                "wall_seconds": time.monotonic() - started,
                "error": f"{type(error).__name__}: {error}",
            })
            if not isinstance(error, InterruptedError):
                # Stage 1 records the failure and continues with already queued
                # independent shards; it never retries a failed work item.
                continue
            return


def _proc_metrics(pid: int) -> tuple[int, int, int]:
    rss = swap = faults = 0
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
            elif line.startswith("VmSwap:"):
                swap = int(line.split()[1]) * 1024
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        faults = int(fields[11])
    except (FileNotFoundError, ProcessLookupError, IndexError, ValueError):
        pass
    return rss, swap, faults


def run_pool(
    items: list[WorkItem], *, worker_count: int, output_root: Path,
    campaign_id: str, scratch_root: Path, report_path: Path | None = None,
    repetition: int | None = None, allocated_memory_bytes: int | None = None,
    scratch_allocation_bytes: int | None = None,
) -> dict[str, Any]:
    if worker_count not in {8, 16, 32, 64}:
        raise ValueError("Stage 1 packing uses exactly 8, 16, 32, or 64 workers")
    available = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else list(range(os.cpu_count() or 1))
    if len(available) < worker_count:
        raise RuntimeError(f"allocation exposes {len(available)} CPUs, fewer than {worker_count} workers")
    if not items:
        raise ValueError("work list is empty")
    scratch_root.mkdir(parents=True, exist_ok=True)
    context = mp.get_context("spawn")
    work_queue = context.Queue()
    result_queue = context.Queue()
    stop_event = context.Event()
    stage_out_semaphore = context.Semaphore(2)
    for item in items:
        work_queue.put(item.to_dict())
    for _ in range(worker_count):
        work_queue.put(None)
    processes = [
        context.Process(
            target=_worker,
            args=(
                index, available[index], work_queue, result_queue, stop_event,
                stage_out_semaphore, str(output_root), campaign_id, str(scratch_root),
            ),
            name=f"stage1-worker-{index:03d}",
        ) for index in range(worker_count)
    ]
    previous_handlers = {
        number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)
    }
    for number in previous_handlers:
        signal.signal(number, lambda _number, _frame: stop_event.set())
    started = time.monotonic()
    for process in processes:
        process.start()
    results: list[dict[str, Any]] = []
    aggregate_peak_rss = peak_swap = scratch_peak = 0
    per_worker_peak: dict[int, int] = {index: 0 for index in range(worker_count)}
    per_worker_scratch_peak: dict[int, int] = {index: 0 for index in range(worker_count)}
    major_faults: dict[int, int] = {index: 0 for index in range(worker_count)}
    try:
        while len(results) < len(items):
            try:
                completed = result_queue.get(timeout=0.25)
                results.append(completed)
                worker_id = int(completed["worker_id"])
                per_worker_scratch_peak[worker_id] = max(
                    per_worker_scratch_peak[worker_id], int(completed.get("scratch_bytes", 0))
                )
                scratch_peak = max(scratch_peak, sum(per_worker_scratch_peak.values()))
            except queue.Empty:
                pass
            current_rss = 0
            for index, process in enumerate(processes):
                if process.pid is None:
                    continue
                rss, swap, faults = _proc_metrics(process.pid)
                current_rss += rss
                peak_swap = max(peak_swap, swap)
                per_worker_peak[index] = max(per_worker_peak[index], rss)
                major_faults[index] = max(major_faults[index], faults)
            aggregate_peak_rss = max(aggregate_peak_rss, current_rss)
            scratch_peak = max(
                scratch_peak,
                sum(path.stat().st_size for path in scratch_root.rglob("*") if path.is_file()),
            )
            if all(not process.is_alive() for process in processes) and len(results) < len(items):
                break
    finally:
        for process in processes:
            process.join(timeout=30)
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)
    wall = time.monotonic() - started
    cpu_seconds = sum(float(item.get("cpu_seconds", 0.0)) for item in results)
    artifact_bytes = sum(
        int(artifact["bytes"]) for item in results for artifact in item.get("artifacts", [])
    )
    total_scratch_bytes = sum(int(item.get("scratch_bytes", 0)) for item in results)
    total_stage_out_seconds = sum(float(item.get("stage_out_seconds", 0)) for item in results)
    report = {
        "schema_version": "packed-node-run/1.0", "campaign_id": campaign_id,
        "repetition": repetition,
        "worker_count": worker_count, "work_items": len(items), "wall_seconds": wall,
        "cpu_seconds": cpu_seconds,
        "cpu_efficiency": cpu_seconds / (wall * worker_count) if wall else 0.0,
        "aggregate_peak_rss_bytes": aggregate_peak_rss,
        "per_worker_peak_rss_bytes": per_worker_peak,
        "major_faults": major_faults, "peak_swap_bytes": peak_swap,
        "scratch_peak_bytes": scratch_peak,
        "allocated_memory_bytes": allocated_memory_bytes,
        "scratch_allocation_bytes": scratch_allocation_bytes,
        "scratch_write_throughput_bytes_per_second": total_scratch_bytes / wall if wall else 0.0,
        "artifact_bytes": artifact_bytes,
        "stage_out_seconds": total_stage_out_seconds,
        "shared_stage_out_throughput_bytes_per_second": (
            artifact_bytes / (total_stage_out_seconds / 2) if total_stage_out_seconds else 0.0
        ),
        "stage_out_wall_fraction": (
            total_stage_out_seconds / 2 / wall if wall else 0.0
        ),
        "failures": sum(item["status"] != "complete" for item in results),
        "exit_status": {process.name: process.exitcode for process in processes},
        "results": results,
    }
    if report_path is not None:
        atomic_json(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Stage 1 work items in a whole-node process pool")
    parser.add_argument("--work-list", required=True, type=Path)
    parser.add_argument("--workers", required=True, type=int, choices=(8, 16, 32, 64))
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--scratch-root", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--repetition", type=int)
    parser.add_argument("--allocated-memory-bytes", type=int)
    parser.add_argument("--scratch-allocation-bytes", type=int)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = json.loads(args.work_list.read_text(encoding="utf-8"))
    raw_items = payload.get("work_items", payload) if isinstance(payload, dict) else payload
    items = [WorkItem.from_dict(item) for item in raw_items]
    scratch = args.scratch_root
    if scratch is None:
        value = os.environ.get("PBS_JOBFS") or os.environ.get("TMPDIR")
        if not value:
            raise RuntimeError("packed execution requires PBS_JOBFS, TMPDIR, or --scratch-root")
        scratch = Path(value)
    report = run_pool(
        items, worker_count=args.workers, output_root=args.output_root,
        campaign_id=args.campaign_id, scratch_root=scratch, report_path=args.report,
        repetition=args.repetition, allocated_memory_bytes=args.allocated_memory_bytes,
        scratch_allocation_bytes=args.scratch_allocation_bytes,
    )
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2, sort_keys=True))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
