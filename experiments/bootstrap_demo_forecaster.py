from __future__ import annotations

import argparse
import math
import random
from datetime import timedelta
from pathlib import Path

from forecasting import DemandObservation, train_forecast_bundle, write_forecast_bundle
from schemas import TimeWindow
from simulator.macro.config import load_scenario


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the small frozen forecast bundle shipped with the demo")
    parser.add_argument("--scenario", type=Path, default=Path("configs/demo_scenario.json"))
    parser.add_argument("--output", type=Path, default=Path("configs/demo_forecast_bundle.json"))
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--seed", type=int, default=20260805)
    args = parser.parse_args()
    config = load_scenario(args.scenario)
    rng = random.Random(args.seed)
    bucket = timedelta(minutes=10)
    count = args.days * 24 * 6
    grouped: dict[str, list[list[DemandObservation]]] = {}
    for group_index, group in enumerate(config.groups):
        sequence: list[DemandObservation] = []
        for index in range(count):
            start = config.start_time - count * bucket + index * bucket
            hour = start.hour + start.minute / 60
            daily = 1 + 0.32 * math.sin(2 * math.pi * (hour - 7 - group_index * .7) / 24)
            weekly = 0.82 if start.weekday() >= 5 and group.key.zone == "business" else 1.0
            event = 1.0
            if group.key.zone == "stadium" and start.weekday() == 5 and 17 <= hour <= 22:
                event = 2.2 + 1.4 * math.sin(math.pi * (hour - 17) / 5)
            expected = max(0.1, group.arrivals_per_step * config.decision_interval_steps * daily * weekly * event)
            realized = max(0.0, expected * math.exp(rng.gauss(-0.012, .16)))
            sequence.append(DemandObservation(
                window=TimeWindow(start, start + bucket), group=group.key,
                new_session_count=realized,
                new_ul_mbps=realized * group.offered_ul_mbps_per_session,
                new_dl_mbps=realized * group.offered_dl_mbps_per_session,
                quality_flags=("synthetic_training", "bootstrap_calibration"),
            ))
        grouped[group.key.selection_id] = [sequence]
    payload = train_forecast_bundle(
        grouped,
        model_version="calendar-ridge-conformal/demo-1.0",
        source={
            "kind": "shipped_demo_bootstrap", "synthetic": True,
            "scenario": str(args.scenario), "days": args.days, "seed": args.seed,
            "release_status": "demo_calibrated_not_campaign_release",
        },
    )
    write_forecast_bundle(args.output, payload)
    print(f"wrote {payload['model_version']} ({payload['bundle_sha256']}) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
