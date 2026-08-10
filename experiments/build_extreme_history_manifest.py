from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STEP_SECONDS = 30
STEPS_PER_HOUR = 3600 // STEP_SECONDS
STEPS_PER_DAY = 24 * STEPS_PER_HOUR


def _daily_factor(profile: str, hour: float) -> float:
    def peak(center: float, width: float) -> float:
        distance = min(abs(hour - center), 24 - abs(hour - center))
        return math.exp(-0.5 * (distance / width) ** 2)

    if profile == "evening":
        return 0.42 + 1.18 * peak(20.0, 3.2)
    if profile == "late":
        return 0.45 + 1.25 * peak(22.0, 3.8)
    if profile == "business":
        return 0.28 + 1.32 * peak(13.0, 4.0)
    if profile == "industrial":
        return 0.55 + 0.90 * peak(11.0, 5.2)
    if profile == "commute":
        return 0.38 + 0.88 * peak(8.0, 1.8) + 1.00 * peak(18.0, 2.2)
    if profile == "overnight":
        return 0.30 + 1.35 * peak(2.0, 2.8)
    return 0.92 + 0.08 * math.sin(2 * math.pi * (hour - 6.0) / 24)


def _path_latency(upf_zone: str, target_zone: str, zones: list[str], tier: str) -> float:
    if tier == "central":
        return 12.0 + 1.5 * zones.index(target_zone)
    if tier == "regional":
        return 5.0 + 2.0 * abs(zones.index(upf_zone) - zones.index(target_zone))
    if upf_zone == target_zone:
        return 1.5
    return 10.0 + 3.0 * abs(zones.index(upf_zone) - zones.index(target_zone))


def _upfs(zones: list[str], capacity_scale: float) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for zone in zones:
        for suffix, ul, dl in (("a", 30_000, 90_000), ("b", 35_000, 80_000)):
            result.append({
                "upf_id": f"upf-edge-{zone}-{suffix}", "zone": zone,
                "capacity_mbps": {"ul": ul * capacity_scale, "dl": dl * capacity_scale},
                "safe_utilization": {"ul": 0.78, "dl": 0.78},
                "session_capacity": round(90_000 * capacity_scale), "session_safe_utilization": 0.82,
                "queue_limit_seconds": 2,
                "path_latency_ms_by_zone": {
                    target: _path_latency(zone, target, zones, "edge") for target in zones
                },
            })
    for index in range(4):
        zone = zones[index * 2]
        result.append({
            "upf_id": f"upf-regional-{index}", "zone": zone,
            "capacity_mbps": {"ul": 70_000 * capacity_scale, "dl": 150_000 * capacity_scale},
            "safe_utilization": {"ul": 0.80, "dl": 0.80},
            "session_capacity": round(220_000 * capacity_scale), "session_safe_utilization": 0.85,
            "queue_limit_seconds": 3,
            "path_latency_ms_by_zone": {
                target: _path_latency(zone, target, zones, "regional") for target in zones
            },
        })
    for index in range(4):
        result.append({
            "upf_id": f"upf-central-{index}", "zone": "central",
            "capacity_mbps": {"ul": 120_000 * capacity_scale, "dl": 240_000 * capacity_scale},
            "safe_utilization": {"ul": 0.75, "dl": 0.75},
            "session_capacity": round(320_000 * capacity_scale), "session_safe_utilization": 0.85,
            "queue_limit_seconds": 4,
            "path_latency_ms_by_zone": {
                target: _path_latency(zones[0], target, zones, "central") for target in zones
            },
        })
    return result


def _eligible_upfs(zone_index: int, zones: list[str]) -> list[str]:
    region = zone_index // 2
    return [
        f"upf-edge-{zones[zone_index]}-a",
        f"upf-edge-{zones[zone_index]}-b",
        f"upf-regional-{region}",
        f"upf-regional-{(region + 1) % 4}",
        f"upf-central-{zone_index % 4}",
        f"upf-central-{(zone_index + 1) % 4}",
    ]


def _groups(profile: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    zones = list(profile["zones"])
    for zone_index, zone in enumerate(zones):
        zone_scale = 0.70 + 0.10 * zone_index
        for service in profile["services"]:
            result.append({
                "key": {
                    "zone": zone,
                    "dnn": service["dnn"],
                    "snssai": service["snssai"],
                    "five_qi": service["five_qi"],
                },
                "arrivals_per_step": round(
                    service["arrivals_per_step"] * zone_scale * float(profile["arrival_scale"]), 4
                ),
                "lifetime_steps": {
                    "min": service["lifetime_steps"][0],
                    "max": service["lifetime_steps"][1],
                },
                "offered_mbps_per_session": {
                    "ul": service["offered_mbps_per_session"][0],
                    "dl": service["offered_mbps_per_session"][1],
                },
                "eligible_upfs": _eligible_upfs(zone_index, zones),
            })
    return result


def _surges(
    profile: dict[str, Any], rng: random.Random, total_steps: int
) -> dict[str, list[tuple[int, int, float]]]:
    result: dict[str, list[tuple[int, int, float]]] = {}
    stress = profile["stress"]
    weeks = math.ceil(total_steps / (7 * STEPS_PER_DAY))
    services = list(profile["services"])
    for week in range(weeks):
        week_start = week * 7 * STEPS_PER_DAY
        for _ in range(stress["surges_per_week"]):
            zone = rng.choice(profile["zones"])
            service = rng.choice(services)
            group_id = f"{zone}|{service['dnn']}|{service['snssai']}"
            start = week_start + rng.randrange(0, 7 * 24) * STEPS_PER_HOUR
            hours = rng.randint(*stress["surge_duration_hours_range"])
            end = min(total_steps, start + hours * STEPS_PER_HOUR)
            multiplier = rng.uniform(*stress["surge_multiplier_range"])
            result.setdefault(group_id, []).append((start, end, multiplier))
    return result


def _arrival_events(
    profile: dict[str, Any], rng: random.Random, total_steps: int
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    interval = profile["event_resolution_minutes"] * 60 // STEP_SECONDS
    services = {item["dnn"]: item for item in profile["services"]}
    surges = _surges(profile, rng, total_steps)
    group_ids = [
        f"{zone}|{service['dnn']}|{service['snssai']}"
        for zone in profile["zones"]
        for service in profile["services"]
    ]
    weekly_noise = {
        (week, group_id): rng.uniform(0.86, 1.16)
        for week in range(math.ceil(total_steps / (7 * STEPS_PER_DAY)))
        for group_id in group_ids
    }
    for step in range(0, total_steps, interval):
        day = step // STEPS_PER_DAY
        week = day // 7
        hour = (step % STEPS_PER_DAY) / STEPS_PER_HOUR
        weekend = day % 7 >= 5
        for group_id in group_ids:
            _, dnn, _ = group_id.split("|")
            service = services[dnn]
            factor = _daily_factor(service["daily_profile"], hour)
            factor *= service["weekend_factor"] if weekend else 1.0
            factor *= weekly_noise[(week, group_id)]
            for start, end, multiplier in surges.get(group_id, ()):
                if start <= step < end:
                    factor *= multiplier
            events.append({
                "step": step,
                "event_type": "arrival_factor",
                "group_id": group_id,
                "arrival_factor": round(max(0.05, factor), 4),
            })
    return events


def _fault_events(
    profile: dict[str, Any], rng: random.Random, upfs: list[dict[str, Any]], total_steps: int
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    stress = profile["stress"]
    weeks = math.ceil(total_steps / (7 * STEPS_PER_DAY))
    upf_by_id = {item["upf_id"]: item for item in upfs}
    for week in range(weeks):
        lower = week * 7 * STEPS_PER_DAY
        upper = min(total_steps, (week + 1) * 7 * STEPS_PER_DAY)
        if upper - lower < 2 * STEPS_PER_HOUR:
            continue
        for _ in range(stress["capacity_faults_per_week"]):
            upf = rng.choice(upfs)["upf_id"]
            start = rng.randrange(lower, upper - STEPS_PER_HOUR)
            end = min(total_steps - 1, start + rng.randint(1, 8) * STEPS_PER_HOUR)
            ul_factor = rng.uniform(0.18, 0.70)
            events.extend([
                {"step": start, "event_type": "capacity_factor", "upf_id": upf,
                 "ul_factor": round(ul_factor, 4), "dl_factor": round(min(0.85, ul_factor + 0.12), 4)},
                {"step": end, "event_type": "capacity_factor", "upf_id": upf,
                 "ul_factor": 1.0, "dl_factor": 1.0},
            ])
        for _ in range(stress["outages_per_week"]):
            upf = rng.choice(upfs)["upf_id"]
            start = rng.randrange(lower, upper - STEPS_PER_HOUR)
            end = min(total_steps - 1, start + rng.randint(1, 4) * STEPS_PER_HOUR)
            events.extend([
                {"step": start, "event_type": "health", "upf_id": upf, "health": "degraded"},
                {"step": start, "event_type": "capacity_factor", "upf_id": upf,
                 "ul_factor": 0.01, "dl_factor": 0.01},
                {"step": end, "event_type": "capacity_factor", "upf_id": upf,
                 "ul_factor": 1.0, "dl_factor": 1.0},
                {"step": end, "event_type": "health", "upf_id": upf, "health": "healthy"},
            ])
        for _ in range(stress["latency_incidents_per_week"]):
            upf = rng.choice(upfs)["upf_id"]
            zone = rng.choice(profile["zones"])
            start = rng.randrange(lower, upper - STEPS_PER_HOUR)
            end = min(total_steps - 1, start + rng.randint(1, 6) * STEPS_PER_HOUR)
            baseline = upf_by_id[upf]["path_latency_ms_by_zone"][zone]
            events.extend([
                {"step": start, "event_type": "path_latency", "upf_id": upf,
                 "zone": zone, "latency_ms": round(baseline + rng.uniform(25, 140), 3)},
                {"step": end, "event_type": "path_latency", "upf_id": upf,
                 "zone": zone, "latency_ms": baseline},
            ])
    return events


def build(
    profile: dict[str, Any], seed: int, start: datetime, *, days: int | None = None
) -> dict[str, Any]:
    rng = random.Random(seed)
    configured_days = int(profile["weeks"]) * 7
    duration_days = days if days is not None else configured_days
    if duration_days < 1 or duration_days > configured_days:
        raise ValueError(f"days must be in [1, {configured_days}]")
    steps = duration_days * STEPS_PER_DAY
    upfs = _upfs(list(profile["zones"]), float(profile["capacity_scale"]))
    groups = _groups(profile)
    events = _arrival_events(profile, rng, steps)
    events.extend(_fault_events(profile, rng, upfs, steps))
    events.sort(key=lambda item: (
        item["step"], item["event_type"], item.get("group_id", ""), item.get("upf_id", "")
    ))
    payload: dict[str, Any] = {
        "scenario_id": f"{profile['scenario_id']}-s{seed}",
        "seed": seed,
        "start_time": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "steps": steps,
        "step_seconds": STEP_SECONDS,
        "decision_interval_steps": 20,
        "selection_audit_stride": int(profile["selection_audit_stride"]),
        "primary_overload_metric": "overload_area_seconds.ul",
        "upfs": upfs,
        "groups": groups,
        "events": events,
        "corpus": {
            "synthetic": True,
            "profile_schema_version": profile["schema_version"],
            "duration_days": duration_days,
            "weeks": duration_days / 7,
            "resolution_seconds": STEP_SECONDS,
            "decision_bucket_seconds": 600,
            "nominal_ue_population": profile["nominal_ue_population"],
            "topology": {"zones": len(profile["zones"]), "upfs": len(upfs), "groups": len(groups)},
            "stress_families": [
                "diurnal_and_weekly_regimes", "flash_crowds", "capacity_brownouts",
                "near_total_upf_outages", "latency_incidents", "localized_overload",
            ],
            "split": {
                "train_weeks": [1, 11], "validation_weeks": [12, 13], "test_weeks": [14, 16]
            } if duration_days == configured_days else {"benchmark_days": [1, duration_days]},
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["corpus"]["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def build_optimizer_pilot(
    profile: dict[str, Any], seed: int, start: datetime
) -> dict[str, Any]:
    """Build one future day with deterministic, concentrated control stress."""
    payload = build(profile, seed, start, days=1)
    hour = STEPS_PER_HOUR
    surge_windows = [
        {
            "name": "stadium_crowd_plus_brownout",
            "knowledge": "scheduled_2h_notice",
            "start_hour": 4,
            "end_hour": 8,
            "multipliers": {
                "stadium|social-live|1-100002": 8.0,
                "stadium|internet-video|1-100001": 6.0,
                "stadium|gaming|1-100003": 6.0,
                "stadium|mission-critical|6-600001": 5.0,
            },
        },
        {
            "name": "airport_crowd_plus_outage",
            "knowledge": "unannounced",
            "start_hour": 10,
            "end_hour": 14,
            "multipliers": {
                "airport|edge-inference|5-500001": 7.0,
                "airport|enterprise-rtc|1-100006": 6.0,
                "airport|social-live|1-100002": 6.0,
                "airport|mission-critical|6-600001": 5.0,
            },
        },
        {
            "name": "industrial_uplink_plus_brownout",
            "knowledge": "scheduled_2h_notice",
            "start_hour": 17,
            "end_hour": 21,
            "multipliers": {
                "industrial|enterprise-backup|1-100007": 8.0,
                "industrial|edge-inference|5-500001": 7.0,
                "industrial|enterprise-rtc|1-100006": 6.0,
                "industrial|mission-critical|6-600001": 5.0,
            },
        },
    ]
    arrival_events = [
        dict(event) for event in payload["events"] if event["event_type"] == "arrival_factor"
    ]
    for event in arrival_events:
        for window in surge_windows:
            multiplier = window["multipliers"].get(event["group_id"])
            start_step = window["start_hour"] * hour
            end_step = window["end_hour"] * hour
            if (
                multiplier is not None
                and start_step <= event["step"] < end_step
            ):
                event["arrival_factor"] = round(event["arrival_factor"] * multiplier, 4)
                if window["knowledge"] != "unannounced":
                    event["known_at_step"] = max(0, start_step - 2 * hour)
                    event["forecast_hint_multiplier"] = multiplier
            elif (
                multiplier is not None
                and event["step"] == end_step
                and window["knowledge"] != "unannounced"
            ):
                event["known_at_step"] = max(0, start_step - 2 * hour)
                event["forecast_hint_multiplier"] = 1.0

    scripted_faults = [
        {"step": 5 * hour, "event_type": "capacity_factor", "upf_id": "upf-edge-stadium-a", "ul_factor": 0.20, "dl_factor": 0.30},
        {"step": 5 * hour, "event_type": "capacity_factor", "upf_id": "upf-regional-3", "ul_factor": 0.40, "dl_factor": 0.50},
        {"step": 8 * hour, "event_type": "capacity_factor", "upf_id": "upf-edge-stadium-a", "ul_factor": 1.0, "dl_factor": 1.0},
        {"step": 8 * hour, "event_type": "capacity_factor", "upf_id": "upf-regional-3", "ul_factor": 1.0, "dl_factor": 1.0},
        {"step": 11 * hour, "event_type": "health", "upf_id": "upf-edge-airport-a", "health": "degraded"},
        {"step": 11 * hour, "event_type": "capacity_factor", "upf_id": "upf-edge-airport-a", "ul_factor": 0.01, "dl_factor": 0.01},
        {"step": 13 * hour, "event_type": "capacity_factor", "upf_id": "upf-edge-airport-a", "ul_factor": 1.0, "dl_factor": 1.0},
        {"step": 13 * hour, "event_type": "health", "upf_id": "upf-edge-airport-a", "health": "healthy"},
        {"step": 15 * hour, "event_type": "path_latency", "upf_id": "upf-regional-2", "zone": "industrial", "latency_ms": 120.0},
        {"step": 17 * hour, "event_type": "path_latency", "upf_id": "upf-regional-2", "zone": "industrial", "latency_ms": 5.0},
        {"step": 18 * hour, "event_type": "capacity_factor", "upf_id": "upf-edge-industrial-a", "ul_factor": 0.20, "dl_factor": 0.35},
        {"step": 18 * hour, "event_type": "capacity_factor", "upf_id": "upf-regional-2", "ul_factor": 0.35, "dl_factor": 0.50},
        {"step": 21 * hour, "event_type": "capacity_factor", "upf_id": "upf-edge-industrial-a", "ul_factor": 1.0, "dl_factor": 1.0},
        {"step": 21 * hour, "event_type": "capacity_factor", "upf_id": "upf-regional-2", "ul_factor": 1.0, "dl_factor": 1.0},
    ]
    payload["events"] = sorted(
        [*arrival_events, *scripted_faults],
        key=lambda item: (
            item["step"], item["event_type"], item.get("group_id", ""), item.get("upf_id", "")
        ),
    )
    payload["scenario_id"] = f"extreme-optimizer-pilot-1d-s{seed}"
    payload["corpus"]["purpose"] = "fresh-seed event-dense trained-optimizer pilot"
    payload["corpus"]["pilot_surge_windows"] = [
        {key: value for key, value in window.items() if key != "multipliers"}
        | {"groups": sorted(window["multipliers"]), "multipliers": window["multipliers"]}
        for window in surge_windows
    ]
    payload["corpus"]["pilot_fault_events"] = len(scripted_faults)
    payload["corpus"].pop("manifest_sha256", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["corpus"]["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic high-scale 5G training manifest")
    parser.add_argument("--profile", type=Path, default=Path("configs/extreme_training_profile.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260805)
    parser.add_argument("--start", default="2026-01-05T00:00:00+00:00")
    parser.add_argument("--days", type=int, help="short calibration manifest; omit for the full profile")
    parser.add_argument(
        "--optimizer-pilot", action="store_true",
        help="build the fixed one-day, event-dense optimizer comparison manifest",
    )
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    if args.optimizer_pilot:
        if args.days is not None:
            parser.error("--optimizer-pilot has a fixed one-day duration; omit --days")
        manifest = build_optimizer_pilot(profile, args.seed, start)
    else:
        manifest = build(profile, args.seed, start, days=args.days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output), "steps": manifest["steps"],
        "groups": len(manifest["groups"]), "upfs": len(manifest["upfs"]),
        "events": len(manifest["events"]),
        "nominal_ue_population": manifest["corpus"]["nominal_ue_population"],
        "manifest_sha256": manifest["corpus"]["manifest_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
