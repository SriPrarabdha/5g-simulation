from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifacts import ArtifactPolicy, atomic_json, topology_identity
from .packed_runner import WorkItem, file_sha256
from .run_campaign_shard import source_fingerprint


def build(
    manifest: Path, worker_count: int, *, seed_base: int,
    forecast_bundle: Path | None, mpc_profile: Path | None,
    node_count: int = 1, waves_per_node: int = 2,
) -> dict:
    if worker_count not in {8, 16, 32, 64}:
        raise ValueError("worker_count must be a Stage 1 packing rung")
    if node_count < 1 or waves_per_node < 1:
        raise ValueError("node_count and waves_per_node must be positive")
    topology = topology_identity(json.loads(manifest.read_text(encoding="utf-8")))
    common_hashes = {"manifest": file_sha256(manifest)}
    policy = ArtifactPolicy(silver_percentage=1.0, gold_pair_keys=frozenset()).to_dict()
    controllers = ("static", "reactive", "mpc")
    items = []
    for index in range(worker_count * node_count * waves_per_node):
        controller = controllers[index % len(controllers)]
        model = forecast_bundle if controller == "mpc" and forecast_bundle else None
        profile = mpc_profile if controller == "mpc" and mpc_profile else None
        input_sha256 = dict(common_hashes)
        if model:
            input_sha256["model"] = file_sha256(model)
        if profile:
            input_sha256["optimizer_profile"] = file_sha256(profile)
        items.append(WorkItem(
            topology_id=topology, scenario_manifest=str(manifest.resolve()),
            seed=seed_base + index, controller=controller,
            model=str(model.resolve()) if model else None,
            optimizer_profile=str(profile.resolve()) if profile else None,
            artifact_policy=policy,
            input_sha256=input_sha256,
        ).to_dict())
    return {
        "schema_version": "stage1-work-list/1.0", "worker_count": worker_count,
        "node_count": node_count, "waves_per_node": waves_per_node,
        "waves": node_count * waves_per_node,
        "seed_base": seed_base, "interleave": list(controllers),
        "source_fingerprint": source_fingerprint(Path(__file__).resolve().parent.parent),
        "work_items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze a two-wave interleaved Stage 1 work list")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--workers", required=True, type=int, choices=(8, 16, 32, 64))
    parser.add_argument("--seed-base", required=True, type=int)
    parser.add_argument("--forecast-bundle", type=Path)
    parser.add_argument("--mpc-profile", type=Path)
    parser.add_argument("--nodes", type=int, default=1)
    parser.add_argument("--waves-per-node", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = build(
        args.manifest, args.workers, seed_base=args.seed_base,
        forecast_bundle=args.forecast_bundle, mpc_profile=args.mpc_profile,
        node_count=args.nodes, waves_per_node=args.waves_per_node,
    )
    atomic_json(args.output, payload)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
