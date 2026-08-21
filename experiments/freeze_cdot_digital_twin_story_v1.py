#!/usr/bin/env python3
"""Seal the source and evidence interfaces for the integrated story deck."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from experiments.artifacts import atomic_json


ROOT = Path(__file__).resolve().parent.parent
INTERFACES = (
    "experiments/freeze_cdot_digital_twin_story_v1.py",
    "scripts/build_cdot_digital_twin_story_v1.py",
    "scripts/build_phase3_cdot_showcase_v5.py",
    "scripts/build_phase3_cdot_showcase_v6.py",
    "presentation/delhi_evidence_manifest.json",
    "presentation/delhi/build-report.json",
    "output/showcase/cdot-production-final/metrics.json",
    "output/control-science/v1/phase3-cdot-showcase-v6/artifact-manifest.json",
    "output/control-science/v1/phase3-cdot-v6-interface-freeze.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite story interface freeze: {args.output}")
    paths = [ROOT / relative for relative in INTERFACES]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"story inputs are missing: {missing}")
    production = json.loads((ROOT / INTERFACES[6]).read_text(encoding="utf-8"))
    v6 = json.loads((ROOT / INTERFACES[7]).read_text(encoding="utf-8"))
    if (
        production.get("production_summary", {}).get("final_node_count") != 12
        or production.get("production_summary", {}).get("final_shards") != 384
        or production.get("production_summary", {}).get("failures") != 0
        or v6.get("decision") != "retain_static"
        or v6.get("protected_seed_state", {}).get("validation_46201_46216_consumed") is not False
        or v6.get("protected_seed_state", {}).get("release_46301_46330_consumed") is not False
    ):
        raise ValueError("story evidence does not satisfy the frozen decision boundary")
    payload = {
        "schema_version": "cdot-digital-twin-story-freeze/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "frozen": True,
        "historical_results_rescored": False,
        "new_experiments_authorized": False,
        "production_controller": "static-capacity-v1",
        "candidate_mode": "guarded_shadow_or_replay_only",
        "protected_validation_seeds_consumed": False,
        "protected_release_seeds_consumed": False,
        "decision": "retain_static",
        "interfaces": {relative: _sha256(ROOT / relative) for relative in INTERFACES},
    }
    atomic_json(args.output, payload)
    print(json.dumps({
        "output": str(args.output.resolve()), "interfaces": len(INTERFACES),
        "sha256": _sha256(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
