"""Checksum-verified packaging for fitted causal forecast challengers."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import pickle
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from schemas import GroupKey

from .baselines import ForecastingError


SCHEMA_VERSION = "causal-forecast-bundle/1.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


class CandidateForecastBundle:
    """A verified directory containing a manifest and fitted Python model.

    The pickle is loaded only after both its digest and the manifest digest are
    verified. Bundles remain trusted local artifacts; callers must not load a
    bundle received from an untrusted party.
    """

    def __init__(self, path: Path, manifest: dict[str, Any], model: Any) -> None:
        self.path = path
        self.manifest = manifest
        self.model = model
        self.model_version = str(manifest["model_version"])

    @property
    def required_history_windows(self) -> int:
        return int(getattr(self.model, "required_history_windows"))

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self.manifest)

    @property
    def payload(self) -> dict[str, Any]:
        # Compatibility with campaign identity code written for JSON bundles.
        return self.manifest

    def validate_groups(self, groups: Iterable[GroupKey]) -> None:
        trained = {
            group_id: GroupKey.from_dict(value)
            for group_id, value in self.manifest["groups"].items()
        }
        missing = [group.selection_id for group in groups if group.selection_id not in trained]
        mismatched = [
            group.selection_id for group in groups
            if group.selection_id in trained and trained[group.selection_id] != group
        ]
        if missing or mismatched:
            raise ForecastingError(
                f"candidate bundle is incompatible: missing={missing[:5]} mismatched={mismatched[:5]}"
            )

    def predict(self, *args: Any, **kwargs: Any):
        return self.model.predict(*args, **kwargs)

    @classmethod
    def load(cls, path: str | Path) -> "CandidateForecastBundle":
        root = Path(path)
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ForecastingError("unsupported causal forecast bundle schema")
        expected_bundle = manifest.get("bundle_sha256")
        unsigned = dict(manifest)
        unsigned.pop("bundle_sha256", None)
        if expected_bundle != _canonical_hash(unsigned):
            raise ForecastingError("causal forecast bundle manifest checksum mismatch")
        artifact = manifest["model_artifact"]
        model_path = root / artifact["path"]
        if model_path.stat().st_size != int(artifact["bytes"]) or _sha256(model_path) != artifact["sha256"]:
            raise ForecastingError("causal forecast model artifact checksum mismatch")
        with model_path.open("rb") as stream:
            model = pickle.load(stream)
        if str(getattr(model, "model_version", "")) != str(manifest["model_version"]):
            raise ForecastingError("loaded model version does not match bundle manifest")
        return cls(root, manifest, model)


def write_candidate_forecast_bundle(
    path: str | Path,
    model: Any,
    *,
    source: dict[str, Any],
) -> dict[str, Any]:
    root = Path(path)
    if root.exists():
        raise FileExistsError(f"refusing to overwrite causal forecast bundle: {root}")
    root.mkdir(parents=True)
    temporary = root / f".model.{os.getpid()}.tmp"
    with temporary.open("wb") as stream:
        pickle.dump(model, stream, protocol=5)
        stream.flush()
        os.fsync(stream.fileno())
    model_path = root / "model.pkl"
    temporary.replace(model_path)
    group_keys = getattr(model, "group_keys", None)
    if group_keys is None and hasattr(model, "normal_model"):
        group_keys = model.normal_model.group_keys
    if not group_keys:
        raise ForecastingError("cannot package a model without fitted group keys")
    model_horizons = getattr(model, "horizons", None)
    if model_horizons is None and hasattr(model, "normal_model"):
        model_horizons = model.normal_model.horizons
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model_family": str(getattr(model, "model_family")),
        "model_version": str(getattr(model, "model_version")),
        "horizons": list(model_horizons or ()),
        "feature_schema": getattr(model, "feature_schema"),
        "groups": {
            group_id: {
                "zone": key.zone, "dnn": key.dnn, "snssai": key.snssai,
                "five_qi": key.five_qi,
            }
            for group_id, key in sorted(group_keys.items())
        },
        "source": source,
        "environment": {
            "python": platform.python_version(),
            "numpy": _version("numpy"),
            "scipy": _version("scipy"),
            "scikit_learn": _version("scikit-learn"),
            "lightgbm": _version("lightgbm"),
        },
        "model_artifact": {
            "path": model_path.name,
            "sha256": _sha256(model_path),
            "bytes": model_path.stat().st_size,
            "pickle_protocol": 5,
        },
    }
    manifest["bundle_sha256"] = _canonical_hash(manifest)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Exercise the exact production load path before publishing success.
    CandidateForecastBundle.load(root)
    return manifest


def merge_candidate_forecast_bundles(
    inputs: Iterable[str | Path], output: str | Path, *, source: dict[str, Any]
) -> dict[str, Any]:
    bundles = [CandidateForecastBundle.load(path) for path in inputs]
    if not bundles:
        raise ValueError("at least one causal forecast bundle is required")
    families = {item.manifest["model_family"] for item in bundles}
    versions = {item.manifest["model_version"] for item in bundles}
    horizons = {tuple(item.manifest["horizons"]) for item in bundles}
    if len(families) != 1 or len(versions) != 1 or len(horizons) != 1:
        raise ForecastingError("cannot merge bundles with different model contracts")
    target = bundles[0].model
    target_base = target.normal_model if hasattr(target, "normal_model") else target
    for bundle in bundles[1:]:
        source_model = bundle.model.normal_model if hasattr(bundle.model, "normal_model") else bundle.model
        overlap = set(target_base.group_keys) & set(source_model.group_keys)
        if overlap:
            raise ForecastingError(f"duplicate groups while merging causal bundles: {sorted(overlap)}")
        target_base.group_keys.update(source_model.group_keys)
        target_base.models.update(source_model.models)
        target_base.calibration_widths.update(source_model.calibration_widths)
        target_base.calibration_widths.update(source_model.calibration_widths)
    return write_candidate_forecast_bundle(output, target, source=source)


def load_forecaster_bundle(path: str | Path):
    root = Path(path)
    if root.is_dir():
        return CandidateForecastBundle.load(root)
    from .bundle import TrainedForecastBundle
    return TrainedForecastBundle.load(root)
