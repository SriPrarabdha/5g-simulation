"""Seal the Day-5 guarded-MPC development decision without touching validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json


EXPECTED_CANDIDATES = {
    "existing-baseline": "configs/cohort_mpc_control_science_baseline_v1.json",
    "empirical-survival": "configs/cohort_mpc_phase3_empirical_survival_v1.json",
    "scheduled-only": "configs/cohort_mpc_phase3_scheduled_only_v1.json",
    "failure-domain": "configs/cohort_mpc_phase3_failure_domain_v1.json",
    "conservative-combined": "configs/cohort_mpc_phase3_combined_v1.json",
    "calendar-optimistic-stale": "configs/cohort_mpc_phase3_calendar_optimistic_v1.json",
    "calendar-conservative": "configs/cohort_mpc_phase3_calendar_conservative_v1.json",
}
TUNING_CANDIDATES = {
    "scheduled-h2-t10": "configs/cohort_mpc_phase3_scheduled_h2_t10_v1.json",
    "scheduled-h3-t10": "configs/cohort_mpc_phase3_scheduled_h3_t10_v1.json",
    "scheduled-h6-t10": "configs/cohort_mpc_phase3_scheduled_h6_t10_v1.json",
    "scheduled-h3-t30": "configs/cohort_mpc_phase3_scheduled_h3_t30_v1.json",
    "scheduled-h6-t30": "configs/cohort_mpc_phase3_scheduled_h6_t30_v1.json",
    "calendar-conservative-h3-t30": "configs/cohort_mpc_phase3_calendar_conservative_h3_t30_v1.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(str(item.relative_to(path)).encode())
        digest.update(bytes.fromhex(hashlib.sha256(item.read_bytes()).hexdigest()))
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-root", required=True, type=Path)
    parser.add_argument("--interface-freeze", required=True, type=Path)
    parser.add_argument("--tuning-root", type=Path)
    parser.add_argument("--tuning-interface-freeze", type=Path)
    parser.add_argument("--survival-bundle", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--forecast-bundle", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    decision_path = args.output_root / "development-decision-v1.json"
    report_path = args.output_root / "REPORT.md"
    candidate_freeze = args.output_root / "candidate-freeze-v1.json"
    if decision_path.exists() or report_path.exists() or candidate_freeze.exists():
        raise FileExistsError("refusing to overwrite the Day-5 development decision")
    if (args.tuning_root is None) != (args.tuning_interface_freeze is None):
        parser.error("tuning root and tuning interface freeze must be supplied together")
    freeze_sha = _sha256(args.interface_freeze)
    evaluation_sets = [
        (args.evaluation_root, args.interface_freeze, freeze_sha, EXPECTED_CANDIDATES),
    ]
    if args.tuning_root is not None and args.tuning_interface_freeze is not None:
        evaluation_sets.append((
            args.tuning_root,
            args.tuning_interface_freeze,
            _sha256(args.tuning_interface_freeze),
            TUNING_CANDIDATES,
        ))
    candidates = []
    for evaluation_root, interface_freeze, expected_freeze_sha, expected_candidates in evaluation_sets:
        for name, profile_relative in expected_candidates.items():
            path = evaluation_root / name / "evaluation.json"
            if not path.is_file():
                raise FileNotFoundError(f"missing guarded development evaluation: {path}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("evaluation_stage") != "development":
                raise ValueError(f"candidate {name} is not a development evaluation")
            seeds = {int(row["seed"]) for row in payload.get("pairs", [])}
            if seeds != set(range(46101, 46113)) or len(payload["pairs"]) != 12:
                raise ValueError(f"candidate {name} does not cover development seeds exactly")
            if payload.get("interface_freeze", {}).get("sha256") != expected_freeze_sha:
                raise ValueError(f"candidate {name} did not use its Phase-3 interface freeze")
            candidates.append({
                "candidate": name,
                "profile": profile_relative,
                "interface_freeze_path": str(interface_freeze.resolve()),
                "interface_freeze_sha256": expected_freeze_sha,
                "evaluation_path": str(path.resolve()),
                "evaluation_sha256": _sha256(path),
                "mean_pair_ul_improvement": payload["mean_pair_ul_overload_area_relative_reduction"],
                "bootstrap_95_interval": payload["mean_pair_ul_reduction_bootstrap_95_interval"],
                "severity_weighted_ul_improvement": payload["weighted_total_ul_overload_area_relative_reduction"],
                "unknown_mixed_ul_improvement": payload["unknown_outage_mixed_ul_improvement"],
                "worst_pair_ul_improvement": payload["worst_pair_ul_overload_area_relative_reduction"],
                "normalized_churn": payload["normalized_mpc_churn_l1_per_group_decision"],
                "gates": payload["development_gates"],
                "passes": bool(payload["passes_day5_development_gate"]),
            })
    eligible = [row for row in candidates if row["passes"]]
    selected = max(eligible, key=lambda row: row["mean_pair_ul_improvement"]) if eligible else None
    decision = {
        "schema_version": "phase3-development-decision/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "development_seeds": [46101, 46112],
        "validation_seeds_consumed": False,
        "release_seeds_consumed": False,
        "phase3_interface_freezes": [
            {"path": str(path.resolve()), "sha256": expected_sha}
            for _, path, expected_sha, _ in evaluation_sets
        ],
        "candidates": candidates,
        "selected_candidate": selected["candidate"] if selected else None,
        "decision": "freeze_for_validation" if selected else "stop_and_retain_static",
        "static_remains_default": True,
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    atomic_json(decision_path, decision)
    lines = [
        "# Phase 3 Day-5 development decision", "",
        "| Candidate | Mean pair UL | Bootstrap 95% | Severity-weighted | Unknown/mixed | Worst pair | Pass |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in candidates:
        lines.append(
            f"| {row['candidate']} | {row['mean_pair_ul_improvement']:.2%} | "
            f"[{row['bootstrap_95_interval'][0]:.2%}, {row['bootstrap_95_interval'][1]:.2%}] | "
            f"{row['severity_weighted_ul_improvement']:.2%} | {row['unknown_mixed_ul_improvement']:.2%} | "
            f"{row['worst_pair_ul_improvement']:.2%} | {'yes' if row['passes'] else 'no'} |"
        )
    if selected:
        profile = Path(selected["profile"])
        freeze = {
            "schema_version": "mpc-development-candidate-freeze/1.0",
            "frozen": True,
            "validation_seeds_untouched": [46201, 46216],
            "release_seeds_untouched": [46301, 46330],
            "artifacts": {
                "manifest": {"path": str(args.manifest.resolve()), "sha256": _sha256(args.manifest)},
                "mpc_profile": {"path": str(profile.resolve()), "sha256": _sha256(profile)},
                "forecast_bundle": {"path": str(args.forecast_bundle.resolve()), "sha256": _sha256(args.forecast_bundle)},
                "survival_bundle": {"path": str(args.survival_bundle.resolve()), "sha256": _sha256(args.survival_bundle)},
                "interface_freeze": {
                    "path": selected["interface_freeze_path"],
                    "sha256": selected["interface_freeze_sha256"],
                },
                "development_evaluation": {
                    "path": selected["evaluation_path"], "sha256": selected["evaluation_sha256"],
                },
            },
        }
        atomic_json(candidate_freeze, freeze)
        lines.extend(["", f"Decision: freeze **{selected['candidate']}** for Day-6 validation. Static remains the default controller."])
    else:
        lines.extend(["", "Decision: no candidate passed every frozen gate. Stop before validation and retain Static."])
    lines.extend(["", "Seeds 46201–46216 and 46301–46330 remain untouched.", ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "decision": decision["decision"], "selected": decision["selected_candidate"],
        "output": str(decision_path.resolve()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
