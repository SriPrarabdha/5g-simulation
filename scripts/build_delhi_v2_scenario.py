#!/usr/bin/env python3
"""Build the additive 24-UPF / 96-group traffic-model/2.0 Delhi scenario."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json


ZONES = (
    "delhi-ncr", "north", "south", "east", "west", "central", "north-east", "south-west",
)
POPULATION = {
    "delhi-ncr": 3_200_000,
    "north": 2_150_000,
    "south": 2_000_000,
    "east": 1_850_000,
    "west": 1_900_000,
    "central": 1_450_000,
    "north-east": 1_700_000,
    "south-west": 1_750_000,
}
SERVICES = (
    ("consumer", "internet", "1-010101", 9, 7.0, 0.20, 0.85, 0.58),
    ("consumer", "video", "1-010102", 8, 5.0, 0.12, 2.40, 0.62),
    ("consumer", "voice", "1-010103", 1, 2.0, 0.08, 0.08, 0.72),
    ("enterprise", "internet", "1-112233", 9, 3.4, 0.35, 0.75, 0.60),
    ("enterprise", "vpn", "1-112234", 7, 1.8, 0.55, 0.70, 0.67),
    ("industry", "telemetry", "1-223344", 6, 2.2, 0.07, 0.09, 0.48),
    ("industry", "control", "1-223345", 5, 1.2, 0.12, 0.10, 0.56),
    ("mobility", "vehicle", "1-334455", 7, 2.0, 0.14, 0.16, 0.52),
    ("public", "safety", "1-445566", 2, 0.7, 0.22, 0.28, 0.65),
    ("stadium", "media", "1-556677", 8, 0.8, 0.90, 1.40, 0.78),
    ("iot", "massive", "1-667788", 9, 4.0, 0.04, 0.05, 0.42),
    ("roaming", "internet", "1-778899", 9, 1.5, 0.18, 0.65, 0.55),
)


def _transition(stay: float, clockwise: bool) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for index, origin in enumerate(ZONES):
        row = {zone: 0.0 for zone in ZONES}
        row[origin] = stay
        neighbor = ZONES[(index + (1 if clockwise else -1)) % len(ZONES)]
        row[neighbor] = 1.0 - stay
        result[origin] = row
    return result


def build(seed: int = 20260828, *, days: int = 1, split_role: str | None = None) -> dict:
    if days < 1:
        raise ValueError("days must be positive")
    if split_role not in {None, "train", "selection_calibration", "untouched_test"}:
        raise ValueError("unsupported forecast split role")
    upfs = []
    for zone_index, zone in enumerate(ZONES):
        for tier_index, tier in enumerate(("edge", "regional", "central")):
            upfs.append({
                "upf_id": f"upf-{zone_index + 1:02d}-{tier}", "zone": zone,
                "capacity_mbps": {
                    "ul": (2600, 4300, 6500)[tier_index],
                    "dl": (5200, 8600, 13000)[tier_index],
                },
                "safe_utilization": {"ul": 0.78, "dl": 0.80},
                "session_capacity": (170000, 290000, 430000)[tier_index],
                "session_safe_utilization": 0.82,
                "queue_limit_seconds": 0.25,
                "path_latency_ms_by_zone": {
                    other: (7.0 + tier_index * 4.0 if other == zone else 22.0 + tier_index * 5.0)
                    for other in ZONES
                },
            })
    groups = []
    for zone_index, zone in enumerate(ZONES):
        eligible = [f"upf-{zone_index + 1:02d}-{tier}" for tier in ("edge", "regional", "central")]
        for service_index, (segment, dnn, snssai, five_qi, arrivals, ul, dl, correlation) in enumerate(SERVICES):
            median_steps = 70 + service_index * 9
            groups.append({
                "key": {"zone": zone, "dnn": f"{segment}-{dnn}", "snssai": snssai, "five_qi": five_qi},
                "arrivals_per_step": arrivals,
                "lifetime_steps": {"min": 10, "max": 720},
                "offered_mbps_per_session": {"ul": ul, "dl": dl},
                "eligible_upfs": eligible,
                "realism": {
                    "holding_time": {
                        "distribution": "lognormal" if service_index % 3 else "pareto",
                        "shape": 1.7 if service_index % 3 == 0 else 0.62,
                        "scale_steps": median_steps,
                        "min_steps": 10,
                        "max_steps": 720,
                    },
                    "demand": {
                        "ar1_phi": 0.82,
                        "innovation_sigma": 0.025,
                        "burst_enter_probability": 0.004,
                        "burst_exit_probability": 0.09,
                        "burst_pareto_alpha": 2.4,
                        "burst_max_multiplier": 4.0,
                    },
                    "joint_rates": {
                        "correlation": correlation,
                        "ul_lognormal": {
                            "median_mbps": ul, "sigma": 0.68,
                            "min_mbps": ul * 0.12, "max_mbps": ul * 7.0,
                        },
                        "dl_lognormal": {
                            "median_mbps": dl, "sigma": 0.72,
                            "min_mbps": dl * 0.12, "max_mbps": dl * 7.0,
                        },
                    },
                },
            })
    stadium_groups = [
        "|".join(("delhi-ncr", f"{segment}-{dnn}", snssai))
        for segment, dnn, snssai, five_qi, *_ in SERVICES
        if segment in {"stadium", "consumer", "roaming"}
    ]
    phases = (
        ("ingress", 1320, 1440, 1.5), ("kickoff", 1440, 1500, 2.1),
        ("match", 1500, 1620, 1.7), ("halftime_upload", 1620, 1680, 3.2),
        ("final_whistle", 1680, 1740, 2.6), ("egress", 1740, 1920, 1.9),
    )
    payload = {
        "scenario_id": "cdot-delhi-national-v2-1d-s20260828",
        "seed": seed, "start_time": "2026-08-28T00:00:00Z",
        "steps": 2880, "step_seconds": 30, "decision_interval_steps": 20,
        "selection_audit_stride": 100, "primary_overload_metric": "overload_area_seconds.ul",
        "traffic_model": {
            "schema_version": "traffic-model/2.0",
            "aggregate_population_by_zone": dict(POPULATION),
            "mobility_phases": [
                {"start_step": 720, "transition_by_origin": _transition(0.985, True)},
                {"start_step": 1440, "transition_by_origin": _transition(0.990, False)},
                {"start_step": 2160, "transition_by_origin": _transition(0.987, True)},
            ],
            "stadium_phases": [
                {"name": name, "start_step": start, "end_step": end,
                 "group_ids": stadium_groups, "arrival_multiplier": multiplier}
                for name, start, end, multiplier in phases
            ],
            "telemetry": {
                "missing_scrape_probability": 0.008,
                "reset_probability": 0.0008,
                "restart_probability": 0.0004,
                "stale_probability": 0.004,
            },
        },
        "groups": groups, "upfs": upfs,
        "events": [
            {"step": 1500, "event_type": "capacity_factor", "upf_id": "upf-01-edge",
             "ul_factor": 0.45, "known_at_step": 1260},
            {"step": 1680, "event_type": "capacity_factor", "upf_id": "upf-01-edge",
             "ul_factor": 1.0, "known_at_step": 1260},
            {"step": 2040, "event_type": "health", "upf_id": "upf-05-edge", "health": "unavailable"},
            {"step": 2160, "event_type": "health", "upf_id": "upf-05-edge", "health": "healthy"},
        ],
    }
    if days == 1:
        return payload

    steps_per_day = 2880
    base_mobility = list(payload["traffic_model"]["mobility_phases"])
    base_stadium = list(payload["traffic_model"]["stadium_phases"])
    base_events = list(payload["events"])
    payload["scenario_id"] = f"cdot-delhi-national-v2-{days}d-s{seed}"
    payload["steps"] = days * steps_per_day
    payload["traffic_model"]["mobility_phases"] = [
        {**phase, "start_step": int(phase["start_step"]) + day * steps_per_day}
        for day in range(days)
        for phase in base_mobility
    ]
    payload["traffic_model"]["stadium_phases"] = [
        {
            **phase,
            "start_step": int(phase["start_step"]) + day * steps_per_day,
            "end_step": int(phase["end_step"]) + day * steps_per_day,
        }
        for day in range(days)
        for phase in base_stadium
    ]
    payload["events"] = [
        {
            **event,
            "step": int(event["step"]) + day * steps_per_day,
            **(
                {"known_at_step": int(event["known_at_step"]) + day * steps_per_day}
                if event.get("known_at_step") is not None else {}
            ),
        }
        for day in range(days)
        for event in base_events
    ]
    payload["corpus"] = {
        "synthetic": True,
        "traffic_model": "traffic-model/2.0",
        "duration_days": days,
        "split_role": split_role,
        "seed": seed,
        "untouched": split_role == "untouched_test",
        "actual_generated_rate_bin_labels": True,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("configs/delhi_traffic_v2.json"))
    parser.add_argument("--seed", type=int, default=20260828)
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument(
        "--split-role",
        choices=("train", "selection_calibration", "untouched_test"),
    )
    args = parser.parse_args()
    atomic_json(args.output, build(args.seed, days=args.days, split_role=args.split_role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
