from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .artifacts import ArtifactPolicy, atomic_json, topology_identity
from .packed_runner import file_sha256
from .run_campaign_shard import run_shard


def profile(
    manifest: Path, output_root: Path, scratch_root: Path, *,
    forecast_bundle: Path | None, mpc_profile: Path | None,
) -> dict[str, Any]:
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    inputs = {
        "manifest": {"path": str(manifest.resolve()), "sha256": file_sha256(manifest)},
        "forecast_bundle": (
            {"path": str(forecast_bundle.resolve()), "sha256": file_sha256(forecast_bundle)}
            if forecast_bundle else None
        ),
        "mpc_profile": (
            {"path": str(mpc_profile.resolve()), "sha256": file_sha256(mpc_profile)}
            if mpc_profile else None
        ),
    }
    rows = []
    for index, controller in enumerate(("static", "reactive", "mpc")):
        destination = run_shard(
            manifest, output_root, "stage1-sequential-profile", 91001 + index,
            controller=controller,
            forecast_bundle=forecast_bundle if controller == "mpc" else None,
            mpc_profile=mpc_profile if controller == "mpc" else None,
            artifact_policy=ArtifactPolicy(silver_percentage=0),
            scratch_root=scratch_root / controller,
            progress_every_simulated_hours=None,
        )
        metadata = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
        performance = json.loads((destination / "performance.json").read_text(encoding="utf-8"))
        rows.append({
            "controller": metadata["controller"], "seed": metadata["seed"],
            "wall_seconds": performance["wall_seconds"],
            "cpu_seconds": performance["cpu_seconds"],
            "peak_rss_bytes": performance["peak_rss_bytes"],
            "phase_timings": {
                **performance["phase_timings"],
                "stage_out_seconds": metadata["stage_out_seconds"],
            },
            "checkpoint_lineage": metadata["checkpoint_lineage"],
            "source_fingerprint": metadata["source_fingerprint"],
        })
    source_fingerprints = {row.pop("source_fingerprint") for row in rows}
    if len(source_fingerprints) != 1:
        raise ValueError("source fingerprint changed during sequential profiling")
    return {
        "schema_version": "stage1-sequential-profile/1.1",
        "scenario_id": manifest_payload["scenario_id"],
        "topology_id": topology_identity(manifest_payload),
        "inputs": inputs,
        "source_fingerprint": source_fingerprints.pop(),
        "runs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile sequential one-day Stage 1 controller shards")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--scratch-root", required=True, type=Path)
    parser.add_argument("--forecast-bundle", type=Path)
    parser.add_argument("--mpc-profile", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = profile(
        args.manifest, args.output_root, args.scratch_root,
        forecast_bundle=args.forecast_bundle, mpc_profile=args.mpc_profile,
    )
    atomic_json(args.report, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
