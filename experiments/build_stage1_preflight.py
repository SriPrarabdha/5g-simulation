from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import atomic_json


EXPECTED_CONTROLLERS = {"static-capacity-v1", "reactive-threshold-v1", "cohort-mpc-v1"}
EXPECTED_TIERS = {"bronze", "silver", "gold"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(memory: dict[str, Any], profile: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if memory.get("schema_version") != "stage1-memory-regression/1.1" or not memory.get("passed"):
        reasons.append("memory_regression_missing_or_failed")
    if (
        int(memory.get("warmup_days", -1)) < int(memory.get("required_warmup_days", 0))
        or memory.get("measurement_days") != [1, 7]
    ):
        reasons.append("memory_regression_steady_state_window_invalid")
    if profile.get("schema_version") != "stage1-sequential-profile/1.1":
        reasons.append("sequential_profile_schema_invalid")
    profile_rows = profile.get("runs", [])
    if {row.get("controller") for row in profile_rows} != EXPECTED_CONTROLLERS:
        reasons.append("sequential_profile_controller_matrix_incomplete")
    if any(int(row.get("peak_rss_bytes", 0)) <= 0 or float(row.get("wall_seconds", 0)) <= 0 for row in profile_rows):
        reasons.append("sequential_profile_metrics_invalid")

    if artifacts.get("schema_version") != "stage1-artifact-characterization/1.1":
        reasons.append("artifact_characterization_schema_invalid")
    if not profile.get("inputs") or profile.get("inputs") != artifacts.get("inputs"):
        reasons.append("characterization_input_identities_mismatch")
    if not profile.get("topology_id") or profile.get("topology_id") != artifacts.get("topology_id"):
        reasons.append("characterization_topology_mismatch")
    if (
        not profile.get("source_fingerprint")
        or profile.get("source_fingerprint") != artifacts.get("source_fingerprint")
    ):
        reasons.append("characterization_source_fingerprint_mismatch")
    artifact_rows = artifacts.get("runs", [])
    matrix = {(row.get("tier"), row.get("controller")) for row in artifact_rows}
    if matrix != {(tier, controller) for tier in EXPECTED_TIERS for controller in EXPECTED_CONTROLLERS}:
        reasons.append("artifact_tier_controller_matrix_incomplete")
    for row in artifact_rows:
        kinds = {item.get("kind") for item in row.get("artifacts", [])}
        required = {"summary", "audit_counts", "provenance", "performance"}
        if row.get("tier") in {"silver", "gold"}:
            required.add("detailed_steps")
        if row.get("tier") == "gold":
            required.update({"selection_audits", "decision_traces"})
        if not required <= kinds or "debug_jsonl" in kinds:
            reasons.append(f"artifact_contract_invalid:{row.get('tier')}:{row.get('controller')}")
    return {
        "schema_version": "stage1-precharacterization-gate/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "passed" if not reasons else "failed",
        "reasons": sorted(set(reasons)),
        "memory_regression": memory,
        "sequential_profile": profile,
        "artifact_characterization": artifacts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate the Stage 1 packing ladder on extreme prerequisites")
    parser.add_argument("--memory", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    paths = (args.memory, args.profile, args.artifacts)
    report = build(*(json.loads(path.read_text(encoding="utf-8")) for path in paths))
    report["input_sha256"] = {path.name: _sha256(path) for path in paths}
    atomic_json(args.output, report)
    print(json.dumps({"output": str(args.output), "status": report["status"], "reasons": report["reasons"]}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
