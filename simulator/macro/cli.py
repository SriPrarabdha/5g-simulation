from __future__ import annotations

import argparse
import json
from pathlib import Path

from forecasting import TrainedForecastBundle
from .config import load_scenario
from .controllers import controller_by_name
from .engine import Simulator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic C-DOT UPF macro scenario")
    parser.add_argument("manifest", help="path to a JSON scenario manifest")
    parser.add_argument("--output", required=True, help="destination JSONL audit file")
    parser.add_argument(
        "--controller", choices=("static", "reactive", "forecast-capacity", "predictive", "oracle"), default="static",
        help="placement controller to evaluate",
    )
    parser.add_argument(
        "--forecast-bundle", type=Path,
        help="checksum-verified trained bundle for predictive or forecast-capacity controllers",
    )
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
    result = Simulator(
        scenario,
        controller_by_name(args.controller, forecaster=forecaster),
    ).run()
    result.write_jsonl(args.output)
    print(json.dumps(result.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
