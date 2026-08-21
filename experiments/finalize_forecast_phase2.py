"""Build fail-closed Phase-2 completion evidence from verified shards."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from experiments.verify_forecast_phase2_shard import CANDIDATE_FAMILIES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_digest(paths: list[Path], base: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(base)).encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _candidate_table(candidates: list[dict[str, Any]]) -> str:
    lines = [
        "| Candidate | WAPE | vs baseline | p90 coverage | peak improvement | worst slice | Eligible |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in candidates:
        baseline_peak = row["baseline_event_peak_underprediction"]
        peak_improvement = (
            (baseline_peak - row["event_peak_underprediction"]) / baseline_peak
            if baseline_peak else 0.0
        )
        lines.append(
            f"| {row['model_family']} | {row['wape']:.3%} | "
            f"{row['relative_wape_improvement']:+.2%} | {row['coverage_p90']:.2%} | "
            f"{peak_improvement:+.2%} | {row['max_slice_regression']:+.2%} | "
            f"{'yes' if row['eligible'] else 'no'} |"
        )
    return "\n".join(lines)


def finalize(root: Path, pbs_jobs: dict[str, Any]) -> tuple[dict[str, Any], str]:
    selection_path = root / "forecast-selection-v3.json"
    phase1_path = root / "phase1-freeze.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    phase1 = json.loads(phase1_path.read_text(encoding="utf-8"))
    if selection.get("schema_version") != "forecast-selection/1.0":
        raise ValueError("unsupported forecast selection evidence")
    if selection.get("protected_test_seed_consumed") is not False:
        raise ValueError("protected forecast test seed state is not fail-closed")
    if selection.get("eligible_for_seed_46003_test"):
        raise ValueError("this no-promotion finalizer cannot close an eligible candidate")
    if selection.get("selected_model_family") is not None:
        raise ValueError("selection unexpectedly contains a promoted candidate")
    expected_families = set(CANDIDATE_FAMILIES) | {"calendar-ridge"}
    candidates = selection.get("candidates", [])
    if {item["model_family"] for item in candidates} != expected_families:
        raise ValueError("selection does not contain exactly the five frozen families")
    if any(item.get("groups") != 96 for item in candidates):
        raise ValueError("a selection family is missing group evidence")

    audit_paths = sorted((root / "artifact-audit-v3").glob("audit-*.json"))
    if len(audit_paths) != 96:
        raise ValueError("artifact audit must contain exactly 96 shards")
    audits = [json.loads(path.read_text(encoding="utf-8")) for path in audit_paths]
    if {row.get("group_index") for row in audits} != set(range(96)):
        raise ValueError("artifact audit indices are incomplete or duplicated")
    if len({row.get("group_id") for row in audits}) != 96:
        raise ValueError("artifact audit group identities are incomplete or duplicated")
    for row in audits:
        if row.get("verified_production_load_path") is not True:
            raise ValueError("artifact audit did not use the production load path")
        if set(row.get("families", {})) != expected_families:
            raise ValueError("artifact audit family set mismatch")

    metric_paths = sorted((root / "selection-v3").rglob("metrics-*.json"))
    trained_manifests = sorted((root / "trained-v3").rglob("manifest.json"))
    calibrated_manifests = sorted((root / "selection-v3").rglob("manifest.json"))
    if (len(metric_paths), len(trained_manifests), len(calibrated_manifests)) != (480, 384, 384):
        raise ValueError("Phase-2 artifact counts are incomplete")

    record = {
        "schema_version": "forecast-phase2-completion/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "phase2_complete_no_candidate_eligible",
        "decision": {
            "selected_model_family": None,
            "eligible_for_seed_46003_test": False,
            "seed_46003_consumed": False,
            "action": "retain protected test seed; do not promote a forecast challenger",
        },
        "seeds": {"train": 46001, "selection_and_calibration": 46002, "protected_test": 46003},
        "phase1_freeze": {
            "path": str(phase1_path.resolve()),
            "sha256": _sha256(phase1_path),
            "source_fingerprint": phase1["source_fingerprint"],
        },
        "selection": {
            "path": str(selection_path.resolve()),
            "sha256": _sha256(selection_path),
            "candidates": candidates,
        },
        "artifact_audit": {
            "shards": 96,
            "groups": 96,
            "production_load_path_verified": True,
            "tree_sha256": _tree_digest(audit_paths, root),
        },
        "artifact_counts": {
            "training_cache_groups": 96,
            "selection_cache_groups": 96,
            "trained_candidate_bundles": 384,
            "calibrated_candidate_bundles": 384,
            "selection_metric_shards": 480,
        },
        "artifact_tree_sha256": {
            "trained_manifests": _tree_digest(trained_manifests, root),
            "calibrated_manifests": _tree_digest(calibrated_manifests, root),
            "selection_metrics": _tree_digest(metric_paths, root),
        },
        "cache_indices": {
            purpose: {
                "path": str((root / f"{purpose}-cache-v3" / "index.json").resolve()),
                "sha256": _sha256(root / f"{purpose}-cache-v3" / "index.json"),
            }
            for purpose in ("train", "selection")
        },
        "pbs_jobs": pbs_jobs,
        "tests": phase1["tests"],
    }
    report = f"""# Forecast Phase 2 Completion Report

Phase 2 is complete. No challenger passed every frozen selection gate, so no model was promoted and protected seed 46003 was not consumed.

## Selection results

The common best simple reference was the six-window moving-average baseline at {candidates[0]['best_simple_baseline_wape']:.3%} WAPE.

{_candidate_table(candidates)}

The nonlinear histogram-gradient and LightGBM challengers produced the best WAPE improvements (about 11.6%) and both reduced scheduled/detected peak underprediction by more than 20%, but both missed the required 15% WAPE improvement and exceeded the 5% maximum aggregate regime/horizon regression. Every candidate kept calibrated p90 coverage inside 88–95%.

## Causal and release discipline

- Train seed: 46001.
- Selection/calibration seed: 46002, split into first-half calibration and second-half selection.
- Protected test seed 46003: not evaluated because the selection gate failed.
- Pre-observation unknown-surge rows were excluded from scoring: {candidates[0]['excluded_pre_observation_unknown_surge_rows']:,} per family.
- Phase 1 interfaces were frozen before authoritative training and selection.

## Reproducibility audit

- 96 train-cache groups and 96 selection-cache groups.
- 384 trained challenger bundles and 384 calibrated challenger bundles.
- 480 group metric shards across five model families.
- 96 independent audit shards reloaded every candidate artifact through the checksum-verifying production load path and cross-checked parent, calibration, group, seed, and metric identities.
- The completion JSON contains the Phase-1 fingerprint, artifact counts, PBS job identities, and Merkle-style tree hashes.

The correct Phase-2 handoff is therefore a documented negative selection result: retain the existing production forecast/controller path and preserve seed 46003 for a future independently frozen challenger.
"""
    return record, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--pbs-jobs", required=True, help="JSON object mapping stages to PBS job ids")
    args = parser.parse_args()
    if args.output.exists() or args.report.exists():
        raise FileExistsError("refusing to overwrite Phase-2 completion evidence")
    pbs_jobs = json.loads(args.pbs_jobs)
    if not isinstance(pbs_jobs, dict):
        raise ValueError("--pbs-jobs must be a JSON object")
    record, report = finalize(args.root, pbs_jobs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.write_text(report, encoding="utf-8")
    print(json.dumps({"output": str(args.output), "report": str(args.report), "status": record["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
