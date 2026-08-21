"""Run the 5 x 25 Phase 3.1 hidden-distribution matrix on one full node."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json
from experiments.evaluate_distribution_blind_survival import DISTRIBUTIONS, run_trial


def _single_thread() -> None:
    for name in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
    ):
        os.environ[name] = "1"


def _one(args: tuple[str, str, str, int, int, float, float, int]) -> dict[str, Any]:
    _single_thread()
    manifest, root, distribution, seed, steps, dense, sparse, buckets = args
    report_path = Path(root) / distribution / f"seed-{seed}" / "report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("schema_version") == "distribution-blind-survival-trial/1.0":
            return report
    return run_trial(
        Path(manifest), Path(root), distribution=distribution, seed=seed,
        steps=steps, dense_arrivals_per_step=dense,
        sparse_arrivals_per_step=sparse, bucket_count=buckets,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=125)
    parser.add_argument("--seed-start", type=int, default=46501)
    parser.add_argument("--seeds-per-distribution", type=int, default=25)
    parser.add_argument("--steps", type=int, default=960)
    parser.add_argument("--dense-arrivals-per-step", type=float, default=0.50)
    parser.add_argument("--sparse-arrivals-per-step", type=float, default=0.04)
    parser.add_argument("--bucket-count", type=int, default=12)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 125:
        raise ValueError("workers must be in [1, 125] on this cluster")
    seeds = range(args.seed_start, args.seed_start + args.seeds_per_distribution)
    tasks = [
        (
            str(args.manifest.resolve()), str(args.output_root.resolve()),
            distribution, seed, args.steps, args.dense_arrivals_per_step,
            args.sparse_arrivals_per_step, args.bucket_count,
        )
        for distribution in DISTRIBUTIONS
        for seed in seeds
    ]
    if any(seed == 46003 or 46201 <= seed <= 46330 for *_, seed, _steps, _dense, _sparse, _buckets in tasks):
        raise ValueError("task matrix collides with protected seeds")
    records: list[dict[str, Any]] = []
    context = mp.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers, mp_context=context,
    ) as executor:
        for index, report in enumerate(executor.map(_one, tasks), 1):
            records.append(report)
            print(f"completed {index}/{len(tasks)}", flush=True)
    by_distribution = {}
    for distribution in DISTRIBUTIONS:
        selected = [
            row for row in records
            if row["distribution_visible_to_auditor_only"] == distribution
        ]
        calibration = [
            row["calibration"]["sample_weighted_mean_absolute_error"]
            for row in selected
        ]
        refreshed = [
            row["refresh_after_drift"]["calibration"]["sample_weighted_mean_absolute_error"]
            for row in selected if row["refresh_after_drift"] is not None
        ]
        by_distribution[distribution] = {
            "trials": len(selected),
            "mean_calibration_mae": mean(calibration),
            "worst_calibration_mae": max(calibration),
            "mean_pooling_coverage": mean(row["pooling"]["coverage"] for row in selected),
            "staleness_fail_closed_fraction": mean(row["staleness"]["fail_closed"] for row in selected),
            "mean_refreshed_calibration_mae": mean(refreshed) if refreshed else None,
        }
    campaign = {
        "schema_version": "distribution-blind-survival-campaign/1.0",
        "trials": len(records),
        "workers": args.workers,
        "seeds": list(seeds),
        "protected_seeds_consumed": False,
        "fit_interface": "lifecycle-export/1.0",
        "by_distribution": by_distribution,
        "closed_loop_sensitivity": "pending_phase3.1_controller_matrix",
    }
    atomic_json(args.output_root / "CAMPAIGN.json", campaign)
    print(json.dumps(campaign, sort_keys=True))


if __name__ == "__main__":
    main()
