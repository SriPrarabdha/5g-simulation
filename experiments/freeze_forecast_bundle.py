from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecasting import TrainedForecastBundle

from .report_forecast_bundle import build_report


FREEZE_SCHEMA = "forecast-freeze/1.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", *args], check=False, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _relative_or_absolute(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def build_freeze_record(
    bundle_path: Path,
    manifest_path: Path,
    metadata_path: Path,
) -> dict[str, Any]:
    bundle_path = bundle_path.resolve()
    manifest_path = manifest_path.resolve()
    metadata_path = metadata_path.resolve()
    for path in (bundle_path, manifest_path, metadata_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    bundle = TrainedForecastBundle.load(bundle_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    manifest_file_sha = _sha256(manifest_path)
    expected_manifest_sha = metadata.get("manifest_sha256")
    if expected_manifest_sha and expected_manifest_sha != manifest_file_sha:
        raise ValueError(
            "campaign metadata manifest hash does not match the supplied manifest"
        )

    dirty_paths = (_git(["status", "--short"]) or "").splitlines()
    report = build_report(bundle_path)
    record: dict[str, Any] = {
        "schema_version": FREEZE_SCHEMA,
        "status": "frozen-provisional",
        "release_accepted": False,
        "frozen_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model": {
            "path": _relative_or_absolute(bundle_path),
            "file_sha256": _sha256(bundle_path),
            "bundle_sha256": bundle.payload["bundle_sha256"],
            "schema_version": bundle.payload["schema_version"],
            "model_version": bundle.payload["model_version"],
            "algorithm": bundle.payload["algorithm"],
            "synthetic": bundle.payload["synthetic"],
            "groups": len(bundle.payload["groups"]),
            "fitted_models": len(bundle.payload["groups"]) * 3 * 8,
            "horizon_minutes": bundle.payload["horizon_minutes"],
            "ordered_split": bundle.payload["split"],
        },
        "training_corpus": {
            "manifest": _relative_or_absolute(manifest_path),
            "manifest_file_sha256": manifest_file_sha,
            "manifest_embedded_sha256": manifest.get("corpus", {}).get("manifest_sha256"),
            "campaign_metadata": _relative_or_absolute(metadata_path),
            "campaign_metadata_file_sha256": _sha256(metadata_path),
            "campaign_id": metadata.get("campaign_id"),
            "scenario_id": metadata.get("scenario_id"),
            "controller": metadata.get("controller"),
            "seed": metadata.get("seed"),
            "canonical_file": metadata.get("canonical_file"),
            "parquet_file_sha256": metadata.get("parquet_file_sha256"),
            "run_file_sha256": metadata.get("run_file_sha256"),
            "selection_audits_sha256": metadata.get("selection_audits_sha256"),
            "steps": metadata.get("summary", {}).get("steps"),
            "duration_days": manifest.get("corpus", {}).get("duration_days"),
            "nominal_ue_population": manifest.get("corpus", {}).get("nominal_ue_population"),
            "topology": manifest.get("corpus", {}).get("topology"),
        },
        "held_out_evaluation": report,
        "reproducibility": {
            "git_commit": _git(["rev-parse", "HEAD"]),
            "worktree_dirty": bool(dirty_paths),
            "dirty_paths": dirty_paths,
            "training_code_sha256": {
                "forecasting/bundle.py": _sha256(Path("forecasting/bundle.py")),
                "experiments/train_forecaster.py": _sha256(Path("experiments/train_forecaster.py")),
            },
            "python": platform.python_version(),
            "dependencies": {
                name: importlib.metadata.version(name)
                for name in ("numpy", "pyarrow")
            },
        },
        "pending_acceptance_gates": [
            "compare the same held-out windows with seasonal-naive and moving-average baselines",
            "report normal, surge, brownout, and outage windows separately",
            "retrain/evaluate against the manifest's explicit 11/2/3 week split",
            "show predictive-controller overload reduction on paired controller runs",
            "revalidate and recalibrate on representative C-DOT telemetry",
        ],
        "notes": [
            "This record freezes a synthetic-data model candidate; it is not C-DOT production validation.",
            "The source worktree was dirty if reproducibility.worktree_dirty is true; exact code hashes are recorded.",
            "The model bundle is not modified by this operation.",
        ],
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    record["freeze_record_sha256"] = hashlib.sha256(canonical).hexdigest()
    return record


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze the identity, lineage, and provisional evaluation of a forecast bundle"
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--campaign-metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite frozen record: {args.output}")
    record = build_freeze_record(args.bundle, args.manifest, args.campaign_metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "status": record["status"],
        "model_bundle_sha256": record["model"]["bundle_sha256"],
        "freeze_record_sha256": record["freeze_record_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
