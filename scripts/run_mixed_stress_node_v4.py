#!/usr/bin/env python3
"""Run one 125-cell guarded-campaign node (v4) with atomic resumability.

Two controller-configuration defects from the v3 run are corrected here.

The MPC horizon was set in *windows* as ``horizon_hours * 60 / cadence``, so
halving the cadence quintupled the LP depth.  Every 2-minute MPC arm then timed
out on 100% of solves and scored exactly Static, which was reported as a cadence
result rather than the sizing bug it was.  v4 caps the LP depth and records the
lookahead each arm actually achieved, so the cadence comparison holds problem
size fixed instead of holding wall-clock lookahead fixed.

The MPC solver budget was left at the 2.0 s default while the acceptance
contract permitted 120 s decisions.  Even at 10-minute cadence that lost 40% of
solves to timeouts.  v4 sets the budget from the contract.
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from experiments.mixed_stress_campaign_v4 import (
    atomic_checkpoint, build_family, fingerprint, static_cache_key,
)
from forecasting import load_forecaster_bundle
from optimization import CohortMPCConfig, ExposureGuardConfig, PreDrainFlowConfig
from simulator.macro import CompositeSink, Simulator, controller_by_name, load_scenario

# Holding the LP depth fixed keeps the 2-minute and 10-minute arms solving the
# same size problem; cadence then trades lookahead against reactivity honestly.
MAXIMUM_MPC_HORIZON_WINDOWS = 12
MPC_SOLVER_TIMEOUT_SECONDS = 20.0
PREDRAIN_SOLVER_TIMEOUT_SECONDS = 2.0
OBSERVATION_WINDOW_SECONDS = 600


def _run_simulation(config, controller):
    simulator = Simulator(config, controller)
    sink = simulator.make_summary_sink()
    outcome = simulator.run(CompositeSink([sink]))
    return sink.summary, simulator.controller, simulator.control_metrics(), outcome.timings


def _cell(job):
    cell, manifest, bundle, output_root, static_root, slot = job
    available = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {available[slot % len(available)]})
    base = load_scenario(manifest)
    scenario = build_family(base, cell["family"], int(cell["seed"]))
    arm = cell["arm"]
    cadence_steps = round(int(arm["cadence_minutes"]) * 60 / scenario.step_seconds)
    observation_steps = round(OBSERVATION_WINDOW_SECONDS / scenario.step_seconds)
    observation_steps = max(cadence_steps, observation_steps - observation_steps % cadence_steps)
    scenario = replace(
        scenario,
        decision_interval_steps=cadence_steps,
        observation_window_steps=observation_steps,
    )
    identity = fingerprint({"cell": cell, "manifest": Path(manifest).read_text(encoding="utf-8")})
    destination = Path(output_root) / "pairs" / f"{cell['family']}-{int(cell['seed']):06d}.json"
    if destination.exists():
        return str(destination)

    static_path = Path(static_root) / f"{static_cache_key(scenario)}.json"
    if static_path.exists():
        static_summary = json.loads(static_path.read_text(encoding="utf-8"))["result"]
    else:
        static_summary, _, _, _ = _run_simulation(scenario, controller_by_name("static"))
        atomic_checkpoint(static_path, {
            "schema_version": "static-reference/4.0",
            "scenario_key": static_cache_key(scenario), "result": static_summary,
        })

    guard = ExposureGuardConfig(
        minimum_blend_fraction=min(0.05, float(arm["maximum_blend"])),
        surprise_capacity_factor=float(arm["surprise_capacity_factor"]),
    )
    # Wall-clock lead is cadence-independent: lead_windows * decision_interval
    # is always horizon_hours of simulated time.
    lead_windows = max(1, round(int(arm["horizon_hours"]) * 3600 / scenario.step_seconds / cadence_steps))
    if arm["controller"] == "predrain":
        sizing = {"lead_windows": lead_windows,
                  "lookahead_minutes": lead_windows * cadence_steps * scenario.step_seconds / 60}
        controller = controller_by_name(
            "predrain",
            predrain_config=PreDrainFlowConfig(
                lead_windows=lead_windows,
                timeout_seconds=PREDRAIN_SOLVER_TIMEOUT_SECONDS,
                action_blend_fraction=float(arm["maximum_blend"]),
                max_group_upf_weight=float(arm["destination_reserve"]),
            ),
            exposure_guard_config=guard,
        )
    else:
        horizon_windows = max(2, min(MAXIMUM_MPC_HORIZON_WINDOWS, lead_windows))
        sizing = {"horizon_windows": horizon_windows,
                  "lookahead_minutes": horizon_windows * cadence_steps * scenario.step_seconds / 60,
                  "requested_lead_windows": lead_windows}
        controller = controller_by_name(
            "mpc", forecaster=load_forecaster_bundle(bundle),
            mpc_config=CohortMPCConfig(
                horizon_windows=horizon_windows,
                timeout_seconds=MPC_SOLVER_TIMEOUT_SECONDS,
                action_blend_fraction=float(arm["maximum_blend"]),
                max_group_upf_weight=float(arm["destination_reserve"]),
                require_known_future_capacity_event=True,
            ),
            exposure_guard_config=guard,
        )
    hybrid_summary, executed, control_metrics, timings = _run_simulation(scenario, controller)
    diagnostics = list(getattr(executed, "decision_diagnostics", []))
    atomic_checkpoint(destination, {
        "schema_version": "mixed-stress-pair/4.0", "work_fingerprint": identity,
        "cell": cell, "static_reference": str(static_path), "sizing": sizing,
        "group_count": len(scenario.groups), "step_seconds": scenario.step_seconds,
        "decision_interval_steps": cadence_steps,
        "static": static_summary, "hybrid": hybrid_summary,
        "decision_diagnostics": diagnostics,
        "control_metrics": control_metrics, "timings": timings,
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
    })
    return str(destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worklist", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--forecast-bundle", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=125)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--static-cache", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.worklist.read_text(encoding="utf-8"))
    cells = payload["cells"]
    if len(cells) != args.workers:
        raise ValueError(f"expected one worker per cell, got {len(cells)} cells and {args.workers} workers")
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[variable] = "1"
    jobs = [
        (cell, str(args.manifest), str(args.forecast_bundle), str(args.output_root),
         str(args.static_cache), slot)
        for slot, cell in enumerate(cells)
    ]
    failures = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_cell, job): job[0] for job in jobs}
        for future in as_completed(futures):
            cell = futures[future]
            try:
                future.result()
            except Exception as error:  # keep the node's other 124 cells
                failures.append({"family": cell["family"], "seed": cell["seed"],
                                 "error": f"{type(error).__name__}: {error}"})
    if failures:
        # Validation shards share one output root, so name the report after the
        # worklist that produced it rather than letting shards clobber a
        # single failures.json.
        report = Path(args.output_root) / f"failures-{args.worklist.stem}-{os.getpid()}.json"
        report.write_text(json.dumps(failures, indent=2, sort_keys=True), encoding="utf-8")
        print(f"ERROR: {len(failures)} of {len(cells)} cells failed; see {report}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
