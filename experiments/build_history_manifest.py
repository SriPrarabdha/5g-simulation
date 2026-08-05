from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WEEKS = 16
STEP_SECONDS = 30
STEPS_PER_DAY = 24 * 60 * 60 // STEP_SECONDS
SOCIAL_GROUP = "stadium|social-live|1-010204"
GAMING_GROUP = "metro|gaming-voice|1-010205"
ENTERPRISE_GROUP = "business|enterprise|1-112233"


def build(template: dict[str, Any], seed: int, start: datetime) -> dict[str, Any]:
    rng = random.Random(seed)
    payload = json.loads(json.dumps(template))
    payload.update({
        "scenario_id": f"synthetic-history-16w-s{seed}", "seed": seed,
        "start_time": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "steps": WEEKS * 7 * STEPS_PER_DAY,
        "events": [],
        "corpus": {
            "synthetic": True, "weeks": WEEKS, "resolution_seconds": STEP_SECONDS,
            "decision_bucket_seconds": 600, "nominal_ue_population": 30000,
            "split": {"train_weeks": [1, 11], "validation_weeks": [12, 13], "test_weeks": [14, 16]},
            "holdout_unit": ["event_template", "seed", "fault_regime"],
            "registry": "traffic-model-registry/1.0",
        },
    })
    events: list[dict[str, Any]] = []
    for week in range(WEEKS):
        # Saturday event start is known in advance; magnitude/outcome remains unavailable to features.
        day = week * 7 + 5
        start_hour = rng.choice([17, 18, 19, 20])
        kickoff = day * STEPS_PER_DAY + start_hour * 120
        weather = rng.uniform(.82, 1.22)
        magnitude = rng.uniform(2.7, 5.8) * weather
        lifecycle = [
            (-120, 1.6), (-20, magnitude), (90, magnitude * .72),
            (180, magnitude * 1.18), (240, magnitude * .8), (360, magnitude * 1.3),
            (480, 1.8), (720, 1.0),
        ]
        for offset, factor in lifecycle:
            events.append({"step": kickoff + offset, "event_type": "arrival_factor",
                           "group_id": SOCIAL_GROUP, "arrival_factor": round(factor, 4)})
        events.extend([
            {"step": kickoff - 60, "event_type": "arrival_factor", "group_id": GAMING_GROUP,
             "arrival_factor": round(1.25 + rng.random() * .7, 4)},
            {"step": kickoff + 540, "event_type": "arrival_factor", "group_id": GAMING_GROUP, "arrival_factor": 1.0},
            {"step": kickoff, "event_type": "arrival_factor", "group_id": ENTERPRISE_GROUP, "arrival_factor": .58},
            {"step": kickoff + 600, "event_type": "arrival_factor", "group_id": ENTERPRISE_GROUP, "arrival_factor": 1.12},
        ])
    upfs = [item["upf_id"] for item in payload["upfs"]]
    for _ in range(18):
        step = rng.randrange(STEPS_PER_DAY, payload["steps"] - STEPS_PER_DAY)
        upf = rng.choice(upfs)
        factor = rng.uniform(.25, .72)
        duration = rng.randint(20, 240)
        events.extend([
            {"step": step, "event_type": "capacity_factor", "upf_id": upf,
             "ul_factor": round(factor, 4), "dl_factor": round(min(1.0, factor + .12), 4)},
            {"step": step + duration, "event_type": "capacity_factor", "upf_id": upf,
             "ul_factor": 1.0, "dl_factor": 1.0},
        ])
    payload["events"] = sorted(events, key=lambda item: (item["step"], item["event_type"], item.get("group_id", "")))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["corpus"]["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze a deterministic 16-week synthetic history manifest")
    parser.add_argument("--template", type=Path, default=Path("configs/demo_scenario.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260805)
    parser.add_argument("--start", default="2026-01-05T00:00:00+00:00")
    args = parser.parse_args()
    template = json.loads(args.template.read_text(encoding="utf-8"))
    manifest = build(template, args.seed, datetime.fromisoformat(args.start.replace("Z", "+00:00")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(f"wrote {manifest['steps']} deterministic ticks to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
