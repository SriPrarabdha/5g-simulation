"""Pre-register and content-seal the Phase 3.1 development interface."""

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


BASE_INTERFACES = (
    "configs/cohort_mpc_phase31_operational_h3_v1.json",
    "configs/cohort_mpc_phase31_operational_h6_v1.json",
    "experiments/evaluate_cohort_mpc_pilot.py",
    "experiments/evaluate_distribution_blind_survival.py",
    "experiments/fit_survival_from_lifecycle.py",
    "experiments/seed_policy.py",
    "optimization/__init__.py",
    "optimization/cohort_mpc.py",
    "optimization/predrain_flow.py",
    "optimization/survival.py",
    "pbs/phase31_survival_matrix.pbs",
    "scripts/run_distribution_blind_survival_parallel.py",
    "scripts/run_phase31_candidate_matrix.py",
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
    "zero_predicted_overflow",
    "skipped_decision_fraction_within_95_percent",
    "normalized_churn_within_0_05_l1_per_group_decision",
    "measured_empirical_survival_robustness",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-matrix", default="configs/phase31_candidates_v1.json")
    parser.add_argument("--candidate-pbs", default="pbs/phase31_candidate_matrix.pbs")
    parser.add_argument("--seed-start", type=int, default=46401)
    parser.add_argument("--seed-count", type=int, default=24)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite frozen interface: {args.output}")
    interfaces = BASE_INTERFACES + (args.candidate_matrix, args.candidate_pbs)
    missing = [relative for relative in interfaces if not (PROJECT_ROOT / relative).is_file()]
    if missing:
        raise FileNotFoundError(f"Phase 3.1 interfaces are missing: {missing}")
    matrix_payload = json.loads(
        (PROJECT_ROOT / args.candidate_matrix).read_text(encoding="utf-8")
    )
    candidate_inputs = sorted({
        candidate[key]
        for candidate in matrix_payload["candidates"]
        for key in ("profile", "forecast_bundle", "survival_bundle")
        if candidate.get(key) is not None
    })
    missing_inputs = [
        relative for relative in candidate_inputs
        if not (PROJECT_ROOT / relative).is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(f"Phase 3.1 candidate inputs are missing: {missing_inputs}")
    payload = {
        "schema_version": "phase3.1-interface-freeze/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "frozen": True,
        "development_only": True,
        "production_controller": "static-capacity-v1",
        "fresh_development_seeds": list(range(args.seed_start, args.seed_start + args.seed_count)),
        "survival_auditor_seeds": list(range(46501, 46526)),
        "protected_validation_seeds": list(range(46201, 46217)),
        "protected_release_seeds": list(range(46301, 46331)),
        "forecast_seed_46003_state": "generated_and_sealed_but_untouched_by_model_evaluation_or_selection",
        "promotion_logic": f"all_{len(GATES)}_gates_must_pass",
        "promotion_gates": list(GATES),
        "survival_language": {
            "phase3_historical": "Kaplan-Meier validation on synthetically generated censored lifecycle telemetry",
            "phase31": "distribution-blind fitting from exported start/end/censor lifecycle records",
            "robustness": "relative_survival_equivalence_not_operational_acceptability",
        },
        "survival_matrix": {
            "distributions_auditor_only": [
                "uniform", "weibull", "lognormal", "heavy-tail-mixture", "drift"
            ],
            "trials": 125,
            "fit_contract": "lifecycle-export/1.0",
            "candidate_inputs_after_dependency": [
                "output/control-science/v1/phase3.1-survival-v1/lognormal/seed-46501/survival.json",
                "output/control-science/v1/phase3.1-survival-v1/heavy-tail-mixture/seed-46501/survival.json",
            ],
        },
        "candidate_matrix": {
            "path": args.candidate_matrix,
            "paired_runs": args.seed_count * matrix_payload["candidate_count"],
            "simulated_days_per_pair": 1.0,
            "static_pairing": True,
        },
        "interfaces": {
            relative: _sha256(PROJECT_ROOT / relative) for relative in interfaces
        },
        "immutable_inputs": {
            "manifest": {
                "path": "output/manifests/stage1-extreme-packing-1d-s20260817.json",
                "sha256": _sha256(PROJECT_ROOT / "output/manifests/stage1-extreme-packing-1d-s20260817.json"),
            },
            "candidate_inputs": {
                relative: {
                    "path": relative,
                    "sha256": _sha256(PROJECT_ROOT / relative),
                }
                for relative in candidate_inputs
            },
        },
    }
    atomic_json(args.output, payload)
    print(json.dumps({
        "output": str(args.output.resolve()),
        "interfaces": len(interfaces),
        "sha256": _sha256(args.output),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
