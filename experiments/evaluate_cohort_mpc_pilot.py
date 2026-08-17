from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from forecasting import TrainedForecastBundle
from optimization import CohortMPCConfig
from simulator.macro import Simulator, load_scenario
from simulator.macro.config import ScenarioEvent
from simulator.macro.controllers import CohortMPCController, StaticCapacityController


SCHEMA_VERSION = "cohort-mpc-pre-campaign-pilot/1.0"
RESERVED_SEEDS = frozenset({20260810, 20260811})
ScenarioKind = Literal["surge", "scheduled_fault", "unannounced_outage", "mixed_stress"]
SCENARIO_KINDS: tuple[ScenarioKind, ...] = (
    "surge",
    "scheduled_fault",
    "unannounced_outage",
    "mixed_stress",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aligned(value: int, bucket_steps: int, steps: int) -> int:
    return min(steps - 1, max(0, value // bucket_steps * bucket_steps))


def _arrival_episode(
    group_id: str, start: int, end: int, factor: float
) -> tuple[ScenarioEvent, ScenarioEvent]:
    return (
        ScenarioEvent(
            step=start,
            event_type="arrival_factor",
            group_id=group_id,
            arrival_factor=factor,
        ),
        ScenarioEvent(
            step=end,
            event_type="arrival_factor",
            group_id=group_id,
            arrival_factor=1.0,
        ),
    )


def _capacity_episode(
    upf_id: str,
    start: int,
    end: int,
    factor: float,
    *,
    known_at: int | None,
    unavailable: bool = False,
) -> tuple[ScenarioEvent, ...]:
    knowledge = {"known_at_step": known_at} if known_at is not None else {}
    events: list[ScenarioEvent] = []
    if unavailable:
        events.append(ScenarioEvent(
            step=start,
            event_type="health",
            upf_id=upf_id,
            health="unavailable",
            **knowledge,
        ))
    events.append(ScenarioEvent(
        step=start,
        event_type="capacity_factor",
        upf_id=upf_id,
        ul_factor=factor,
        dl_factor=min(0.75, factor + 0.15),
        **knowledge,
    ))
    events.append(ScenarioEvent(
        step=end,
        event_type="capacity_factor",
        upf_id=upf_id,
        ul_factor=1.0,
        dl_factor=1.0,
        **knowledge,
    ))
    if unavailable:
        events.append(ScenarioEvent(
            step=end,
            event_type="health",
            upf_id=upf_id,
            health="healthy",
            **knowledge,
        ))
    return tuple(events)


def build_pilot_scenario(
    base,
    *,
    kind: ScenarioKind,
    seed: int,
    steps: int,
    horizon_windows: int,
):
    if seed in RESERVED_SEEDS:
        raise ValueError(f"seed {seed} is reserved")
    if kind not in SCENARIO_KINDS:
        raise ValueError(f"unsupported pilot scenario: {kind}")
    rng = random.Random(f"cohort-mpc-pilot:{kind}:{seed}")
    bucket = base.decision_interval_steps
    groups = list(base.groups)
    upfs = list(base.upfs)
    events: list[ScenarioEvent] = []

    def point(fraction: float, jitter_buckets: int = 6) -> int:
        jitter = rng.randint(-jitter_buckets, jitter_buckets) * bucket
        return _aligned(round(steps * fraction) + jitter, bucket, steps)

    if kind == "surge":
        selected = rng.sample(groups, min(3, len(groups)))
        for index, group in enumerate(selected):
            start = point(0.22 + index * 0.22)
            end = _aligned(start + rng.randint(18, 36) * bucket, bucket, steps)
            events.extend(_arrival_episode(
                group.key.selection_id, start, end, rng.uniform(2.5, 5.5)
            ))

    elif kind == "scheduled_fault":
        selected_upfs = rng.sample(upfs, min(2, len(upfs)))
        for index, upf in enumerate(selected_upfs):
            start = point(0.34 + index * 0.24)
            end = _aligned(start + rng.randint(24, 42) * bucket, bucket, steps)
            known_at = max(0, start - horizon_windows * bucket)
            events.extend(_capacity_episode(
                upf.upf_id,
                start,
                end,
                rng.uniform(0.08, 0.35),
                known_at=known_at,
            ))

    elif kind == "unannounced_outage":
        upf = rng.choice(upfs)
        start = point(0.46)
        end = _aligned(start + rng.randint(18, 30) * bucket, bucket, steps)
        events.extend(_capacity_episode(
            upf.upf_id,
            start,
            end,
            0.01,
            known_at=None,
            # Match the extreme benchmark's outage convention: retain a 1%
            # emergency envelope so normalized overload area remains finite.
            unavailable=False,
        ))
        group = rng.choice(groups)
        surge_start = _aligned(start - 12 * bucket, bucket, steps)
        events.extend(_arrival_episode(
            group.key.selection_id,
            surge_start,
            end,
            rng.uniform(1.8, 3.2),
        ))

    else:
        scheduled_upf, surprise_upf = rng.sample(upfs, 2)
        scheduled_start = point(0.25)
        scheduled_end = _aligned(
            scheduled_start + rng.randint(24, 40) * bucket, bucket, steps
        )
        known_at = max(0, scheduled_start - horizon_windows * bucket)
        events.extend(_capacity_episode(
            scheduled_upf.upf_id,
            scheduled_start,
            scheduled_end,
            rng.uniform(0.12, 0.4),
            known_at=known_at,
        ))
        outage_start = point(0.62)
        outage_end = _aligned(
            outage_start + rng.randint(12, 24) * bucket, bucket, steps
        )
        events.extend(_capacity_episode(
            surprise_upf.upf_id,
            outage_start,
            outage_end,
            0.01,
            known_at=None,
            unavailable=False,
        ))
        for group, fraction in zip(rng.sample(groups, min(2, len(groups))), (0.18, 0.55)):
            start = point(fraction)
            end = _aligned(start + rng.randint(18, 34) * bucket, bucket, steps)
            events.extend(_arrival_episode(
                group.key.selection_id, start, end, rng.uniform(2.0, 4.5)
            ))
        latency_upf = rng.choice(upfs)
        zone = rng.choice(groups).key.zone
        latency_start = point(0.40)
        latency_end = _aligned(latency_start + 30 * bucket, bucket, steps)
        baseline_latency = latency_upf.path_latency_ms_by_zone[zone]
        events.extend((
            ScenarioEvent(
                step=latency_start,
                event_type="path_latency",
                upf_id=latency_upf.upf_id,
                zone=zone,
                latency_ms=baseline_latency + rng.uniform(30, 100),
            ),
            ScenarioEvent(
                step=latency_end,
                event_type="path_latency",
                upf_id=latency_upf.upf_id,
                zone=zone,
                latency_ms=baseline_latency,
            ),
        ))

    events.sort(key=lambda event: (
        event.step,
        event.event_type,
        event.group_id or "",
        event.upf_id or "",
    ))
    scenario = replace(
        base,
        scenario_id=f"{base.scenario_id}-mpc-pilot-{kind}-s{seed}",
        seed=seed,
        steps=steps,
        events=tuple(events),
    )
    return scenario, [asdict(event) for event in events]


def _relative_reduction(static: float, candidate: float) -> float:
    if not math.isfinite(static) or not math.isfinite(candidate):
        return 0.0 if static == candidate else (1.0 if math.isfinite(candidate) else -math.inf)
    return (static - candidate) / static if static > 0 else 0.0


def _within(candidate: float, static: float, tolerance: float = 1e-9) -> bool:
    if not math.isfinite(static) or not math.isfinite(candidate):
        return candidate == static
    return candidate <= static + tolerance * max(1.0, abs(static))


def evaluate_pilot(
    manifest: Path,
    bundle_path: Path,
    profile_path: Path,
    seeds_by_scenario: dict[ScenarioKind, list[int]],
    *,
    steps: int,
    progress: bool = False,
) -> dict[str, Any]:
    seeds = [seed for values in seeds_by_scenario.values() for seed in values]
    if set(seeds_by_scenario) != set(SCENARIO_KINDS):
        raise ValueError("pilot requires all four scenario kinds")
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("pilot seeds must be non-empty and unique")
    if RESERVED_SEEDS.intersection(seeds):
        raise ValueError("reserved validation seeds cannot be consumed by the pilot")
    base = load_scenario(manifest)
    if steps < 24 * 3600 // base.step_seconds:
        raise ValueError("each pilot scenario must cover at least one simulated day")
    bundle = TrainedForecastBundle.load(bundle_path)
    bundle.validate_groups(group.key for group in base.groups)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile.get("schema_version") != "cohort-mpc-profile/1.0":
        raise ValueError("unsupported cohort MPC profile schema")
    settings = CohortMPCConfig(**profile.get("mpc", {}))

    records: list[dict[str, Any]] = []
    total = len(seeds)
    completed = 0
    for kind in SCENARIO_KINDS:
        for seed in seeds_by_scenario[kind]:
            scenario, events = build_pilot_scenario(
                base,
                kind=kind,
                seed=seed,
                steps=steps,
                horizon_windows=settings.horizon_windows,
            )
            if progress:
                print(
                    f"pilot {completed + 1}/{total} scenario={kind} seed={seed} controller=static",
                    flush=True,
                )
            static_simulator = Simulator(scenario, StaticCapacityController())
            static = static_simulator.run(static_simulator.make_summary_sink()).summary
            controller = CohortMPCController(forecaster=bundle, mpc_config=settings)
            if progress:
                print(
                    f"pilot {completed + 1}/{total} scenario={kind} seed={seed} controller=mpc",
                    flush=True,
                )
            mpc_simulator = Simulator(scenario, controller)
            mpc = mpc_simulator.run(mpc_simulator.make_summary_sink()).summary
            reductions = {
                metric: {
                    direction: _relative_reduction(
                        static[metric][direction], mpc[metric][direction]
                    )
                    for direction in ("ul", "dl")
                }
                for metric in ("overload_area_seconds", "dropped_bytes")
            }
            gates = {
                "ul_overload_improves": reductions["overload_area_seconds"]["ul"] > 0,
                "no_dl_overload_regression": _within(
                    mpc["overload_area_seconds"]["dl"],
                    static["overload_area_seconds"]["dl"],
                ),
                "no_directional_drop_regression": all(
                    _within(mpc["dropped_bytes"][direction], static["dropped_bytes"][direction])
                    for direction in ("ul", "dl")
                ),
                "no_session_failure_regression": (
                    mpc["establishment_failures"] <= static["establishment_failures"]
                ),
            }
            record = {
                "scenario_kind": kind,
                "scenario_id": scenario.scenario_id,
                "seed": seed,
                "events": events,
                "static": static,
                "mpc": mpc,
                "relative_reduction": reductions,
                "controller_decisions": controller.decision_count,
                "certified_decisions": controller.certified_decision_count,
                "gates": gates,
                "passes_all_guardrails": all(gates.values()),
            }
            records.append(record)
            completed += 1
            if progress:
                print(
                    "pilot result "
                    f"scenario={kind} seed={seed} "
                    f"ul_reduction={reductions['overload_area_seconds']['ul']:.6f} "
                    f"guardrails={record['passes_all_guardrails']}",
                    flush=True,
                )

    by_scenario = {}
    for kind in SCENARIO_KINDS:
        selected = [item for item in records if item["scenario_kind"] == kind]
        by_scenario[kind] = {
            "pairs": len(selected),
            "mean_ul_overload_area_relative_reduction": sum(
                item["relative_reduction"]["overload_area_seconds"]["ul"]
                for item in selected
            ) / len(selected),
            "minimum_ul_overload_area_relative_reduction": min(
                item["relative_reduction"]["overload_area_seconds"]["ul"]
                for item in selected
            ),
            "all_guardrails_pass": all(
                item["passes_all_guardrails"] for item in selected
            ),
        }
    mean_ul = sum(
        item["relative_reduction"]["overload_area_seconds"]["ul"]
        for item in records
    ) / len(records)
    all_guardrails = all(item["passes_all_guardrails"] for item in records)
    all_scenarios_improve = all(
        item["minimum_ul_overload_area_relative_reduction"] > 0
        for item in by_scenario.values()
    )
    passes = all_guardrails and all_scenarios_improve and mean_ul >= 0.20
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "development_only": True,
        "reserved_seeds_consumed": False,
        "manifest": {"path": str(manifest.resolve()), "sha256": _sha256(manifest)},
        "forecast_bundle": {
            "path": str(bundle_path.resolve()),
            "sha256": _sha256(bundle_path),
            "bundle_sha256": bundle.payload["bundle_sha256"],
        },
        "mpc_profile": {
            "path": str(profile_path.resolve()),
            "sha256": _sha256(profile_path),
            "profile_id": profile["profile_id"],
            "settings": asdict(settings),
        },
        "simulated_days_per_pair": steps * base.step_seconds / 86_400,
        "paired_runs": len(records),
        "mean_ul_overload_area_relative_reduction": mean_ul,
        "all_guardrails_pass": all_guardrails,
        "all_scenarios_improve_ul": all_scenarios_improve,
        "reaches_20_percent_gate": passes,
        "decision": (
            "advance_to_full_campaign"
            if passes else "stop_before_full_campaign"
        ),
        "by_scenario": by_scenario,
        "pairs": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the four-day, twelve-pair cohort MPC pre-campaign pilot"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--forecast-bundle", type=Path, required=True)
    parser.add_argument("--mpc-profile", type=Path, required=True)
    parser.add_argument("--seed-start", type=int, default=32001)
    parser.add_argument("--seeds-per-scenario", type=int, default=3)
    parser.add_argument("--steps", type=int, default=2880)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seeds_per_scenario < 1:
        parser.error("--seeds-per-scenario must be positive")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite pilot evaluation: {args.output}")
    seeds_by_scenario: dict[ScenarioKind, list[int]] = {}
    cursor = args.seed_start
    for kind in SCENARIO_KINDS:
        seeds_by_scenario[kind] = list(range(cursor, cursor + args.seeds_per_scenario))
        cursor += args.seeds_per_scenario
    payload = evaluate_pilot(
        args.manifest,
        args.forecast_bundle,
        args.mpc_profile,
        seeds_by_scenario,
        steps=args.steps,
        progress=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "decision": payload["decision"],
        "paired_runs": payload["paired_runs"],
        "mean_ul_reduction": payload["mean_ul_overload_area_relative_reduction"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
