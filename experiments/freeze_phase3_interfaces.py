"""Freeze code/config interfaces before any Phase-3 development execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json


INTERFACES = (
    "optimization/survival.py",
    "optimization/cohort_mpc.py",
    "simulator/macro/controllers.py",
    "simulator/macro/engine.py",
    "simulator/macro/config.py",
    "forecasting/metadata.py",
    "experiments/evaluate_control_science_release.py",
    "experiments/evaluate_survival_guardrail.py",
    "experiments/build_empirical_survival_phase3.py",
    "scripts/run_mpc_candidate_parallel.py",
    "scripts/run_mpc_release_once.py",
    "pbs/evaluate_mpc_candidate.pbs",
    "pbs/run_mpc_release_once.pbs",
    "pbs/phase3_survival_comparison_array.pbs",
    "pbs/phase3_guarded_development_array.pbs",
    "pbs/phase3_scheduled_solver_tuning_array.pbs",
)
PROFILES = (
    "configs/cohort_mpc_control_science_baseline_v1.json",
    "configs/cohort_mpc_phase3_empirical_survival_v1.json",
    "configs/cohort_mpc_phase3_scheduled_only_v1.json",
    "configs/cohort_mpc_phase3_failure_domain_v1.json",
    "configs/cohort_mpc_phase3_combined_v1.json",
    "configs/cohort_mpc_phase3_calendar_optimistic_v1.json",
    "configs/cohort_mpc_phase3_calendar_conservative_v1.json",
    "configs/cohort_mpc_phase3_scheduled_h2_t10_v1.json",
    "configs/cohort_mpc_phase3_scheduled_h3_t10_v1.json",
    "configs/cohort_mpc_phase3_scheduled_h6_t10_v1.json",
    "configs/cohort_mpc_phase3_scheduled_h3_t30_v1.json",
    "configs/cohort_mpc_phase3_scheduled_h6_t30_v1.json",
    "configs/cohort_mpc_phase3_calendar_conservative_h3_t30_v1.json",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase21-addendum", required=True, type=Path)
    parser.add_argument("--survival-calibration", required=True, type=Path)
    parser.add_argument("--survival-guardrail", required=True, type=Path)
    parser.add_argument("--guarded-survival-bundle", required=True, type=Path)
    parser.add_argument("--supersedes", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite Phase-3 interface freeze")
    paths = [PROJECT_ROOT / path for path in (*INTERFACES, *PROFILES)]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Phase-3 freeze inputs are missing: {missing}")
    payload = {
        "schema_version": "phase3-interface-freeze/1.0",
        "frozen": True,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "protected_seed_policy": {
            "development": [46101, 46112],
            "validation_untouched": [46201, 46216],
            "release_untouched": [46301, 46330],
            "forecast_test_46003_untouched": True,
        },
        "interfaces": {
            str(path.relative_to(PROJECT_ROOT)): sha256(path) for path in paths
        },
        "evidence": {
            "phase21_addendum": {
                "path": str(args.phase21_addendum.resolve()),
                "sha256": sha256(args.phase21_addendum),
            },
            "survival_calibration": {
                "path": str(args.survival_calibration.resolve()),
                "sha256": sha256(args.survival_calibration),
            },
            "survival_guardrail": {
                "path": str(args.survival_guardrail.resolve()),
                "sha256": sha256(args.survival_guardrail),
            },
            "guarded_survival_bundle": {
                "path": str(args.guarded_survival_bundle.resolve()),
                "sha256": sha256(args.guarded_survival_bundle),
            },
        },
    }
    if args.supersedes is not None:
        payload["supersedes"] = {
            "path": str(args.supersedes.resolve()),
            "sha256": sha256(args.supersedes),
        }
    atomic_json(args.output, payload)
    print(json.dumps({"output": str(args.output.resolve()), "interfaces": len(paths)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
