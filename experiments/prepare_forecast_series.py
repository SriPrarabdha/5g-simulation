from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import pickle
from pathlib import Path

from experiments.seed_policy import require_forecast_seed
from experiments.train_forecaster import collect_training_series
from simulator.macro.config import load_scenario


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare immutable per-group forecast series cache")
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--purpose", choices=("train", "selection"), required=True)
    parser.add_argument("--controller", default="static-capacity-v1")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite series cache: {args.output}")
    config = load_scenario(args.manifest)
    require_forecast_seed(config.seed, args.purpose)
    series = collect_training_series(args.campaign_root, config, controller=args.controller)
    args.output.mkdir(parents=True)
    groups = []
    for index, group_id in enumerate(sorted(series)):
        path = args.output / f"group-{index:03d}.pkl.gz"
        with gzip.open(path, "wb", compresslevel=5) as stream:
            pickle.dump(series[group_id], stream, protocol=5)
        groups.append({
            "index": index, "group_id": group_id, "path": path.name,
            "sha256": _sha256(path),
            "observations": sum(len(sequence) for sequence in series[group_id]),
        })
    manifest = {
        "schema_version": "forecast-series-cache/1.0", "purpose": args.purpose,
        "seed": config.seed, "scenario_id": config.scenario_id,
        "source_manifest": str(args.manifest.resolve()), "groups": groups,
    }
    (args.output / "index.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), "groups": len(groups), "seed": config.seed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
