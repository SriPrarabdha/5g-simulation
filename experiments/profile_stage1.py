from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .artifacts import ArtifactPolicy, atomic_json
from .run_campaign_shard import run_shard


def profile(
    manifest: Path, output_root: Path, scratch_root: Path, *,
    forecast_bundle: Path | None, mpc_profile: Path | None,
) -> dict[str, Any]:
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
        })
    return {"schema_version": "stage1-sequential-profile/1.0", "runs": rows}


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
