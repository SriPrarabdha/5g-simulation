"""Dedicated, claim-before-use runner for untouched MPC release seeds."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing as mp
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json
from experiments.evaluate_cohort_mpc_pilot import SCENARIO_KINDS
from experiments.evaluate_control_science_release import evaluate_release
from forecasting import load_forecaster_bundle
from optimization import CohortMPCConfig
from scripts.run_mpc_candidate_parallel import (
    UNTOUCHED_RELEASE_SEEDS,
    _artifact_sha256,
    _canonical_sha256,
    _evaluate_pair,
    _sha256,
    aggregate,
)
from simulator.macro import load_scenario


def release_tasks() -> list[tuple[str, int]]:
    seeds = sorted(UNTOUCHED_RELEASE_SEEDS)
    tasks: list[tuple[str, int]] = []
    base, remainder = divmod(len(seeds), len(SCENARIO_KINDS))
    cursor = 0
    for index, kind in enumerate(SCENARIO_KINDS):
        count = base + (1 if index < remainder else 0)
        tasks.extend((kind, seed) for seed in seeds[cursor:cursor + count])
        cursor += count
    return tasks


def validate_release_inputs(
    freeze: dict[str, Any], validation: dict[str, Any], *, manifest: Path,
    profile: Path, forecaster: Path, survival: Path,
) -> None:
    if freeze.get("schema_version") != "mpc-release-freeze/1.0":
        raise ValueError("release execution requires the final MPC release freeze")
    if not freeze.get("frozen", False):
        raise ValueError("release freeze is not sealed")
    expected = freeze.get("artifacts", {})
    actual = {
        "manifest": _sha256(manifest),
        "mpc_profile": _sha256(profile),
        "forecast_bundle": _artifact_sha256(forecaster),
        "survival_bundle": _sha256(survival),
    }
    if any(expected.get(key, {}).get("sha256") != value for key, value in actual.items()):
        raise ValueError("release inputs do not match the frozen validated candidate")
    if validation.get("evaluation_stage") != "validation":
        raise ValueError("release requires a frozen validation-stage evaluation")
    if not validation.get("passes_day5_development_gate", False):
        raise ValueError("the frozen candidate did not pass validation gates")
    validation_hash = freeze.get("validation_evaluation_sha256")
    if not validation_hash:
        raise ValueError("release freeze lacks validation evidence")


def main() -> int:
    parser = argparse.ArgumentParser(description="One-shot untouched release execution")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--mpc-profile", required=True, type=Path)
    parser.add_argument("--forecast-bundle", required=True, type=Path)
    parser.add_argument("--survival-bundle", required=True, type=Path)
    parser.add_argument("--release-freeze", required=True, type=Path)
    parser.add_argument("--validation-evaluation", required=True, type=Path)
    parser.add_argument("--steps", type=int, default=2880)
    parser.add_argument("--workers", required=True, type=int)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    freeze = json.loads(args.release_freeze.read_text(encoding="utf-8"))
    validation = json.loads(args.validation_evaluation.read_text(encoding="utf-8"))
    validate_release_inputs(
        freeze, validation, manifest=args.manifest, profile=args.mpc_profile,
        forecaster=args.forecast_bundle, survival=args.survival_bundle,
    )
    if freeze["validation_evaluation_sha256"] != _sha256(args.validation_evaluation):
        raise ValueError("validation evidence hash does not match the release freeze")
    tasks = release_tasks()
    if {seed for _, seed in tasks} != UNTOUCHED_RELEASE_SEEDS or len(tasks) != 30:
        raise AssertionError("internal release split does not cover seeds 46301-46330 exactly once")
    profile_payload = json.loads(args.mpc_profile.read_text(encoding="utf-8"))
    settings = CohortMPCConfig(**profile_payload.get("mpc", {}))
    base = load_scenario(args.manifest)
    if args.steps * base.step_seconds < 86_400:
        parser.error("release pairs must cover at least one simulated day")
    forecaster = load_forecaster_bundle(args.forecast_bundle)
    forecaster.validate_groups(group.key for group in base.groups)
    fingerprint = _canonical_sha256({
        "release_freeze_sha256": _sha256(args.release_freeze),
        "validation_sha256": _sha256(args.validation_evaluation),
        "tasks": tasks, "steps": args.steps,
        "runner_sha256": _sha256(Path(__file__)),
    })
    args.output_root.mkdir(parents=True, exist_ok=True)
    claim = args.output_root / "release-claim.json"
    claim_payload = {
        "schema_version": "mpc-release-claim/1.0",
        "claimed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "work_fingerprint": fingerprint,
        "release_seeds": sorted(UNTOUCHED_RELEASE_SEEDS),
        "release_freeze_sha256": _sha256(args.release_freeze),
    }
    try:
        with claim.open("x", encoding="utf-8") as stream:
            json.dump(claim_payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError:
        existing = json.loads(claim.read_text(encoding="utf-8"))
        if existing.get("work_fingerprint") != fingerprint:
            raise ValueError("release seeds were already claimed by different work")
    pair_root = args.output_root / "pairs"
    pair_root.mkdir(parents=True, exist_ok=True)
    completed: dict[tuple[str, int], dict[str, Any]] = {}
    pending = []
    for kind, seed in tasks:
        path = pair_root / f"pair-{kind}-{seed:06d}.json"
        if path.exists():
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("work_fingerprint") != fingerprint:
                raise ValueError(f"release checkpoint fingerprint mismatch: {path}")
            completed[(kind, seed)] = row
        else:
            pending.append((kind, seed, path))
    for variable in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
    ):
        os.environ[variable] = "1"
    context = mp.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers, mp_context=context
    ) as pool:
        futures = {
            pool.submit(
                _evaluate_pair, str(args.manifest), str(args.mpc_profile),
                str(args.forecast_bundle), kind, seed, args.steps, fingerprint,
                str(args.survival_bundle),
            ): (kind, seed, path)
            for kind, seed, path in pending
        }
        for future in concurrent.futures.as_completed(futures):
            kind, seed, path = futures[future]
            row = future.result()
            atomic_json(path, row)
            completed[(kind, seed)] = row
    records = [completed[(kind, seed)] for kind, seed in tasks]
    aggregate_result = aggregate(
        records, manifest=args.manifest, profile_path=args.mpc_profile,
        bundle_path=args.forecast_bundle, steps=args.steps, settings=settings,
        evaluation_stage="release",
    )
    aggregate_result["release_claim_sha256"] = _sha256(claim)
    aggregate_result["release_freeze_sha256"] = _sha256(args.release_freeze)
    paired_output = args.output_root / "release-pairs.json"
    atomic_json(paired_output, aggregate_result)
    strict = evaluate_release(aggregate_result)
    strict["release_pairs_sha256"] = _sha256(paired_output)
    strict_output = args.output_root / "release-evaluation.json"
    atomic_json(strict_output, strict)
    print(json.dumps({
        "promoted": strict["promoted"], "pairs": len(records),
        "output": str(strict_output.resolve()),
    }, sort_keys=True))
    return 0 if strict["promoted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
