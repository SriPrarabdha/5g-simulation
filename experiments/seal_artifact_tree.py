"""Write a deterministic SHA-256 manifest for a completed artifact tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-name", default="artifact-manifest.json")
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / args.output_name
    artifacts = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path == output:
            continue
        artifacts.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    payload = {
        "schema_version": "artifact-tree-manifest/1.0",
        "hash_algorithm": "sha256",
        "file_count": len(artifacts),
        "total_bytes": sum(item["bytes"] for item in artifacts),
        "artifacts": artifacts,
    }
    if output.exists():
        existing = json.loads(output.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError(f"existing manifest does not match current tree: {output}")
    else:
        atomic_json(output, payload)
    print(json.dumps({
        "output": str(output), "sha256": _sha256(output),
        "files": len(artifacts), "bytes": payload["total_bytes"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
