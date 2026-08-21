"""Seal the post-Phase-3.2 safety corrections used by the C-DOT v6 package."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from experiments.artifacts import atomic_json


ROOT = Path(__file__).resolve().parent.parent
INTERFACES = (
    "experiments/freeze_phase3_cdot_v6.py",
    "experiments/freeze_phase31.py",
    "experiments/freeze_phase32.py",
    "optimization/predrain_flow.py",
    "schemas/policy.py",
    "scripts/build_phase3_cdot_showcase.py",
    "scripts/build_phase3_cdot_showcase_v5.py",
    "scripts/build_phase3_cdot_showcase_v6.py",
    "scripts/run_phase31_candidate_matrix.py",
    "simulator/macro/controllers.py",
    "tests/test_control_science.py",
    "tests/test_simulator.py",
)
ORACLE_EVIDENCE = "output/models/extreme-oracle-bound-evaluation-v1.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite v6 interface freeze: {args.output}")
    required = [ROOT / relative for relative in (*INTERFACES, ORACLE_EVIDENCE)]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"v6 correction inputs are missing: {missing}")
    oracle = json.loads((ROOT / ORACLE_EVIDENCE).read_text(encoding="utf-8"))
    if (
        oracle.get("schema_version") != "oracle-bound-evaluation/1.0"
        or oracle.get("decision")
        != "new_session_gate_reachable_in_continuous_relaxation"
        or oracle.get("test_seeds_consumed") is not False
    ):
        raise ValueError("restored oracle evidence does not satisfy the sealed contract")
    payload = {
        "schema_version": "phase3-cdot-v6-interface-freeze/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "frozen": True,
        "production_controller": "static-capacity-v1",
        "candidate_mode": "guarded_shadow_or_replay_only",
        "protected_validation_seeds_consumed": False,
        "protected_release_seeds_consumed": False,
        "corrections": {
            "predrain_overflow": "fail_closed_to_static_with_resource_slack",
            "promotion_gate": "zero_predicted_overflow",
            "run_inventory": {
                "declared_candidate_pairs": 516,
                "declared_candidate_configurations": 28,
                "survival_sensitivity_controller_pairs": 72,
                "controller_pairs_total": 588,
            },
            "unknown_mixed_label": "combined_severity_weighted_unknown_plus_mixed",
            "latency_context": "campaign_saturation_not_isolated_control_plane_benchmark",
        },
        "interfaces": {
            relative: _sha256(ROOT / relative) for relative in INTERFACES
        },
        "oracle_evidence": {
            "path": ORACLE_EVIDENCE,
            "sha256": _sha256(ROOT / ORACLE_EVIDENCE),
            "source_job_output": (
                "/home/abharadwaj/5g-stage1/overnight-20260817/oracle/"
                "evaluation/oracle-bound-evaluation-s036001-s036002.json"
            ),
        },
    }
    atomic_json(args.output, payload)
    print(json.dumps({
        "output": str(args.output.resolve()),
        "interfaces": len(INTERFACES),
        "sha256": _sha256(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
