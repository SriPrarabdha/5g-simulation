"""Auditor-separated holding-time trials for Phase 3.1 survival estimation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json
from experiments.fit_survival_from_lifecycle import fit_lifecycle_file
from optimization import SurvivalTable, load_survival_tables
from simulator.macro import Simulator, load_scenario
from simulator.macro.config import GroupProfile


DISTRIBUTIONS = ("uniform", "weibull", "lognormal", "heavy-tail-mixture", "drift")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clamp(value: float, minimum: int = 4, maximum: int = 480) -> int:
    return min(maximum, max(minimum, int(round(value))))


def _draw(regime: str, rng: random.Random) -> int:
    if regime == "uniform":
        return rng.randint(12, 240)
    if regime == "weibull":
        return _clamp(82.0 * (-math.log(max(1e-15, 1.0 - rng.random()))) ** (1 / 1.35))
    if regime == "lognormal":
        return _clamp(rng.lognormvariate(math.log(70.0), 0.75))
    if regime == "heavy-tail-mixture":
        if rng.random() < 0.84:
            return _clamp(rng.lognormvariate(math.log(58.0), 0.52))
        return _clamp(95.0 * rng.paretovariate(1.35))
    raise ValueError(f"unsupported hidden distribution: {regime}")


def _truth_tables(
    samples: dict[str, Counter[int]], *, bucket_steps: int, bucket_count: int, generated_at,
) -> dict[str, SurvivalTable]:
    result: dict[str, SurvivalTable] = {}
    for group_id, counts in samples.items():
        total = sum(counts.values())
        if not total:
            continue
        probabilities = tuple(
            sum(count for duration, count in counts.items() if duration > lag * bucket_steps) / total
            for lag in range(bucket_count)
        )
        result[group_id] = SurvivalTable(
            probabilities, "auditor-hidden-generator-truth", generated_at,
            total, "oracle", False, False,
        )
    return result


def _calibration(
    candidate: dict[str, SurvivalTable], truth: dict[str, SurvivalTable], *, bucket_count: int,
) -> dict[str, Any]:
    rows = []
    absolute_sum = 0.0
    exposure_truth = 0.0
    exposure_error = 0.0
    for group_id in sorted(set(candidate) & set(truth)):
        estimate = candidate[group_id].values(bucket_count)
        actual = truth[group_id].values(bucket_count)
        errors = [abs(float(left) - float(right)) for left, right in zip(estimate, actual)]
        weight = truth[group_id].sample_count
        absolute_sum += weight * sum(errors)
        exposure_error += weight * sum(errors)
        exposure_truth += weight * sum(float(value) for value in actual)
        rows.append({
            "group_id": group_id,
            "truth_samples": weight,
            "source": candidate[group_id].source,
            "mean_absolute_error": sum(errors) / len(errors),
            "max_absolute_error": max(errors),
        })
    denominator = sum(item["truth_samples"] for item in rows) * bucket_count
    return {
        "groups_compared": len(rows),
        "sample_weighted_mean_absolute_error": absolute_sum / denominator if denominator else None,
        "max_group_horizon_absolute_error": max(
            (item["max_absolute_error"] for item in rows), default=None
        ),
        "load_exposure_relative_absolute_error": (
            exposure_error / exposure_truth if exposure_truth else None
        ),
        "by_group": rows,
    }


def run_trial(
    manifest: Path,
    output_root: Path,
    *,
    distribution: str,
    seed: int,
    steps: int = 960,
    dense_arrivals_per_step: float = 0.50,
    sparse_arrivals_per_step: float = 0.04,
    bucket_count: int = 12,
) -> dict[str, Any]:
    if distribution not in DISTRIBUTIONS:
        raise ValueError(f"unsupported distribution: {distribution}")
    if 46201 <= seed <= 46330 or seed == 46003:
        raise ValueError("protected validation/release/forecast seed cannot be used")
    base = load_scenario(manifest)
    groups = tuple(
        replace(
            group,
            arrivals_per_step=(
                sparse_arrivals_per_step if index % 4 == 0 else dense_arrivals_per_step
            ),
        )
        for index, group in enumerate(base.groups)
    )
    config = replace(
        base,
        scenario_id=f"phase3.1-survival-{distribution}-s{seed}",
        seed=seed,
        steps=steps,
        groups=groups,
        events=(),
        traffic_model=None,
        selection_audit_stride=max(1, steps * 1000),
    )
    drift_step = steps // 2
    samples_by_period: dict[str, dict[str, Counter[int]]] = {
        "pre": defaultdict(Counter), "post": defaultdict(Counter), "all": defaultdict(Counter),
    }

    def sampler(group: GroupProfile, step: int, rng: random.Random) -> int:
        regime = (
            "weibull" if distribution == "drift" and step < drift_step
            else "heavy-tail-mixture" if distribution == "drift"
            else distribution
        )
        duration = _draw(regime, rng)
        period = "pre" if step < drift_step else "post"
        samples_by_period[period][group.key.selection_id][duration] += 1
        samples_by_period["all"][group.key.selection_id][duration] += 1
        return duration

    trial_root = output_root / distribution / f"seed-{seed}"
    trial_root.mkdir(parents=True, exist_ok=True)
    simulator = Simulator(
        config,
        capture_session_lifecycle=True,
        lifetime_sampler=sampler,
    )
    outcome = simulator.run(simulator.make_summary_sink())
    fit_cutoff = drift_step - 1 if distribution == "drift" else 3 * steps // 4 - 1
    telemetry = trial_root / "lifecycle-fit.jsonl.gz"
    simulator.write_session_lifecycle_jsonl(
        telemetry, observed_through_step=fit_cutoff
    )
    generated_at = config.start_time + timedelta(seconds=(fit_cutoff + 1) * config.step_seconds)
    survival = trial_root / "survival.json"
    fit_report = fit_lifecycle_file(
        telemetry,
        survival,
        bucket_steps=config.decision_interval_steps,
        bucket_count=bucket_count,
        minimum_group_samples=100,
        generated_at=generated_at,
        now=generated_at,
    )
    stale_survival = trial_root / "survival-stale.json"
    stale_report = fit_lifecycle_file(
        telemetry,
        stale_survival,
        bucket_steps=config.decision_interval_steps,
        bucket_count=bucket_count,
        minimum_group_samples=100,
        generated_at=generated_at,
        now=generated_at + timedelta(days=7),
    )
    candidates = load_survival_tables(str(survival))
    truth_period = "post" if distribution == "drift" else "all"
    truth = _truth_tables(
        samples_by_period[truth_period],
        bucket_steps=config.decision_interval_steps,
        bucket_count=bucket_count,
        generated_at=generated_at,
    )
    calibration = _calibration(candidates, truth, bucket_count=bucket_count)
    refresh: dict[str, Any] | None = None
    if distribution == "drift":
        refresh_cutoff = 7 * steps // 8 - 1
        refresh_telemetry = trial_root / "lifecycle-refresh.jsonl.gz"
        simulator.write_session_lifecycle_jsonl(
            refresh_telemetry, observed_through_step=refresh_cutoff
        )
        refresh_generated_at = config.start_time + timedelta(
            seconds=(refresh_cutoff + 1) * config.step_seconds
        )
        refresh_survival = trial_root / "survival-refreshed.json"
        refresh_fit = fit_lifecycle_file(
            refresh_telemetry,
            refresh_survival,
            bucket_steps=config.decision_interval_steps,
            bucket_count=bucket_count,
            minimum_group_samples=100,
            generated_at=refresh_generated_at,
            now=refresh_generated_at,
        )
        refresh = {
            "fit": refresh_fit,
            "calibration": _calibration(
                load_survival_tables(str(refresh_survival)), truth,
                bucket_count=bucket_count,
            ),
            "telemetry_sha256": _sha256(refresh_telemetry),
            "survival_sha256": _sha256(refresh_survival),
        }
    report = {
        "schema_version": "distribution-blind-survival-trial/1.0",
        "distribution_visible_to_auditor_only": distribution,
        "seed": seed,
        "protected_seeds_consumed": False,
        "scenario": {
            "steps": steps,
            "groups": len(groups),
            "dense_arrivals_per_step": dense_arrivals_per_step,
            "sparse_arrivals_per_step": sparse_arrivals_per_step,
            "fit_cutoff": fit_cutoff,
            "decision_interval_steps": config.decision_interval_steps,
        },
        "fit": fit_report,
        "calibration": calibration,
        "staleness": {
            "age_days": 7,
            "stale_groups": stale_report["stale_groups"],
            "groups": stale_report["groups"],
            "fail_closed": stale_report["stale_groups"] == stale_report["groups"],
        },
        "pooling": {
            "coverage": fit_report["pooling_coverage"],
            "source_counts": fit_report["source_counts"],
        },
        "refresh_after_drift": refresh,
        "closed_loop_sensitivity": "measured_in_phase3.1_paired_controller_campaign",
        "simulation": {
            "completed": outcome.completed,
            "steps": outcome.step_count,
            "establishment_failures": outcome.summary["establishment_failures"],
        },
        "artifacts": {
            "telemetry": str(telemetry.resolve()),
            "telemetry_sha256": _sha256(telemetry),
            "survival": str(survival.resolve()),
            "survival_sha256": _sha256(survival),
            "stale_survival_sha256": _sha256(stale_survival),
        },
    }
    atomic_json(trial_root / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--distribution", choices=DISTRIBUTIONS, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--steps", type=int, default=960)
    parser.add_argument("--dense-arrivals-per-step", type=float, default=0.50)
    parser.add_argument("--sparse-arrivals-per-step", type=float, default=0.04)
    parser.add_argument("--bucket-count", type=int, default=12)
    args = parser.parse_args()
    result = run_trial(
        args.manifest,
        args.output_root,
        distribution=args.distribution,
        seed=args.seed,
        steps=args.steps,
        dense_arrivals_per_step=args.dense_arrivals_per_step,
        sparse_arrivals_per_step=args.sparse_arrivals_per_step,
        bucket_count=args.bucket_count,
    )
    print(json.dumps({
        "distribution": args.distribution,
        "seed": args.seed,
        "calibration": result["calibration"]["sample_weighted_mean_absolute_error"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
