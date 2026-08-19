from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "oracle-stage-a-freeze/1.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=False, capture_output=True, text=True
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def build_freeze_record(
    evaluation_path: Path,
    overlay_path: Path,
    results_doc_path: Path,
) -> dict[str, Any]:
    for path in (evaluation_path, overlay_path, results_doc_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    if evaluation.get("schema_version") != "oracle-bound-evaluation/1.0":
        raise ValueError("unsupported oracle evaluation schema")
    if evaluation.get("decision") != "new_session_gate_reachable_in_continuous_relaxation":
        raise ValueError("Stage A cannot be frozen without a reachable action-space decision")
    overlay_sha = _sha256(overlay_path)
    if evaluation.get("fault_knowledge_overlay", {}).get("sha256") != overlay_sha:
        raise ValueError("oracle evaluation does not match the supplied knowledge overlay")
    scenario_inputs = []
    for scenario in evaluation.get("scenarios", []):
        manifest = Path(scenario["manifest"])
        if not manifest.is_file() or _sha256(manifest) != scenario["manifest_sha256"]:
            raise ValueError("oracle scenario manifest checksum no longer matches")
        scenario_inputs.append({
            "scenario_id": scenario["scenario_id"],
            "seed": scenario["seed"],
            "manifest": _relative(manifest),
            "manifest_sha256": scenario["manifest_sha256"],
            "static_metadata": scenario["static_metadata"],
        })
    code_paths = [
        Path("optimization/oracle_bounds.py"),
        Path("experiments/evaluate_oracle_bounds.py"),
        Path("tests/test_oracle_bounds.py"),
    ]
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "frozen-stage-a-benchmark",
        "frozen_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "decision": evaluation["decision"],
        "non_deployable": True,
        "reserved_seeds_consumed": False,
        "evaluation": {
            "path": _relative(evaluation_path),
            "sha256": _sha256(evaluation_path),
            "created_at": evaluation.get("created_at"),
            "schema_version": evaluation["schema_version"],
        },
        "knowledge_overlay": {
            "path": _relative(overlay_path),
            "sha256": overlay_sha,
        },
        "results_document": {
            "path": _relative(results_doc_path),
            "sha256": _sha256(results_doc_path),
        },
        "scenario_inputs": scenario_inputs,
        "implementation_sha256": {
            _relative(path): _sha256(path) for path in code_paths
        },
        "git_commit": _git_commit(),
        "notes": [
            "This freezes an offline continuous-relaxation benchmark, not a deployable policy.",
            "The freeze consumes no new simulator seed and does not modify manifests.",
            "MPC development must use non-reserved seeds and compare against static from the same state.",
        ],
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    record["freeze_record_sha256"] = hashlib.sha256(canonical).hexdigest()
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze the Stage A oracle benchmark")
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--knowledge-overlay", type=Path, required=True)
    parser.add_argument("--results-document", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite Stage A freeze: {args.output}")
    record = build_freeze_record(
        args.evaluation, args.knowledge_overlay, args.results_document
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "freeze_record_sha256": record["freeze_record_sha256"],
        "status": record["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
