"""Immutable work construction for the guarded mixed-stress campaign, v4.

v4 fixes four defects found while auditing the v3 discovery campaign:

* ``surprise_outage`` used ``health="unavailable"``, which drives safe capacity
  to zero.  The engine scores overload as ``load / capacity - 1``, so every
  such pair returned ``inf`` for both controllers -- a tie that nonetheless
  failed the finite-metric gate on all 160 arms.  v4 uses a deep but finite
  brownout instead, so the family is a genuine tie rather than an artifact.
* ``maintenance_then_outage`` used a fixed 1% capacity factor, inflating that
  family's relative overload roughly 275x above every other family and letting
  it dominate any cross-family aggregate.  v4 randomises the depth.
* ``surprise_demand`` scaled arrivals on one of 96 groups, which never produced
  measurable overload: 0 of 25 seeds were informative.  v4 shocks a whole zone.
* Notice time was pinned at two hours, so controller lead horizons of 2, 3 and
  4 hours were indistinguishable.  v4 makes notice a scenario axis derived from
  the seed, and records it on every cell so the analysis can slice by it.

Declared-maintenance events now degrade two capacity-weighted UPFs.  In v3 a
single uniformly chosen UPF left 14 of 25 seeds with no static overload at all,
which diluted every per-pair mean by more than a factor of two.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import random

from experiments.mixed_stress_campaign import (
    PROTECTED_SEEDS,
    atomic_checkpoint,
    churn_l1_per_group_hour,
    fingerprint,
    static_cache_key,
)
from simulator.macro.config import ScenarioConfig, ScenarioEvent

__all__ = [
    "FAMILIES",
    "NOTICE_HOURS",
    "SEED_POOLS",
    "CandidateArm",
    "atomic_checkpoint",
    "build_family",
    "candidate_arm",
    "churn_l1_per_group_hour",
    "development_cells",
    "fingerprint",
    "notice_hours_for_seed",
    "seeds_for",
    "static_cache_key",
]


FAMILIES = (
    "declared_maintenance",
    "surprise_demand",
    "surprise_brownout",
    "maintenance_then_stadium",
    "maintenance_then_brownout",
)
# Notice is a property of the scenario, never of the controller under test.
NOTICE_HOURS = (0.5, 1.0, 2.0, 3.0, 4.0)
# Disjoint from the v3 pools (47000-70999) and from the protected seeds.
SEED_POOLS = {
    "development": range(80000, 80125),
    "validation": range(81000, 83500),
    "holdout_a": range(90000, 100000),
    "holdout_b": range(100000, 110000),
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
        cadence, blend, horizon, reserve, envelope = _product_at(
            index, (10, 2), (0.25, 0.35, 0.50, 0.75), (1, 2, 3, 4), (0.70, 0.80), (0.45, 0.10)
        )
        return CandidateArm(index, "predrain", cadence, horizon, blend, reserve, envelope)
    cadence, horizon, blend, reserve, envelope = _product_at(
        index - 128, (10, 2), (1, 2), (0.25, 0.50), (0.70, 0.80), (0.45, 0.10)
    )
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


def notice_hours_for_seed(seed: int) -> float:
    """Notice depends only on the seed, so every arm sees the same schedule."""
    return NOTICE_HOURS[seed % len(NOTICE_HOURS)]


def development_cells(index: int) -> list[dict[str, Any]]:
    arm = candidate_arm(index)
    return [
        {
            "arm": asdict(arm),
            "family": family,
            "seed": seed,
            "notice_hours": notice_hours_for_seed(seed),
        }
        for family in FAMILIES
        for seed in seeds_for("development", family)
    ]


def _maintenance_targets(base: ScenarioConfig, rng: random.Random) -> list[str]:
    """Pick two UPFs weighted by capacity.

    Scheduled maintenance on an idle UPF is not a stress test; operators take
    down the units that carry traffic.  Weighting by nominal capacity is a
    causal proxy for load that needs no simulation, and it lifts the fraction
    of seeds that produce any static overload at all.
    """
    ranked = sorted(base.upfs, key=lambda item: item.capacity_ul_mbps, reverse=True)
    pool = ranked[:max(2, len(ranked) // 2)]
    chosen = rng.sample(pool, k=min(2, len(pool)))
    return sorted(item.upf_id for item in chosen)


def build_family(base: ScenarioConfig, family: str, seed: int) -> ScenarioConfig:
    """Build stress independently of controller cadence and horizon."""
    if family not in FAMILIES:
        raise ValueError(f"unknown stress family: {family}")
    rng = random.Random(seed)
    hour = max(1, round(3600 / base.step_seconds))
    notice_steps = max(1, round(notice_hours_for_seed(seed) * hour))
    lower = min(max(notice_steps + 1, base.steps // 3), base.steps - 2)
    upper = max(lower + 1, min(base.steps - 1, base.steps * 2 // 3))
    start = rng.randrange(lower, upper)
    notice = max(0, start - notice_steps)
    upfs = sorted(item.upf_id for item in base.upfs)
    zones = sorted({item.key.zone for item in base.groups})
    events: list[ScenarioEvent] = []
    targets: list[str] = []
    if family in {"declared_maintenance", "maintenance_then_stadium", "maintenance_then_brownout"}:
        targets = _maintenance_targets(base, rng)
        for upf_id in targets:
            factor = rng.uniform(0.05, 0.30)
            events.append(ScenarioEvent(
                start, "capacity_factor", upf_id=upf_id,
                ul_factor=factor, dl_factor=factor, known_at_step=notice,
            ))
    if family == "surprise_demand":
        events.extend(_zone_surge(base, rng, start, zones))
    elif family == "surprise_brownout":
        # Deep enough to be an outage in practice, finite so the scored metric
        # stays comparable with every other family.
        factor = rng.uniform(0.02, 0.10)
        events.append(ScenarioEvent(
            start, "capacity_factor", upf_id=rng.choice(upfs),
            ul_factor=factor, dl_factor=factor,
        ))
    elif family == "maintenance_then_stadium":
        surprise = min(base.steps - 1, start + max(1, hour // 2))
        events.extend(_zone_surge(base, rng, surprise, zones))
    elif family == "maintenance_then_brownout":
        eligible = [item for item in upfs if item not in targets] or upfs
        surprise = min(base.steps - 1, start + max(1, hour // 2))
        factor = rng.uniform(0.02, 0.20)
        events.append(ScenarioEvent(
            surprise, "capacity_factor", upf_id=rng.choice(eligible),
            ul_factor=factor, dl_factor=factor,
        ))
    return replace(
        base,
        scenario_id=f"{base.scenario_id}:v4:{family}:{seed}",
        seed=seed,
        events=tuple(events),
    )


def _zone_surge(
    base: ScenarioConfig, rng: random.Random, step: int, zones: list[str],
) -> list[ScenarioEvent]:
    """Shock every group in one zone.

    v3 scaled arrivals on a single group out of 96 and produced no measurable
    overload in any seed; a stadium empties into a whole zone at once.
    """
    zone = rng.choice(zones)
    multiplier = rng.uniform(2.5, 4.0)
    return [
        ScenarioEvent(step, "arrival_factor", group_id=group.key.selection_id,
                      arrival_factor=multiplier)
        for group in base.groups
        if group.key.zone == zone
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--array-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cells = development_cells(args.array_index)
    atomic_checkpoint(args.output, {
        "schema_version": "mixed-stress-worklist/4.0",
        "arm": asdict(candidate_arm(args.array_index)),
        "cells": cells,
    })
    print(json.dumps({"arm": args.array_index, "cells": len(cells)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
