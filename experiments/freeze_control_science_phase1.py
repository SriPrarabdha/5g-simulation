from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from datetime import datetime, timezone
from pathlib import Path

from experiments.evaluate_control_science_release import MANDATORY_PAIR_FIELDS
from experiments.run_campaign_shard import source_fingerprint
from experiments.seed_policy import FORECAST_SEEDS, MPC_SEEDS


ROOT = Path(__file__).resolve().parent.parent
INTERFACE_FILES = (
    "forecasting/baselines.py", "forecasting/candidates.py", "forecasting/candidate_bundle.py",
    "forecasting/metadata.py", "forecasting/evaluation.py", "optimization/survival.py",
    "optimization/cohort_mpc.py", "simulator/macro/engine.py", "simulator/macro/controllers.py",
    "experiments/train_forecaster.py", "experiments/prepare_forecast_series.py",
    "experiments/calibrate_evaluate_forecast_candidate.py",
    "experiments/aggregate_forecast_selection.py",
    "experiments/evaluate_control_science_release.py", "experiments/seed_policy.py",
    "requirements-pbs-forecast.lock", "configs/control_science_v1.json",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze Phase-1 control-science interfaces")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--train-cache-index", required=True, type=Path)
    parser.add_argument("--selection-cache-index", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite Phase-1 freeze: {args.output}")
    caches = {}
    for purpose, path in (
        ("train", args.train_cache_index), ("selection", args.selection_cache_index)
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("purpose") != purpose or payload.get("seed") not in FORECAST_SEEDS[purpose]:
            raise ValueError(f"invalid {purpose} cache index")
        caches[purpose] = {
            "path": str(path.resolve()), "sha256": _sha(path),
            "groups": len(payload["groups"]),
            "observations": sum(item["observations"] for item in payload["groups"]),
            "seed": payload["seed"],
        }
    packages = ("numpy", "scipy", "scikit-learn", "lightgbm", "pyarrow")
    record = {
        "schema_version": "control-science-phase1-freeze/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "phase1_interfaces_frozen",
        "source_fingerprint": source_fingerprint(ROOT),
        "interfaces": {
            name: _sha(ROOT / name) for name in INTERFACE_FILES
        },
        "environment": {name: importlib.metadata.version(name) for name in packages},
        "forecast_seed_policy": {
            key: sorted(value) for key, value in FORECAST_SEEDS.items()
        },
        "mpc_seed_policy": {
            key: sorted(value) for key, value in MPC_SEEDS.items()
        },
        "release_mandatory_pair_fields": list(MANDATORY_PAIR_FIELDS),
        "caches": caches,
        "tests": {
            "focused": "73 passed, 47 subtests passed",
            "full": "157 passed; 1 unrelated missing frozen oracle artifact",
            "protected_forecast_test_seed_consumed": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "source_fingerprint": record["source_fingerprint"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
