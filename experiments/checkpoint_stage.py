from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import atomic_json


STAGE_SCHEMA = "stage1-durable-checkpoint-stage/1.1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*")) if path.is_file()
    ]


def _validate_attempt(attempt: Path) -> dict:
    marker = attempt / "complete.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid durable checkpoint marker: {marker}") from error
    if payload.get("schema_version") != STAGE_SCHEMA:
        raise ValueError(f"unsupported durable checkpoint stage: {marker}")
    scratch = attempt / "scratch"
    expected = payload.get("artifacts")
    if not isinstance(expected, list) or expected != _inventory(scratch):
        raise ValueError(f"durable checkpoint hash validation failed: {attempt}")
    return payload


def _partition(root: Path, index: int) -> Path:
    if index < 0:
        raise ValueError("partition index must be non-negative")
    return root / f"partition-{index:04d}"


def restore(root: Path, index: int, scratch: Path) -> Path | None:
    partition = _partition(root, index)
    candidates = [
        path.parent for path in partition.glob("attempt-*/complete.json")
        if (path.parent / "scratch").is_dir()
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))
    _validate_attempt(latest)
    scratch.mkdir(parents=True, exist_ok=True)
    shutil.copytree(latest / "scratch", scratch, dirs_exist_ok=True)
    if _inventory(scratch) != _inventory(latest / "scratch"):
        raise ValueError(f"restored durable checkpoint validation failed: {latest}")
    return latest


def save(root: Path, index: int, scratch: Path, attempt: str) -> Path | None:
    files = [path for path in scratch.rglob("*") if path.is_file()]
    if not files:
        return None
    partition = _partition(root, index)
    partition.mkdir(parents=True, exist_ok=True)
    token = re.sub(r"[^A-Za-z0-9_.-]", "_", attempt)
    final = partition / f"attempt-{token}"
    if final.exists():
        raise FileExistsError(f"durable checkpoint attempt already exists: {final}")
    staging = partition / f".{final.name}.staging-{os.getpid()}"
    staging.mkdir()
    shutil.copytree(scratch, staging / "scratch")
    artifacts = _inventory(staging / "scratch")
    atomic_json(staging / "complete.json", {
        "schema_version": STAGE_SCHEMA,
        "partition_index": index,
        "attempt": attempt,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "files": len(artifacts),
        "bytes": sum(int(item["bytes"]) for item in artifacts),
        "artifacts": artifacts,
    })
    _validate_attempt(staging)
    os.replace(staging, final)
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage incomplete Stage 1 scratch to or from durable shared storage")
    parser.add_argument("action", choices=("save", "restore"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--partition-index", required=True, type=int)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--attempt")
    args = parser.parse_args()
    if args.action == "save":
        if not args.attempt:
            parser.error("save requires --attempt")
        path = save(args.root, args.partition_index, args.scratch, args.attempt)
    else:
        path = restore(args.root, args.partition_index, args.scratch)
    print(str(path) if path is not None else "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
