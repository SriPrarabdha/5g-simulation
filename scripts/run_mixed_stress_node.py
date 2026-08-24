#!/usr/bin/env python3
"""Run one 125-cell guarded-campaign node with atomic resumability."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from experiments.mixed_stress_campaign import atomic_checkpoint, build_family, fingerprint, static_cache_key
from forecasting import load_forecaster_bundle
from optimization import CohortMPCConfig, ExposureGuardConfig, PreDrainFlowConfig
from simulator.macro import CompositeSink, Simulator, controller_by_name, load_scenario


def _run_simulation(config, controller):
    simulator = Simulator(config, controller)
    sink = simulator.make_summary_sink()
    outcome = simulator.run(CompositeSink([sink]))
    return sink.summary, simulator.controller, simulator.control_metrics(), outcome.timings


def _cell(job):
    cell, manifest, bundle, output_root, static_root = job
    available = sorted(os.sched_getaffinity(0))[:125]
    # PBS on the x800 partition exposes all 128 hardware CPUs even for a
    # 125-ncpu allocation. Pin deterministically so 125 workers cannot pile up
    # on a subset of cores or consume the three unrequested CPUs.
    os.sched_setaffinity(0, {available[int(cell["seed"]) % len(available)]})
    base = load_scenario(manifest)
    scenario = build_family(base, cell["family"], int(cell["seed"]))
    arm = cell["arm"]
    cadence_steps = round(int(arm["cadence_minutes"]) * 60 / scenario.step_seconds)
    observation_steps = round(10 * 60 / scenario.step_seconds)
    scenario = replace(scenario, decision_interval_steps=cadence_steps, observation_window_steps=observation_steps)
    identity = fingerprint({"cell": cell, "manifest": Path(manifest).read_text(encoding="utf-8")})
    destination = Path(output_root) / "pairs" / f"{cell['family']}-{int(cell['seed']):06d}.json"
    if destination.exists():
        return str(destination)

    static_path = Path(static_root) / f"{static_cache_key(scenario)}.json"
    if static_path.exists():
        static_summary = json.loads(static_path.read_text(encoding="utf-8"))["result"]
    else:
        static_summary, _, _, _ = _run_simulation(scenario, controller_by_name("static"))
        atomic_checkpoint(static_path, {"schema_version": "static-reference/1.0", "scenario_key": static_cache_key(scenario), "result": static_summary})

    guard = ExposureGuardConfig(
        minimum_blend_fraction=min(0.05, float(arm["maximum_blend"])),
        surprise_capacity_factor=float(arm["surprise_capacity_factor"]),
    )
    horizon_windows = round(int(arm["horizon_hours"]) * 60 / int(arm["cadence_minutes"]))
    if arm["controller"] == "predrain":
        controller = controller_by_name(
            "predrain",
            predrain_config=PreDrainFlowConfig(
                lead_windows=horizon_windows,
                action_blend_fraction=float(arm["maximum_blend"]),
                max_group_upf_weight=float(arm["destination_reserve"]),
            ),
            exposure_guard_config=guard,
        )
    else:
        controller = controller_by_name(
            "mpc", forecaster=load_forecaster_bundle(bundle),
            mpc_config=CohortMPCConfig(
                horizon_windows=max(2, horizon_windows),
                action_blend_fraction=float(arm["maximum_blend"]),
                max_group_upf_weight=float(arm["destination_reserve"]),
                require_known_future_capacity_event=True,
            ),
            exposure_guard_config=guard,
        )
    hybrid_summary, executed, control_metrics, timings = _run_simulation(scenario, controller)
    diagnostics = list(getattr(executed, "decision_diagnostics", []))
    atomic_checkpoint(destination, {
        "schema_version": "mixed-stress-pair/1.0", "work_fingerprint": identity,
        "cell": cell, "static_reference": str(static_path),
        "group_count": len(scenario.groups), "step_seconds": scenario.step_seconds,
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
    if len(cells) != 125 or args.workers != 125:
        raise ValueError("a discovery node requires exactly 125 cells/workers")
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[variable] = "1"
    jobs = [(cell, str(args.manifest), str(args.forecast_bundle), str(args.output_root), str(args.static_cache)) for cell in cells]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_cell, job) for job in jobs]
        for future in as_completed(futures):
            future.result()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
