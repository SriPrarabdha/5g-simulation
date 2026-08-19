from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from experiments.artifacts import atomic_json
from simulator.macro.config import load_scenario
from simulator.macro.engine import Simulator


def _worker(path: Path) -> dict[str, Any]:
    config = load_scenario(path)
    simulator = Simulator(config)
    started = time.perf_counter()
    while simulator.current_step < config.steps:
        simulator.advance()
    wall = time.perf_counter() - started
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "traffic_model_version": simulator.traffic_model_version,
        "steps": config.steps, "wall_seconds": wall,
        "peak_rss_bytes": int(usage.ru_maxrss * 1024),
    }


def characterize(scenario: Path) -> dict[str, Any]:
    payload = json.loads(scenario.read_text(encoding="utf-8"))
    controlled_v2 = json.loads(json.dumps(payload))
    controlled_v2["scenario_id"] += "-controlled-characterization"
    controlled_v2["traffic_model"]["mobility_phases"] = []
    controlled_v2["traffic_model"]["stadium_phases"] = []
    controlled_v2["traffic_model"]["telemetry"] = {}
    for group in controlled_v2["groups"]:
        demand = group["realism"]["demand"]
        demand.update({
            "ar1_phi": 0.0, "innovation_sigma": 0.0,
            "burst_enter_probability": 0.0, "burst_exit_probability": 1.0,
        })
    v1 = json.loads(json.dumps(payload))
    v1["scenario_id"] += "-v1-characterization"
    v1.pop("traffic_model", None)
    for group in v1["groups"]:
        group.pop("realism", None)
    with tempfile.TemporaryDirectory(prefix="cdot-v2-characterization-", dir="/tmp") as directory:
        v1_path = Path(directory) / "v1.json"
        v2_path = Path(directory) / "v2.json"
        atomic_json(v1_path, v1)
        atomic_json(v2_path, controlled_v2)
        runs = []
        for path in (v1_path, v2_path):
            completed = subprocess.run(
                [sys.executable, "-m", "experiments.characterize_traffic_v2", "--worker", str(path)],
                check=True, capture_output=True, text=True,
            )
            runs.append(json.loads(completed.stdout))
    baseline, candidate = runs
    wall_growth = candidate["wall_seconds"] / baseline["wall_seconds"] - 1
    rss_growth = candidate["peak_rss_bytes"] / baseline["peak_rss_bytes"] - 1
    return {
        "schema_version": "traffic-v2-performance-characterization/1.0",
        "scenario": str(scenario.resolve()), "runs": runs,
        "method": "controlled equal-arrival one-day workload; v2 mobility, bursts, stadium phases and telemetry pathologies disabled for overhead isolation",
        "walltime_growth_fraction": wall_growth, "peak_rss_growth_fraction": rss_growth,
        "targets": {"max_walltime_growth_fraction": .25, "max_peak_rss_growth_fraction": .20},
        "passed": wall_growth <= .25 and rss_growth <= .20,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=Path, default=Path("configs/delhi_traffic_v2.json"))
    parser.add_argument("--output", type=Path, default=Path("output/delhi/traffic-v2-performance.json"))
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker is not None:
        print(json.dumps(_worker(args.worker), sort_keys=True)); return 0
    result = characterize(args.scenario); atomic_json(args.output, result)
    print(json.dumps(result, sort_keys=True)); return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
