"""Independently verify one group of Phase-2 forecast artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from forecasting import CandidateForecastBundle, load_forecaster_bundle


CANDIDATE_FAMILIES = (
    "ridge-v2",
    "hist-gradient-quantile",
    "regime-ensemble",
    "lightgbm-quantile",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_metric(path: Path, index: int, family: str) -> dict[str, Any]:
    metric = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": "forecast-selection-group/1.0",
        "seed": 46002,
        "group_index": index,
        "model_family": family,
    }
    for field, value in expected.items():
        if metric.get(field) != value:
            raise ValueError(f"{path}: expected {field}={value!r}")
    return metric


def verify(root: Path, index: int) -> dict[str, Any]:
    if not 0 <= index < 96:
        raise ValueError("Phase-2 group index must be in 0..95")
    suffix = f"{index:03d}"
    verified: dict[str, Any] = {}
    group_id: str | None = None
    for family in CANDIDATE_FAMILIES:
        trained_path = root / "trained-v3" / family / f"group-{suffix}"
        calibrated_path = root / "selection-v3" / family / f"bundle-{suffix}"
        metric_path = root / "selection-v3" / family / f"metrics-{suffix}.json"
        trained = CandidateForecastBundle.load(trained_path)
        calibrated = CandidateForecastBundle.load(calibrated_path)
        metric = _load_metric(metric_path, index, family)
        if trained.manifest["model_family"] != family or calibrated.manifest["model_family"] != family:
            raise ValueError(f"model-family mismatch for {family} group {index}")
        trained_groups = list(trained.manifest["groups"])
        calibrated_groups = list(calibrated.manifest["groups"])
        if len(trained_groups) != 1 or calibrated_groups != trained_groups:
            raise ValueError(f"group packaging mismatch for {family} group {index}")
        current_group = trained_groups[0]
        if group_id is None:
            group_id = current_group
        if current_group != group_id or metric["group_id"] != group_id:
            raise ValueError(f"cross-family group mismatch at index {index}")
        if trained.manifest["source"].get("manifest_seed") != 46001:
            raise ValueError(f"training seed mismatch for {family} group {index}")
        source = calibrated.manifest["source"]
        if source.get("calibration_seed") != 46002:
            raise ValueError(f"calibration seed mismatch for {family} group {index}")
        if source.get("parent_bundle_sha256") != trained.manifest["bundle_sha256"]:
            raise ValueError(f"parent bundle mismatch for {family} group {index}")
        if metric["bundle_sha256"] != calibrated.manifest["bundle_sha256"]:
            raise ValueError(f"metric bundle mismatch for {family} group {index}")
        verified[family] = {
            "trained_bundle_sha256": trained.manifest["bundle_sha256"],
            "calibrated_bundle_sha256": calibrated.manifest["bundle_sha256"],
            "metric_file_sha256": _sha256(metric_path),
        }

    calendar_path = root / "trained-v3" / "calendar-ridge.json"
    calendar = load_forecaster_bundle(calendar_path)
    calendar_metric_path = root / "selection-v3" / "calendar-ridge" / f"metrics-{suffix}.json"
    calendar_metric = _load_metric(calendar_metric_path, index, "calendar-ridge")
    calendar.validate_groups([next(iter(CandidateForecastBundle.load(
        root / "trained-v3" / CANDIDATE_FAMILIES[0] / f"group-{suffix}"
    ).model.group_keys.values()))])
    if calendar_metric["group_id"] != group_id:
        raise ValueError(f"calendar group mismatch at index {index}")
    if calendar_metric["bundle_sha256"] != calendar.metadata["bundle_sha256"]:
        raise ValueError(f"calendar bundle mismatch at index {index}")
    verified["calendar-ridge"] = {
        "trained_bundle_sha256": calendar.metadata["bundle_sha256"],
        "calibrated_bundle_sha256": None,
        "metric_file_sha256": _sha256(calendar_metric_path),
    }
    return {
        "schema_version": "forecast-phase2-artifact-audit-shard/1.0",
        "group_index": index,
        "group_id": group_id,
        "verified_production_load_path": True,
        "families": verified,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--group-index", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite audit shard: {args.output}")
    result = verify(args.root, args.group_index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "group": result["group_id"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
