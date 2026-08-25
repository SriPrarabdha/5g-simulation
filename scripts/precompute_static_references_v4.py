#!/usr/bin/env python3
"""Build every Static reference the v4 discovery wave needs, exactly once.

Each of the 160 arm nodes evaluates the same 125 scenarios against the same
Static baseline.  When all of them start at once the cache is cold for all of
them, so the paired baseline gets simulated up to 160 times over.  Static is a
function of (family, seed, cadence) alone, so 250 runs cover the whole wave.
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from experiments.mixed_stress_campaign_v4 import (
    FAMILIES, atomic_checkpoint, build_family, seeds_for, static_cache_key,
)
from simulator.macro import CompositeSink, Simulator, controller_by_name, load_scenario

CADENCE_MINUTES = (10, 2)
OBSERVATION_WINDOW_SECONDS = 600


def work_items(stage: str, per_family: int | None = None,
               cadences: tuple[int, ...] = CADENCE_MINUTES) -> list[dict]:
    return [
        {"family": family, "seed": seed, "cadence_minutes": cadence}
        for family in FAMILIES
        for seed in (seeds_for(stage, family)[:per_family] if per_family
                     else seeds_for(stage, family))
        for cadence in cadences
    ]


def scenario_for(base, item: dict):
    scenario = build_family(base, item["family"], int(item["seed"]))
    cadence_steps = round(int(item["cadence_minutes"]) * 60 / scenario.step_seconds)
    observation_steps = round(OBSERVATION_WINDOW_SECONDS / scenario.step_seconds)
    observation_steps = max(cadence_steps, observation_steps - observation_steps % cadence_steps)
    return replace(scenario, decision_interval_steps=cadence_steps,
                   observation_window_steps=observation_steps)


def _run(job):
    item, manifest, static_root, slot = job
    available = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {available[slot % len(available)]})
    scenario = scenario_for(load_scenario(manifest), item)
    path = Path(static_root) / f"{static_cache_key(scenario)}.json"
    if path.exists():
        return "cached"
    simulator = Simulator(scenario, controller_by_name("static"))
    sink = simulator.make_summary_sink()
    simulator.run(CompositeSink([sink]))
    atomic_checkpoint(path, {
        "schema_version": "static-reference/4.0",
        "scenario_key": static_cache_key(scenario), "result": sink.summary,
    })
    return "built"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--static-cache", type=Path, required=True)
    parser.add_argument("--stage", default="development")
    parser.add_argument("--per-family", type=int, default=None)
    parser.add_argument("--cadences", default=",".join(str(c) for c in CADENCE_MINUTES))
    parser.add_argument("--workers", type=int, default=125)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    args = parser.parse_args()
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[variable] = "1"
    items = work_items(args.stage, args.per_family,
                       tuple(int(c) for c in args.cadences.split(",")))
    mine = [item for index, item in enumerate(items) if index % args.shard_count == args.shard_index]
    args.static_cache.mkdir(parents=True, exist_ok=True)
    jobs = [(item, str(args.manifest), str(args.static_cache), slot)
            for slot, item in enumerate(mine)]
    outcomes, failures = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run, job): job[0] for job in jobs}
        for future in as_completed(futures):
            try:
                outcomes.append(future.result())
            except Exception as error:
                failures.append({"item": futures[future],
                                 "error": f"{type(error).__name__}: {error}"})
    print(json.dumps({
        "shard": args.shard_index, "of": args.shard_count, "items": len(mine),
        "built": outcomes.count("built"), "cached": outcomes.count("cached"),
        "failed": len(failures),
    }, sort_keys=True))
    if failures:
        print(json.dumps(failures[:5], indent=2, default=str))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
