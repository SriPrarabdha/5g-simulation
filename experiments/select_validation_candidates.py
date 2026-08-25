"""Freeze discovery candidates and emit fresh-seed validation worklists.

The selection rule below is pre-registered: it is applied mechanically to
whatever discovery produces, and it is deliberately NOT ranked on headline
gain.  Discovery arms were scored on the seeds that also chose them, so their
point estimates are optimistically biased; the bootstrap lower bound is the
conservative statistic and is what ranks candidates here.

Validation seeds come from a pool disjoint from discovery and from every
protected seed, and are never inspected before the candidates are frozen.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from experiments.mixed_stress_campaign_v4 import (
    FAMILIES, atomic_checkpoint, candidate_arm, notice_hours_for_seed, seeds_for,
)

# Pre-registered, recorded before the full 160-arm analysis existed.
RULE = {
    "must_be_valid": True,
    "must_pass_all_performance_gates": True,
    "rank_by": "pooled_informative_bootstrap_lower",
    "max_predrain": 2,
    "max_mpc": 2,
    "note": "Ranked on the conservative lower bound, not the point estimate, "
            "because discovery arms were selected on the seeds that scored them.",
}


def select(analysis: dict, max_predrain: int, max_mpc: int) -> list[dict]:
    eligible = [a for a in analysis["arms"] if a["valid"] and a["passed"]]
    eligible.sort(key=lambda a: a["pooled_informative_bootstrap_lower"], reverse=True)
    chosen, counts = [], {"predrain": 0, "mpc": 0}
    limits = {"predrain": max_predrain, "mpc": max_mpc}
    for arm in eligible:
        kind = arm["arm"]["controller"]
        if counts[kind] < limits[kind]:
            counts[kind] += 1
            chosen.append(arm)
    return chosen


def validation_cells(index: int, per_family: int) -> list[dict]:
    arm = candidate_arm(index)
    return [
        {"arm": asdict(arm), "family": family, "seed": seed,
         "notice_hours": notice_hours_for_seed(seed)}
        for family in FAMILIES
        for seed in seeds_for("validation", family)[:per_family]
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--per-family", type=int, default=250)
    parser.add_argument("--cells-per-node", type=int, default=125)
    parser.add_argument("--max-predrain", type=int, default=2)
    parser.add_argument("--max-mpc", type=int, default=2)
    args = parser.parse_args()

    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    chosen = select(analysis, args.max_predrain, args.max_mpc)
    if not chosen:
        print("no discovery arm passed every gate; nothing to validate")
        return 2

    root = args.campaign_root / "validation"
    shards, manifest = [], []
    for arm in chosen:
        index = int(arm["arm"]["index"])
        cells = validation_cells(index, args.per_family)
        # Every shard of a candidate writes into ONE pairs/ directory, so the
        # analysis sees a family-balanced set of 1250 pairs per candidate.
        # Cells are dealt round-robin rather than sliced, so a shard that dies
        # costs a little of every family instead of all of one.
        buckets = [cells[i::(len(cells) // args.cells_per_node)]
                   for i in range(len(cells) // args.cells_per_node)]
        output_root = root / f"arm-{index:03d}"
        for position, bucket in enumerate(buckets):
            shard_dir = output_root / f"shard-{position:02d}"
            shard_dir.mkdir(parents=True, exist_ok=True)
            atomic_checkpoint(shard_dir / "worklist.json", {
                "schema_version": "mixed-stress-worklist/4.0-validation",
                "arm": arm["arm"], "cells": bucket,
            })
            shards.append({"worklist": str(shard_dir / "worklist.json"),
                           "output_root": str(output_root), "arm_index": index,
                           "cells": len(bucket)})
        manifest.append({
            "arm_index": index, "arm": arm["arm"],
            "discovery_macro_gain": arm["macro_gain"],
            "discovery_actionable_macro_gain": arm["actionable_macro_gain"],
            "discovery_bootstrap_lower": arm["pooled_informative_bootstrap_lower"],
            "discovery_harm_ratio": arm["harm_ratio"],
            "shards": len(buckets), "pairs": len(cells),
        })
    atomic_checkpoint(root / "shard-index.json", {
        "schema_version": "mixed-stress-validation-shards/1.0", "shards": shards,
    })
    node_id = len(shards)
    atomic_checkpoint(root / "frozen-candidates.json", {
        "schema_version": "mixed-stress-validation-manifest/1.0",
        "selection_rule": RULE,
        "source_analysis": str(args.analysis.resolve()),
        "source_analysis_inventory_sha256": analysis["input_inventory_sha256"],
        "discovery_arm_count": analysis["arm_count"],
        "discovery_passing_arm_count": analysis["passing_arm_count"],
        "seeds_per_family": args.per_family,
        "total_shards": node_id, "candidates": manifest,
    })
    print(json.dumps({"candidates": len(manifest), "shards": node_id,
                      "arms": [m["arm_index"] for m in manifest]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
