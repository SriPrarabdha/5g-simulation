from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifacts import ArtifactPolicy, atomic_json, topology_identity
from .packed_runner import WorkItem


def build(
    manifest: Path, worker_count: int, *, seed_base: int,
    forecast_bundle: Path | None, mpc_profile: Path | None,
) -> dict:
    if worker_count not in {8, 16, 32, 64}:
        raise ValueError("worker_count must be a Stage 1 packing rung")
    topology = topology_identity(json.loads(manifest.read_text(encoding="utf-8")))
    policy = ArtifactPolicy(silver_percentage=1.0, gold_pair_keys=frozenset()).to_dict()
    controllers = ("static", "reactive", "mpc")
    items = []
    for index in range(worker_count * 2):
        controller = controllers[index % len(controllers)]
        items.append(WorkItem(
            topology_id=topology, scenario_manifest=str(manifest.resolve()),
            seed=seed_base + index, controller=controller,
            model=str(forecast_bundle.resolve()) if controller == "mpc" and forecast_bundle else None,
            optimizer_profile=str(mpc_profile.resolve()) if controller == "mpc" and mpc_profile else None,
            artifact_policy=policy,
        ).to_dict())
    return {
        "schema_version": "stage1-work-list/1.0", "worker_count": worker_count,
        "waves": 2, "seed_base": seed_base, "interleave": list(controllers),
        "work_items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze a two-wave interleaved Stage 1 work list")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--workers", required=True, type=int, choices=(8, 16, 32, 64))
    parser.add_argument("--seed-base", required=True, type=int)
    parser.add_argument("--forecast-bundle", type=Path)
    parser.add_argument("--mpc-profile", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = build(
        args.manifest, args.workers, seed_base=args.seed_base,
        forecast_bundle=args.forecast_bundle, mpc_profile=args.mpc_profile,
    )
    atomic_json(args.output, payload)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
