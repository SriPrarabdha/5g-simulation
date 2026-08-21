"""Pre-register and content-seal the Phase 3.2 development interface."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json
from experiments.seed_policy import reject_protected_mpc_seeds


INTERFACES = (
    "configs/phase32_candidates_v1.json",
    "experiments/evaluate_cohort_mpc_pilot.py",
    "experiments/freeze_phase32.py",
    "experiments/seed_policy.py",
    "optimization/__init__.py",
    "optimization/predrain_flow.py",
    "pbs/phase32_candidate_matrix_v1.pbs",
    "scripts/run_phase31_candidate_matrix.py",
    "scripts/run_phase32_candidate_matrix.py",
    "simulator/macro/config.py",
    "simulator/macro/controllers.py",
    "simulator/macro/engine.py",
)

GATES = (
    "mean_pair_ul_improvement_at_least_10_percent",
    "bootstrap_lower_bound_above_zero",
    "positive_severity_weighted_improvement",
    "unknown_mixed_regression_no_worse_than_minus_2_percent",
    "worst_pair_better_than_minus_10_percent",
    "no_dl_overload_drop_or_establishment_regression",
    "no_solver_timeout_or_error",
    "unexpected_fallback_fraction_within_1_percent",
    "skipped_decision_fraction_within_95_percent",
    "normalized_churn_within_0_05_l1_per_group_decision",
    "measured_empirical_survival_robustness",
    "end_to_end_decision_latency_within_candidate_deadline",
    "zero_predicted_overflow",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite frozen interface: {args.output}")
    missing = [path for path in INTERFACES if not (PROJECT_ROOT / path).is_file()]
    if missing:
        raise FileNotFoundError(f"Phase 3.2 interfaces are missing: {missing}")

    fresh = list(range(46449, 46473))
    reject_protected_mpc_seeds(fresh)
    prior_seeds: set[int] = set()
    prior_decisions = (
        "output/control-science/v1/phase3.1-development-v1/DEVELOPMENT_DECISION.json",
        "output/control-science/v1/phase3.1-development-v2/DEVELOPMENT_DECISION.json",
    )
    for relative in prior_decisions:
        prior = json.loads((PROJECT_ROOT / relative).read_text(encoding="utf-8"))
        prior_seeds.update(int(seed) for seed in prior["fresh_development_seeds"])
    if prior_seeds.intersection(fresh):
        raise ValueError("Phase 3.2 development seeds collide with a prior campaign")

    matrix_path = "configs/phase32_candidates_v1.json"
    matrix = json.loads((PROJECT_ROOT / matrix_path).read_text(encoding="utf-8"))
    manifest_path = "output/manifests/stage1-extreme-packing-1d-s20260817.json"
    payload = {
        "schema_version": "phase3.2-interface-freeze/1.0",
        "campaign_schema": "phase3.2",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "frozen": True,
        "development_only": True,
        "production_controller": "static-capacity-v1",
        "fresh_development_seeds": fresh,
        "prior_development_seed_count": len(prior_seeds),
        "seed_collision_check": "passed",
        "protected_validation_seeds": list(range(46201, 46217)),
        "protected_release_seeds": list(range(46301, 46331)),
        "forecast_seed_46003_state": "generated_and_sealed_but_untouched_by_model_evaluation_or_selection",
        "promotion_logic": f"all_{len(GATES)}_gates_must_pass",
        "promotion_gates": list(GATES),
        "candidate_matrix": {
            "path": matrix_path,
            "paired_runs": len(fresh) * matrix["candidate_count"],
            "simulated_days_per_pair": 1.0,
            "static_pairing": True,
        },
        "interfaces": {
            relative: _sha256(PROJECT_ROOT / relative) for relative in INTERFACES
        },
        "immutable_inputs": {
            "manifest": {
                "path": manifest_path,
                "sha256": _sha256(PROJECT_ROOT / manifest_path),
            },
            "prior_decisions": {
                relative: {"path": relative, "sha256": _sha256(PROJECT_ROOT / relative)}
                for relative in prior_decisions
            },
        },
    }
    atomic_json(args.output, payload)
    print(json.dumps({
        "output": str(args.output.resolve()), "interfaces": len(INTERFACES),
        "paired_runs": payload["candidate_matrix"]["paired_runs"],
        "sha256": _sha256(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
