#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json


DEFAULTS = {
    "guided": PROJECT_ROOT / "demo_api/data/cohort_mpc_full_campaign_evidence_v1.json",
    "demo_scenario": PROJECT_ROOT / "configs/demo_mpc_scenario.json",
    "ma6": Path("/home/abharadwaj/5g-stage1/extreme/mpc-evaluation/candidate-v2/evaluation.json"),
    "fourteen_day": Path("/home/abharadwaj/5g-stage1/overnight-20260817/forecaster-14d/mpc-evaluation/candidate-14d-v1/evaluation.json"),
    "forecast_7d": PROJECT_ROOT / "output/stage1/models/extreme-forecaster-7d-s20260817.freeze.json",
    "forecast_14d": PROJECT_ROOT / "output/stage1/models/extreme-forecaster-14d-s20260818.freeze.json",
    "forecast_baselines_14d": Path("/home/abharadwaj/5g-stage1/overnight-20260817/forecaster-14d/evaluation/baseline-evaluation.json"),
    "oracle": Path("/home/abharadwaj/5g-stage1/overnight-20260817/oracle/evaluation/oracle-bound-evaluation-s036001-s036002.json"),
    "packing": Path("/home/abharadwaj/5g-stage1/extreme/packing/characterization/stage1-report.json"),
    "two_node": Path("/home/abharadwaj/5g-stage1/stage2-smoke/multinode/stage2-array-demo-2node-v1/combined.json"),
    "worklist_2": Path("/home/abharadwaj/5g-stage1/overnight-20260817/production/worklists/extreme-2node.json"),
    "worklist_4": Path("/home/abharadwaj/5g-stage1/overnight-20260817/production/worklists/extreme-4node.json"),
    "worklist_12": Path("/home/abharadwaj/5g-stage1/overnight-20260817/production/worklists/extreme-12node.json"),
    "realism": PROJECT_ROOT / "output/delhi/traffic-realism-v2-evaluation.json",
    "performance": PROJECT_ROOT / "output/delhi/traffic-v2-performance.json",
    "v2_scenario": PROJECT_ROOT / "configs/delhi_traffic_v2.json",
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": str(path.resolve()), "sha256": _sha(path), "bytes": path.stat().st_size}


def _seeds(evaluation: dict[str, Any]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for pair in evaluation.get("pairs", []):
        result.setdefault(pair["scenario_kind"], []).append(int(pair["seed"]))
    return {key: sorted(values) for key, values in sorted(result.items())}


def _claim(item_id: str, classification: str, statement: str, source_key: str | None) -> dict[str, Any]:
    return {"id": item_id, "classification": classification, "statement": statement,
            "source_key": source_key}


def build(paths: dict[str, Path]) -> dict[str, Any]:
    data = {key: _read(path) for key, path in paths.items()}
    guided = data["guided"]; ma6 = data["ma6"]; fourteen = data["fourteen_day"]
    forecast_7d = data["forecast_7d"]["held_out_evaluation"]
    forecast_14d = data["forecast_14d"]["held_out_evaluation"]
    oracle = data["oracle"]; packing = data["packing"]; realism = data["realism"]
    performance = data["performance"]
    oracle_by_regime: dict[str, list[float]] = {}
    for scenario in oracle["scenarios"]:
        for bound in scenario["bounds"]:
            oracle_by_regime.setdefault(bound["regime"], []).append(
                bound["ul_overload_area_relative_reduction"]
            )
    rungs = []
    for rung in packing["rungs"]:
        metrics = [item["metrics"] for item in rung["repetitions"]]
        rungs.append({
            "workers": rung["worker_count"], "passed": rung["passed"],
            "rejected_reasons": rung.get("rejected_reasons", []),
            "cpu_efficiency_median": statistics.median(item["cpu_efficiency"] for item in metrics) if metrics else None,
            "memory_fraction_median": statistics.median(
                item["aggregate_peak_rss_bytes"] / item["allocated_memory_bytes"] for item in metrics
            ) if metrics else None,
            "scratch_fraction_median": statistics.median(
                item["scratch_peak_bytes"] / item["scratch_allocation_bytes"] for item in metrics
            ) if metrics else None,
            "failures": sum(int(item["failures"]) for item in metrics),
        })
    cpu_gate_values = [
        int(match.group(1)) / 100
        for rung in packing["rungs"] for reason in rung.get("rejected_reasons", [])
        if (match := re.search(r"cpu_efficiency_below_(\d+)_percent", reason))
    ]
    gate_key = next(key for key in ma6 if re.fullmatch(r"reaches_\d+_percent_gate", key))
    mean_pair_gate = int(gate_key.split("_")[1]) / 100
    sources = {key: _source(path) for key, path in paths.items()}
    display = {
        "live_demo": {"upfs": len(data["demo_scenario"]["upfs"]),
                      "groups": len(data["demo_scenario"]["groups"]),
                      "step_seconds": data["demo_scenario"].get("step_seconds", 30)},
        "scale": realism["scale"],
        "guided_campaign": {
            "campaign_id": guided["campaign_id"], "pairs": guided["paired_runs"],
            "mean_pair_reduction": guided["mean_pair_relative_reduction"],
            "severity_weighted_reduction": guided["weighted_total_relative_reduction"],
            "worst_pair_reduction": guided["worst_pair_relative_reduction"],
            "confidence_interval": guided["bootstrap_95_interval"],
        },
        "national_ma6": {
            "pairs": ma6["paired_runs"],
            "mean_pair_reduction": ma6["mean_pair_ul_overload_area_relative_reduction"],
            "severity_weighted_reduction": ma6["weighted_total_ul_overload_area_relative_reduction"],
            "worst_pair_reduction": ma6["worst_pair_ul_overload_area_relative_reduction"],
            "confidence_interval": ma6["mean_pair_ul_reduction_bootstrap_95_interval"],
            "decision": ma6["decision"], "by_scenario": ma6["by_scenario"],
            "mean_pair_gate": mean_pair_gate,
            "pairs_detail": [{
                "scenario": pair["scenario_kind"], "seed": pair["seed"],
                "relative_reduction": pair["relative_reduction"]["overload_area_seconds"]["ul"],
            } for pair in ma6["pairs"]],
        },
        "fourteen_day_control": {
            "pairs": fourteen["paired_runs"],
            "mean_pair_reduction": fourteen["mean_pair_ul_overload_area_relative_reduction"],
            "severity_weighted_reduction": fourteen["weighted_total_ul_overload_area_relative_reduction"],
            "worst_pair_reduction": fourteen["worst_pair_ul_overload_area_relative_reduction"],
            "confidence_interval": fourteen["mean_pair_ul_reduction_bootstrap_95_interval"],
            "decision": fourteen["decision"],
        },
        "forecast_models": {
            "seven_day": {"overall": forecast_7d["overall"], "by_horizon": forecast_7d["by_horizon"]},
            "fourteen_day": {"overall": forecast_14d["overall"], "by_horizon": forecast_14d["by_horizon"]},
            "causal_baselines": {"by_horizon": data["forecast_baselines_14d"]["aggregate"]["by_horizon"],
                                 "overall": data["forecast_baselines_14d"]["aggregate"]["overall"]},
        },
        "oracle_information_ladder": [
            {"regime": regime, "minimum": min(values), "maximum": max(values), "mean": statistics.mean(values)}
            for regime, values in oracle_by_regime.items()
        ],
        "packing_ladder": {"selected_workers": packing["selected_worker_count"],
                           "cpu_efficiency_gate": min(cpu_gate_values), "rungs": rungs},
        "multinode": {
            "validated": [{
                "nodes": data["two_node"]["node_count"], "workers": data["two_node"]["total_worker_count"],
                "work_items": data["two_node"]["work_items"], "failures": data["two_node"]["failures"],
                "cpu_efficiency": data["two_node"]["cpu_efficiency"],
            }],
            "pending_node_counts": [2, 4, 12],
            "publication_rule": "walltime/throughput/CPU/memory chart is withheld until every 2→4→12 pilot report passes",
        },
        "realism": {
            "scorecard": {
                "structurally_aligned": len(data["v2_scenario"]["upfs"]) == realism["scale"]["upfs"],
                "statistically_verified": all(realism["distribution_fidelity"]["acceptance"].values()),
                "scale_tested": realism["population"]["conserved_exactly"] and realism["accounting"]["passed"],
                "performance_target_met": performance["passed"],
                "operator_calibrated": False,
            },
            "performance": performance,
            "distribution_fidelity": realism["distribution_fidelity"],
            "population": realism["population"], "traffic_fingerprint": realism["traffic_fingerprint"],
            "representative_upfs": realism["representative_upfs"], "events": realism["events"],
            "accounting": realism["accounting"], "telemetry_pathology": realism["telemetry_pathology"],
            "controllability_surface": realism["controllability_surface"],
        },
        "v2_controller_pilot": {
            "status": "not_run", "required_pairs": 16,
            "advance_gate": {"mean_pair_ul_reduction": .10, "severity_weighted_positive": True,
                             "guardrails_no_regression": True},
            "accepted_v1_profile_unchanged": True,
        },
    }
    claims = [
        _claim("live-loop", "live", f"The {len(data['demo_scenario']['upfs'])}-UPF runtime is causal and steers only future sessions.", "demo_scenario"),
        _claim("guided-result", "measured-synthetic", f"The guided campaign improves mean-pair UL overload area by {guided['mean_pair_relative_reduction']*100:.2f}%.", "guided"),
        _claim("national-result", "measured-synthetic", "The 30-pair national MA6 MPC result passes its campaign gate with visible fault tails.", "ma6"),
        _claim("forecast-control-gap", "measured-synthetic", "The 14-day forecaster is more accurate but its MPC pairing fails the control gate.", "fourteen_day"),
        _claim("oracle-headroom", "modeled-projection",
               f"Continuous oracle relaxations show {min(oracle_by_regime['arrival_only'])*100:.0f}–{max(oracle_by_regime['scheduled_fault'])*100:.0f}% causal/scheduled and {min(oracle_by_regime['clairvoyant_fault'])*100:.0f}% clairvoyant headroom.",
               "oracle"),
        _claim("realism-v2", "measured-synthetic", realism["claim_boundary"], "realism"),
        _claim("smf-hook", "external-pending", "Prometheus mapping and a supported SMF/EMS new-session hook remain C-DOT integration work.", None),
        _claim("scale-2-4-12", "external-pending", "2→4→12-node performance is withheld until all pilot reports pass.", "worklist_12"),
        _claim("v2-controller-pilot", "external-pending", "The fresh 16-pair v2 controller pilot has not been run; accepted v1 evidence remains unchanged.", "v2_scenario"),
    ]
    return {
        "schema_version": "presentation-evidence-manifest/1.0",
        "presentation_id": "CDOT_Predictive_UPF_Steering_Delhi_2026",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evidence_labels": ["live", "measured-synthetic", "modeled-projection", "external-pending"],
        "claim_boundary": realism["claim_boundary"],
        "sources": sources,
        "experiments": {
            "guided": {"identity": guided["campaign_id"], "seed_matrix": None,
                       "profile": guided["mpc_profile"], "source_fingerprint": sources["guided"]["sha256"]},
            "national_ma6": {"identity": "national-ma6-mpc-30-pair", "seed_matrix": _seeds(ma6),
                             "profile": ma6["mpc_profile"], "source_fingerprint": ma6["manifest"]["sha256"]},
            "fourteen_day": {"identity": "national-14d-forecaster-mpc-30-pair", "seed_matrix": _seeds(fourteen),
                             "profile": fourteen["mpc_profile"], "source_fingerprint": fourteen["manifest"]["sha256"]},
            "traffic_v2": {"identity": realism["scenario_id"], "seed_matrix": [realism["seed"]],
                           "profile": "traffic-model/2.0", "source_fingerprint": sources["v2_scenario"]["sha256"]},
        },
        "claims": claims, "display": display,
    }


def validate(manifest: dict[str, Any]) -> None:
    labels = set(manifest["evidence_labels"])
    for claim in manifest["claims"]:
        if claim["classification"] not in labels:
            raise ValueError(f"unknown evidence label for {claim['id']}")
        key = claim.get("source_key")
        if key is not None and key not in manifest["sources"]:
            raise ValueError(f"claim {claim['id']} references a missing source")
    for key, source in manifest["sources"].items():
        path = Path(source["path"])
        if not path.is_file() or _sha(path) != source["sha256"]:
            raise ValueError(f"presentation source hash mismatch: {key}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "presentation/delhi_evidence_manifest.json")
    args = parser.parse_args()
    manifest = build(dict(DEFAULTS)); validate(manifest); atomic_json(args.output, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
