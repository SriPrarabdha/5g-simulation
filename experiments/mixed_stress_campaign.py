"""Immutable work construction for the guarded mixed-stress campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from simulator.macro.config import ScenarioConfig, ScenarioEvent


FAMILIES = (
    "declared_maintenance",
    "surprise_demand",
    "surprise_outage",
    "maintenance_then_stadium",
    "maintenance_then_outage",
)
PROTECTED_SEEDS = frozenset({46003, *range(46201, 46217), *range(46301, 46331)})
SEED_POOLS = {
    "development": range(47000, 47125),
    "validation": range(48000, 50500),
    "holdout_a": range(51000, 61000),
    "holdout_b": range(61000, 71000),
}


@dataclass(frozen=True, slots=True)
class CandidateArm:
    index: int
    controller: str
    cadence_minutes: int
    horizon_hours: int
    maximum_blend: float
    destination_reserve: float
    surprise_capacity_factor: float


def candidate_arm(index: int) -> CandidateArm:
    if not 0 <= index < 160:
        raise ValueError("candidate index must be in [0, 159]")
    if index < 128:
        values = _product_at(index, (10, 2), (0.25, 0.35, 0.50, 0.75), (1, 2, 3, 4), (0.70, 0.80), (0.45, 0.01))
        cadence, blend, horizon, reserve, envelope = values
        return CandidateArm(index, "predrain", cadence, horizon, blend, reserve, envelope)
    cadence, horizon, blend, reserve, envelope = _product_at(index - 128, (10, 2), (1, 2), (0.25, 0.50), (0.70, 0.80), (0.45, 0.01))
    return CandidateArm(index, "mpc", cadence, horizon, blend, reserve, envelope)


def _product_at(index: int, *axes: tuple[Any, ...]) -> tuple[Any, ...]:
    values = []
    for axis in reversed(axes):
        values.append(axis[index % len(axis)])
        index //= len(axis)
    return tuple(reversed(values))


def seeds_for(stage: str, family: str) -> tuple[int, ...]:
    if family not in FAMILIES or stage not in SEED_POOLS:
        raise ValueError("unknown campaign stage or stress family")
    pool = tuple(SEED_POOLS[stage])
    count = len(pool) // len(FAMILIES)
    offset = FAMILIES.index(family) * count
    result = pool[offset:offset + count]
    if PROTECTED_SEEDS.intersection(result):
        raise RuntimeError("campaign pool overlaps protected seeds")
    return result


def development_cells(index: int) -> list[dict[str, Any]]:
    arm = candidate_arm(index)
    return [
        {"arm": asdict(arm), "family": family, "seed": seed}
        for family in FAMILIES
        for seed in seeds_for("development", family)
    ]


def build_family(base: ScenarioConfig, family: str, seed: int) -> ScenarioConfig:
    """Build stress independently of controller cadence/horizon."""
    if family not in FAMILIES:
        raise ValueError(f"unknown stress family: {family}")
    rng = random.Random(seed)
    hour = max(1, round(3600 / base.step_seconds))
    lower = min(max(1, base.steps // 3), base.steps - 2)
    upper = max(lower + 1, min(base.steps - 1, base.steps * 2 // 3))
    start = rng.randrange(lower, upper)
    notice = max(0, start - 2 * hour)  # scenario property: always two hours
    upfs = sorted(item.upf_id for item in base.upfs)
    groups = sorted(item.key.selection_id for item in base.groups)
    target = rng.choice(upfs)
    events: list[ScenarioEvent] = []
    if family in {"declared_maintenance", "maintenance_then_stadium", "maintenance_then_outage"}:
        factor = rng.uniform(0.08, 0.35)
        events.append(ScenarioEvent(start, "capacity_factor", upf_id=target, ul_factor=factor, dl_factor=factor, known_at_step=notice))
    if family == "surprise_demand":
        events.append(ScenarioEvent(start, "arrival_factor", group_id=rng.choice(groups), arrival_factor=3.2))
    elif family == "surprise_outage":
        events.append(ScenarioEvent(start, "health", upf_id=target, health="unavailable"))
    elif family == "maintenance_then_stadium":
        surprise = min(base.steps - 1, start + max(1, hour // 2))
        events.append(ScenarioEvent(surprise, "arrival_factor", group_id=rng.choice(groups), arrival_factor=3.2))
    elif family == "maintenance_then_outage":
        eligible = [item for item in upfs if item != target] or upfs
        surprise = min(base.steps - 1, start + max(1, hour // 2))
        events.append(ScenarioEvent(surprise, "capacity_factor", upf_id=rng.choice(eligible), ul_factor=0.01, dl_factor=0.01))
    return replace(base, scenario_id=f"{base.scenario_id}:{family}:{seed}", seed=seed, events=tuple(events))


def fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def atomic_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    """Publish one immutable seed result; mismatched retries fail closed."""
    payload = dict(payload)
    payload.setdefault("work_fingerprint", fingerprint({key: value for key, value in payload.items() if key != "result"}))
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != payload:
            raise FileExistsError(f"checkpoint fingerprint/content mismatch: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def static_cache_key(scenario: ScenarioConfig) -> str:
    payload = asdict(scenario)
    payload.pop("seed", None)
    # Observation history cannot affect Static, but decision cadence can: a
    # realized capacity event is incorporated at the next decision boundary.
    # Therefore cadence is part of the immutable Static reference identity.
    payload.pop("observation_window_steps", None)
    return fingerprint({"scenario": payload, "seed": scenario.seed, "controller": "static-capacity-v1"})


def churn_l1_per_group_hour(total_l1: float, *, groups: int, steps: int,
                            step_seconds: int) -> float:
    if groups <= 0 or steps <= 0 or step_seconds <= 0:
        raise ValueError("churn normalization dimensions must be positive")
    hours = steps * step_seconds / 3600
    return total_l1 / groups / hours


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--array-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cells = development_cells(args.array_index)
    atomic_checkpoint(args.output, {"schema_version": "mixed-stress-worklist/1.0", "arm": asdict(candidate_arm(args.array_index)), "cells": cells})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
