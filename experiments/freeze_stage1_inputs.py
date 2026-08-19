from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecasting import TrainedForecastBundle
from simulator.macro import load_scenario

from .artifacts import ArtifactPolicy, atomic_json, topology_identity
from .run_campaign_shard import source_fingerprint


def _file(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def build(manifest: Path, forecast_bundle: Path, mpc_profile: Path, artifact_policy: Path) -> dict[str, Any]:
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    config = load_scenario(manifest)
    bundle = TrainedForecastBundle.load(forecast_bundle)
    bundle.validate_groups(group.key for group in config.groups)
    profile_payload = json.loads(mpc_profile.read_text(encoding="utf-8"))
    if profile_payload.get("schema_version") != "cohort-mpc-profile/1.0":
        raise ValueError("unsupported MPC profile schema")
    policy = ArtifactPolicy.from_dict(json.loads(artifact_policy.read_text(encoding="utf-8")))
    development_only = bool(profile_payload.get("development_only", False))
    project_root = Path(__file__).resolve().parent.parent
    record = {
        "schema_version": "stage1-frozen-inputs/1.0",
        "status": "frozen-provisional" if development_only else "frozen-production",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scenario_id": config.scenario_id,
        "topology_id": topology_identity(manifest_payload),
        "manifest": _file(manifest),
        "manifest_embedded_sha256": manifest_payload.get("corpus", {}).get("manifest_sha256"),
        "forecast_bundle": {
            **_file(forecast_bundle),
            "bundle_sha256": bundle.metadata["bundle_sha256"],
            "model_version": bundle.model_version,
            "groups": len(bundle.payload["groups"]),
        },
        "mpc_profile": {
            **_file(mpc_profile),
            "profile_id": profile_payload["profile_id"],
            "development_only": development_only,
        },
        "artifact_policy": {**_file(artifact_policy), "contract": policy.to_dict()},
        "source_fingerprint": source_fingerprint(project_root),
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    record["record_sha256"] = hashlib.sha256(canonical).hexdigest()
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze exact Stage 1 manifest/model/profile/policy identities")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--forecast-bundle", required=True, type=Path)
    parser.add_argument("--mpc-profile", required=True, type=Path)
    parser.add_argument("--artifact-policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite frozen Stage 1 inputs: {args.output}")
    record = build(args.manifest, args.forecast_bundle, args.mpc_profile, args.artifact_policy)
    atomic_json(args.output, record)
    print(json.dumps({
        "output": str(args.output), "status": record["status"],
        "record_sha256": record["record_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
