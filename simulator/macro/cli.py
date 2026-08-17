from __future__ import annotations

import argparse
import json
from pathlib import Path

from forecasting import TrainedForecastBundle
from optimization import CohortMPCConfig, OptimizationConfig
from steering import PolicyGateConfig
from .config import load_scenario
from .controllers import ForecastAdjustmentConfig, controller_by_name
from .engine import Simulator
from .sinks import CompositeSink, JsonlSink


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic C-DOT UPF macro scenario")
    parser.add_argument("manifest", help="path to a JSON scenario manifest")
    parser.add_argument("--output", required=True, help="destination JSONL audit file")
    parser.add_argument(
        "--controller", choices=("static", "reactive", "forecast-capacity", "predictive", "mpc", "oracle"), default="static",
        help="placement controller to evaluate",
    )
    parser.add_argument(
        "--forecast-bundle", type=Path,
        help="checksum-verified trained bundle for predictive, forecast-capacity, or MPC controllers",
    )
    parser.add_argument("--predictive-profile", type=Path)
    parser.add_argument("--mpc-profile", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    forecaster = (
        TrainedForecastBundle.load(args.forecast_bundle)
        if args.forecast_bundle is not None else None
    )
    scenario = load_scenario(args.manifest)
    if forecaster is not None:
        forecaster.validate_groups(group.key for group in scenario.groups)
    profile = (
        json.loads(args.predictive_profile.read_text(encoding="utf-8"))
        if args.predictive_profile is not None else None
    )
    if profile is not None and profile.get("schema_version") != "predictive-controller-profile/1.0":
        raise ValueError("unsupported predictive controller profile schema")
    mpc_profile = (
        json.loads(args.mpc_profile.read_text(encoding="utf-8"))
        if args.mpc_profile is not None else None
    )
    if mpc_profile is not None and mpc_profile.get("schema_version") != "cohort-mpc-profile/1.0":
        raise ValueError("unsupported cohort MPC profile schema")
    simulator = Simulator(
        scenario,
        controller_by_name(
            args.controller,
            forecaster=forecaster,
            gate_config=PolicyGateConfig(**profile.get("gate", {})) if profile else None,
            optimization_config=(
                OptimizationConfig(**profile.get("optimization", {})) if profile else None
            ),
            optimizer_weight=float(profile.get("optimizer_weight", 1.0)) if profile else 1.0,
            forecast_adjustment_config=(
                ForecastAdjustmentConfig(**profile.get("forecast_adjustment", {}))
                if profile else None
            ),
            mpc_config=(
                CohortMPCConfig(**mpc_profile.get("mpc", {}))
                if mpc_profile else None
            ),
        ),
    )
    summary = simulator.make_summary_sink()
    outcome = simulator.run(CompositeSink([summary, JsonlSink(args.output)]))
    print(json.dumps(outcome.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
