from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .artifacts import ArtifactPolicy, atomic_json, topology_identity
from .packed_runner import WorkItem, file_sha256, work_list_sha256
from .run_campaign_shard import source_fingerprint


CONTROLLERS = ("static", "reactive", "mpc")


def build(
    manifest: Path,
    *,
    seed_base: int,
    seed_count: int,
    worker_count: int,
    node_count: int,
    forecast_bundle: Path,
    mpc_profile: Path,
    frozen_inputs: Path | None = None,
) -> dict:
    if seed_count < 1 or node_count < 1:
        raise ValueError("seed_count and node_count must be positive")
    if worker_count not in {8, 16, 32, 64}:
        raise ValueError("worker_count must be a characterized Stage 1 rung")
    if seed_count * len(CONTROLLERS) < worker_count * node_count:
        raise ValueError("Stage 2 work list must contain at least one full worker wave")
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    topology = topology_identity(manifest_payload)
    common_hashes = {"manifest": file_sha256(manifest)}
    policy = ArtifactPolicy(silver_percentage=1.0, gold_pair_keys=frozenset()).to_dict()
    source_digest = source_fingerprint(Path(__file__).resolve().parent.parent)
    freeze_payload = None
    if frozen_inputs is not None:
        freeze_payload = json.loads(frozen_inputs.read_text(encoding="utf-8"))
        if freeze_payload.get("schema_version") != "stage1-frozen-inputs/1.0":
            raise ValueError("unsupported frozen Stage 1 input record")
        expected_hashes = {
            "manifest": file_sha256(manifest),
            "forecast_bundle": file_sha256(forecast_bundle),
            "mpc_profile": file_sha256(mpc_profile),
        }
        for name, expected in expected_hashes.items():
            if freeze_payload.get(name, {}).get("sha256") != expected:
                raise ValueError(f"frozen Stage 1 {name} identity mismatch")
        if freeze_payload.get("artifact_policy", {}).get("contract") != policy:
            raise ValueError("frozen Stage 1 artifact policy mismatch")
        if freeze_payload.get("source_fingerprint") != source_digest:
            raise ValueError("source code does not match frozen Stage 1 inputs")
    freeze_status = freeze_payload.get("status") if freeze_payload else "unfrozen-provisional"
    if node_count > 4 and freeze_status != "frozen-production":
        raise ValueError("Stage 2 campaigns above four nodes require frozen-production inputs")
    items = []
    for seed in range(seed_base, seed_base + seed_count):
        for controller in CONTROLLERS:
            input_sha256 = dict(common_hashes)
            if controller == "mpc":
                input_sha256.update({
                    "model": file_sha256(forecast_bundle),
                    "optimizer_profile": file_sha256(mpc_profile),
                })
            items.append(WorkItem(
                topology_id=topology,
                scenario_manifest=str(manifest.resolve()),
                seed=seed,
                controller=controller,
                model=str(forecast_bundle.resolve()) if controller == "mpc" else None,
                optimizer_profile=str(mpc_profile.resolve()) if controller == "mpc" else None,
                artifact_policy=policy,
                input_sha256=input_sha256,
            ).to_dict())
    slots_per_wave = worker_count * node_count
    campaign_inputs = {
        "manifest_sha256": common_hashes["manifest"],
        "forecast_bundle_sha256": file_sha256(forecast_bundle),
        "mpc_profile_sha256": file_sha256(mpc_profile),
        "artifact_policy": policy,
        "controllers": list(CONTROLLERS),
        "source_fingerprint": source_digest,
        "input_freeze_status": freeze_status,
        "input_freeze_sha256": file_sha256(frozen_inputs) if frozen_inputs else None,
    }
    campaign_input_sha256 = hashlib.sha256(
        json.dumps(campaign_inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload = {
        "schema_version": "stage2-work-list/1.0",
        "worker_count": worker_count,
        "node_count": node_count,
        "seed_base": seed_base,
        "seed_count": seed_count,
        "controllers": list(CONTROLLERS),
        "slots_per_wave": slots_per_wave,
        "projected_waves": (len(items) + slots_per_wave - 1) // slots_per_wave,
        "source_fingerprint": source_digest,
        "campaign_inputs": campaign_inputs,
        "campaign_input_sha256": campaign_input_sha256,
        "input_freeze_status": freeze_status,
        "input_freeze_sha256": file_sha256(frozen_inputs) if frozen_inputs else None,
        "work_items": items,
    }
    payload["work_list_sha256"] = work_list_sha256(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze a paired multi-controller Stage 2 work list")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--seed-base", required=True, type=int)
    parser.add_argument("--seeds", required=True, type=int)
    parser.add_argument("--workers", required=True, type=int, choices=(8, 16, 32, 64))
    parser.add_argument("--nodes", required=True, type=int)
    parser.add_argument("--forecast-bundle", required=True, type=Path)
    parser.add_argument("--mpc-profile", required=True, type=Path)
    parser.add_argument("--frozen-inputs", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite frozen Stage 2 work list: {args.output}")
    payload = build(
        args.manifest, seed_base=args.seed_base, seed_count=args.seeds,
        worker_count=args.workers, node_count=args.nodes,
        forecast_bundle=args.forecast_bundle, mpc_profile=args.mpc_profile,
        frozen_inputs=args.frozen_inputs,
    )
    atomic_json(args.output, payload)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
