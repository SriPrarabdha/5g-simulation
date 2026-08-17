from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .artifacts import ArtifactPolicy, atomic_json, gold_pair_key, topology_identity
from .run_campaign_shard import run_shard


def characterize(
    manifest: Path, output_root: Path, scratch_root: Path, *,
    forecast_bundle: Path | None, mpc_profile: Path | None,
) -> dict[str, Any]:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    topology = topology_identity(payload)
    rows: list[dict[str, Any]] = []
    tier_seeds = {"bronze": 93001, "silver": 93002, "gold": 93003}
    for tier, seed in tier_seeds.items():
        policy = ArtifactPolicy(
            silver_percentage=100 if tier == "silver" else 0,
            gold_pair_keys=(
                frozenset({gold_pair_key(topology, payload["scenario_id"], seed)})
                if tier == "gold" else frozenset()
            ),
        )
        for controller in ("static", "reactive", "mpc"):
            destination = run_shard(
                manifest, output_root, f"stage1-artifacts-{tier}", seed,
                controller=controller,
                forecast_bundle=forecast_bundle if controller == "mpc" else None,
                mpc_profile=mpc_profile if controller == "mpc" else None,
                artifact_policy=policy,
                scratch_root=scratch_root / tier / controller,
                progress_every_simulated_hours=None,
            )
            metadata = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
            rows.append({
                "tier": tier, "controller": metadata["controller"],
                "artifact_bytes": sum(item["bytes"] for item in metadata["artifacts"]),
                "artifacts": metadata["artifacts"],
                "checkpoint_count": len(metadata["checkpoint_lineage"]),
                "checkpoint_bytes": sum(item["bytes"] for item in metadata["checkpoint_lineage"]),
                "checkpoint_seconds": json.loads(
                    (destination / "performance.json").read_text(encoding="utf-8")
                )["phase_timings"]["checkpointing_seconds"],
                "stage_out_seconds": metadata["stage_out_seconds"],
                "scratch_bytes": metadata["scratch_bytes"],
            })
    return {"schema_version": "stage1-artifact-characterization/1.0", "runs": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Characterize Stage 1 Bronze/Silver/Gold artifacts")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--scratch-root", required=True, type=Path)
    parser.add_argument("--forecast-bundle", type=Path)
    parser.add_argument("--mpc-profile", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = characterize(
        args.manifest, args.output_root, args.scratch_root,
        forecast_bundle=args.forecast_bundle, mpc_profile=args.mpc_profile,
    )
    atomic_json(args.report, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
