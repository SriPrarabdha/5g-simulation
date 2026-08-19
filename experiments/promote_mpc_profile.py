from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import atomic_json


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def promote(candidate_path: Path, evaluation_path: Path, profile_id: str) -> dict[str, Any]:
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    if candidate.get("schema_version") != "cohort-mpc-profile/1.0":
        raise ValueError("unsupported MPC candidate profile")
    if not candidate.get("development_only"):
        raise ValueError("candidate is already a production profile")
    if evaluation.get("schema_version") != "cohort-mpc-10pct-candidate-evaluation/1.0":
        raise ValueError("unsupported MPC candidate evaluation")
    if evaluation.get("reserved_seeds_consumed") is not False:
        raise ValueError("evaluation consumed reserved seeds or omitted seed provenance")
    if not evaluation.get("reaches_10_percent_gate") or evaluation.get("decision") != "advance_to_full_campaign":
        raise ValueError("MPC candidate did not pass the held-out 10% gate")
    if int(evaluation.get("paired_runs", 0)) < 30:
        raise ValueError("MPC promotion requires at least 30 held-out paired runs")
    if float(evaluation.get("simulated_days_per_pair", 0)) < 1.0:
        raise ValueError("MPC promotion requires at least one simulated day per pair")
    required_scenarios = {"surge", "scheduled_fault", "unannounced_outage", "mixed_stress"}
    if set(evaluation.get("by_scenario", {})) != required_scenarios:
        raise ValueError("MPC promotion requires the complete four-scenario matrix")
    guardrails = evaluation.get("aggregate_guardrails", {})
    if not guardrails or not all(guardrails.values()):
        raise ValueError("MPC candidate failed an aggregate safety guardrail")
    identity = evaluation.get("mpc_profile", {})
    if identity.get("sha256") != _sha256(candidate_path):
        raise ValueError("evaluation does not identify the supplied MPC candidate")
    if not profile_id or profile_id == candidate.get("profile_id"):
        raise ValueError("promotion requires a new non-empty profile_id")
    promoted = dict(candidate)
    promoted["profile_id"] = profile_id
    promoted["development_only"] = False
    promoted["promotion"] = {
        "schema_version": "cohort-mpc-promotion/1.0",
        "promoted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "candidate_profile_id": candidate["profile_id"],
        "candidate_sha256": _sha256(candidate_path),
        "evaluation_path": str(evaluation_path.resolve()),
        "evaluation_sha256": _sha256(evaluation_path),
        "paired_runs": evaluation.get("paired_runs"),
        "mean_pair_ul_overload_area_relative_reduction": evaluation.get(
            "mean_pair_ul_overload_area_relative_reduction"
        ),
        "mean_pair_ul_reduction_bootstrap_95_interval": evaluation.get(
            "mean_pair_ul_reduction_bootstrap_95_interval"
        ),
        "aggregate_guardrails": guardrails,
    }
    return promoted


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote a held-out-validated MPC candidate")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--evaluation", required=True, type=Path)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite promoted MPC profile: {args.output}")
    payload = promote(args.candidate, args.evaluation, args.profile_id)
    atomic_json(args.output, payload)
    print(json.dumps({"output": str(args.output), "profile_id": payload["profile_id"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
