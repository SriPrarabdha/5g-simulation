from __future__ import annotations

import argparse
from pathlib import Path

from .run_campaign_shard import run_shard


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an exactly paired local macro campaign")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-root", default=Path("output/macro"), type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--seed-start", type=int, default=1000)
    parser.add_argument("--seed-count", type=int, default=30)
    parser.add_argument(
        "--controllers", nargs="+",
        choices=("static", "reactive", "forecast-capacity", "predictive", "mpc", "oracle"),
        default=("static", "reactive", "predictive"),
    )
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument(
        "--forecast-bundle", type=Path,
        help="trained bundle used by predictive, forecast-capacity, and MPC controllers",
    )
    parser.add_argument("--predictive-profile", type=Path)
    parser.add_argument("--mpc-profile", type=Path)
    args = parser.parse_args()
    if args.seed_count < 1:
        parser.error("--seed-count must be positive")
    for controller in args.controllers:
        for seed in range(args.seed_start, args.seed_start + args.seed_count):
            destination = run_shard(
                args.manifest,
                args.output_root,
                args.campaign_id,
                seed,
                skip_existing=args.skip_existing,
                controller=controller,
                forecast_bundle=(
                    args.forecast_bundle
                    if controller in {"predictive", "forecast-capacity", "mpc"} else None
                ),
                predictive_profile=(
                    args.predictive_profile
                    if controller in {"predictive", "forecast-capacity"} else None
                ),
                mpc_profile=args.mpc_profile if controller == "mpc" else None,
            )
            print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
