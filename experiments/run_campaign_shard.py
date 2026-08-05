from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simulator.macro import Simulator, load_scenario


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(project_root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def shard_directory(output_root: Path, campaign_id: str, scenario_id: str, controller: str, seed: int) -> Path:
    return (
        output_root
        / "schema_major=0"
        / f"campaign={campaign_id}"
        / f"scenario={scenario_id}"
        / f"controller={controller}"
        / f"seed={seed:06d}"
    )


def run_shard(
    manifest: Path,
    output_root: Path,
    campaign_id: str,
    seed: int,
    skip_existing: bool = False,
) -> Path:
    project_root = Path(__file__).resolve().parent.parent
    base_config = load_scenario(manifest)
    config = replace(base_config, seed=seed)
    simulator = Simulator(config)
    destination = shard_directory(output_root, campaign_id, config.scenario_id, simulator.controller.name, seed)
    run_path = destination / "run.jsonl"
    metadata_path = destination / "metadata.json"
    manifest_digest = file_sha256(manifest)

    if run_path.exists() or metadata_path.exists():
        if not skip_existing or not run_path.is_file() or not metadata_path.is_file():
            raise FileExistsError(f"refusing to overwrite shard: {destination}")
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        if existing.get("manifest_sha256") != manifest_digest or existing.get("run_file_sha256") != file_sha256(run_path):
            raise FileExistsError(f"existing shard does not match this manifest or is incomplete: {destination}")
        return destination

    result = simulator.run()
    destination.mkdir(parents=True, exist_ok=True)
    temporary_run = destination / f".run.jsonl.{os.getpid()}.tmp"
    result.write_jsonl(temporary_run)
    os.replace(temporary_run, run_path)

    metadata = {
        "schema_version": "experiment-shard/1.0",
        "campaign_id": campaign_id,
        "scenario_id": config.scenario_id,
        "seed": seed,
        "controller": result.controller,
        "manifest": str(manifest.resolve()),
        "manifest_sha256": manifest_digest,
        "git_commit": git_commit(project_root),
        "component_versions": {"cdot_upf_simulation": "0.1.0", "python": platform.python_version()},
        "host": socket.gethostname(),
        "job_id": os.environ.get("PBS_JOBID"),
        "array_index": os.environ.get("PBS_ARRAY_INDEX"),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_file": run_path.name,
        "run_file_sha256": file_sha256(run_path),
        "summary": result.summary,
    }
    atomic_json(metadata_path, metadata)
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one deterministic macro-campaign shard")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-root", default=Path("output/macro"), type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--skip-existing", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    destination = run_shard(
        args.manifest, args.output_root, args.campaign_id, args.seed,
        skip_existing=args.skip_existing,
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
