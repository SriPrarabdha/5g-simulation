"""Create a deterministic, content-addressed archive of executable source state."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json


SOURCE_ROOTS = (
    "configs", "demo", "docs", "experiments", "forecasting", "optimization",
    "pbs", "schemas", "scripts", "simulator", "tests",
)
TOP_LEVEL_PATTERNS = ("pyproject.toml", "requirements*.lock", "README*", "LICENSE*")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _eligible(path: Path) -> bool:
    return (
        path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
        and not any(part in {".pytest_cache", ".mypy_cache", ".ruff_cache"} for part in path.parts)
    )


def source_files(extra: Iterable[Path] = ()) -> tuple[Path, ...]:
    paths: set[Path] = set()
    for relative in SOURCE_ROOTS:
        root = PROJECT_ROOT / relative
        if root.is_dir():
            paths.update(path for path in root.rglob("*") if _eligible(path))
    for pattern in TOP_LEVEL_PATTERNS:
        paths.update(path for path in PROJECT_ROOT.glob(pattern) if _eligible(path))
    paths.update(path.resolve() for path in extra if _eligible(path.resolve()))
    return tuple(sorted(paths, key=lambda item: str(item.relative_to(PROJECT_ROOT))))


def build_archive(destination: Path, files: Iterable[Path]) -> tuple[str, list[dict[str, object]]]:
    members = []
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            content = path.read_bytes()
            relative = str(path.relative_to(PROJECT_ROOT))
            info = tarfile.TarInfo(relative)
            info.size = len(content)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
            archive.addfile(info, io.BytesIO(content))
            members.append({
                "path": relative,
                "bytes": len(content),
                "sha256": _sha256_bytes(content),
            })
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0) as stream:
        stream.write(raw.getvalue())
    payload = compressed.getvalue()
    digest = _sha256_bytes(payload)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"source-{digest}.tar.gz"
    if target.exists() and _sha256_bytes(target.read_bytes()) != digest:
        raise ValueError(f"content-address collision at {target}")
    if not target.exists():
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, target)
    return digest, members


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--interface-freeze", required=True, type=Path)
    args = parser.parse_args()
    freeze = args.interface_freeze.resolve()
    digest, members = build_archive(args.output_root, source_files((freeze,)))
    archive = args.output_root / f"source-{digest}.tar.gz"
    manifest = {
        "schema_version": "content-addressed-source-archive/1.0",
        "archive": str(archive.resolve()),
        "archive_sha256": digest,
        "archive_bytes": archive.stat().st_size,
        "member_count": len(members),
        "members": members,
        "interface_freeze": {
            "path": str(freeze),
            "sha256": _sha256_bytes(freeze.read_bytes()),
        },
    }
    manifest_path = args.output_root / f"source-{digest}.manifest.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise ValueError(f"archive manifest mismatch at {manifest_path}")
    else:
        atomic_json(manifest_path, manifest)
    print(json.dumps({
        "archive": str(archive.resolve()), "sha256": digest,
        "members": len(members), "manifest": str(manifest_path.resolve()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
