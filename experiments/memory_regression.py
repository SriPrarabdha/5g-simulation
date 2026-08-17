from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

from .artifacts import ArtifactPolicy, atomic_json


def _run_child(manifest: str, output: str, scratch: str, campaign: str, result: Any) -> None:
    from .run_campaign_shard import run_shard
    try:
        destination = run_shard(
            Path(manifest), Path(output), campaign, 95001,
            artifact_policy=ArtifactPolicy(silver_percentage=0),
            scratch_root=Path(scratch), progress_every_simulated_hours=None,
        )
        metadata = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
        result.put({
            "status": "complete", "manifest": manifest,
            "steps": metadata["step_count"], "peak_rss_bytes": metadata["peak_rss_bytes"],
            "scratch_bytes": metadata["scratch_bytes"], "summary": metadata["summary"],
        })
    except BaseException as error:
        result.put({"status": "failed", "error": f"{type(error).__name__}: {error}"})


def run_memory_regression(
    one_day: Path, seven_day: Path, output_root: Path, scratch_root: Path,
    *, max_growth_fraction: float = 0.20,
) -> dict[str, Any]:
    context = mp.get_context("spawn")
    rows = []
    for label, manifest in (("one_day", one_day), ("seven_day", seven_day)):
        result = context.Queue()
        process = context.Process(
            target=_run_child,
            args=(str(manifest), str(output_root), str(scratch_root / label), f"memory-{label}", result),
        )
        process.start()
        process.join()
        row = result.get()
        row["label"] = label
        row["exit_code"] = process.exitcode
        rows.append(row)
    if any(row["status"] != "complete" for row in rows):
        passed = False
        growth = None
    else:
        growth = rows[1]["peak_rss_bytes"] / max(1, rows[0]["peak_rss_bytes"]) - 1.0
        passed = growth <= max_growth_fraction
    return {
        "schema_version": "stage1-memory-regression/1.0", "runs": rows,
        "max_peak_rss_growth_fraction": max_growth_fraction,
        "observed_peak_rss_growth_fraction": growth,
        "passed": passed,
        "criterion": "7-day Bronze peak RSS remains within the configured steady-state tolerance of 1-day Bronze",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated 1-day/7-day Bronze memory regression")
    parser.add_argument("--one-day-manifest", required=True, type=Path)
    parser.add_argument("--seven-day-manifest", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--scratch-root", required=True, type=Path)
    parser.add_argument("--max-growth-fraction", type=float, default=0.20)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = run_memory_regression(
        args.one_day_manifest, args.seven_day_manifest, args.output_root,
        args.scratch_root, max_growth_fraction=args.max_growth_fraction,
    )
    atomic_json(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
