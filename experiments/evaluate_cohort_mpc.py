from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecasting import TrainedForecastBundle
from optimization import CohortMPCConfig
from simulator.macro import Simulator, load_scenario
from simulator.macro.config import ScenarioEvent
from simulator.macro.controllers import CohortMPCController, StaticCapacityController


SCHEMA_VERSION = "cohort-mpc-development-evaluation/1.0"
RESERVED_SEEDS = frozenset({20260810, 20260811})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _development_scenario(base, seed: int, steps: int, notice_windows: int):
    if seed in RESERVED_SEEDS:
        raise ValueError(f"seed {seed} is reserved and cannot be consumed by development evaluation")
    rng = random.Random(seed)
    start = rng.randrange(max(base.decision_interval_steps * 6, steps // 3), max(base.decision_interval_steps * 7, steps * 2 // 3))
    start = start // base.decision_interval_steps * base.decision_interval_steps
    duration = rng.randrange(base.decision_interval_steps * 3, base.decision_interval_steps * 7)
    end = min(steps - 1, start + duration)
    end = end // base.decision_interval_steps * base.decision_interval_steps
    upf = rng.choice(base.upfs)
    factor = rng.uniform(0.05, 0.35)
    known_at = max(0, start - notice_windows * base.decision_interval_steps)
    retained_events = tuple(event for event in base.events if event.step < steps)
    faults = (
        ScenarioEvent(
            step=start,
            event_type="capacity_factor",
            upf_id=upf.upf_id,
            ul_factor=factor,
            dl_factor=min(0.65, factor + 0.15),
            known_at_step=known_at,
        ),
        ScenarioEvent(
            step=end,
            event_type="capacity_factor",
            upf_id=upf.upf_id,
            ul_factor=1.0,
            dl_factor=1.0,
            known_at_step=known_at,
        ),
    )
    return replace(
        base,
        scenario_id=f"{base.scenario_id}-mpc-dev-s{seed}",
        seed=seed,
        steps=steps,
        events=tuple(sorted((*retained_events, *faults), key=lambda event: event.step)),
    ), {
        "upf_id": upf.upf_id,
        "start_step": start,
        "end_step": end,
        "known_at_step": known_at,
        "ul_factor": factor,
    }


def evaluate(
    manifest: Path,
    bundle_path: Path,
    profile_path: Path,
    seeds: list[int],
    *,
    steps: int,
) -> dict[str, Any]:
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("development seeds must be non-empty and unique")
    if RESERVED_SEEDS.intersection(seeds):
        raise ValueError("reserved validation seeds cannot be consumed by development evaluation")
    base = load_scenario(manifest)
    if steps <= base.decision_interval_steps * 12:
        raise ValueError("development scenario must exceed the 12-window warm-up/horizon")
    bundle = TrainedForecastBundle.load(bundle_path)
    bundle.validate_groups(group.key for group in base.groups)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile.get("schema_version") != "cohort-mpc-profile/1.0":
        raise ValueError("unsupported cohort MPC profile schema")
    settings = CohortMPCConfig(**profile.get("mpc", {}))
    scenarios = []
    for seed in seeds:
        scenario, fault = _development_scenario(
            base, seed, steps, max(2, settings.horizon_windows // 2)
        )
        static = Simulator(scenario, StaticCapacityController()).run().summary
        controller = CohortMPCController(forecaster=bundle, mpc_config=settings)
        mpc = Simulator(scenario, controller).run().summary
        reductions = {
            direction: (
                (static["overload_area_seconds"][direction] - mpc["overload_area_seconds"][direction])
                / static["overload_area_seconds"][direction]
                if static["overload_area_seconds"][direction] > 0 else 0.0
            )
            for direction in ("ul", "dl")
        }
        gates = {
            "ul_overload_improves": reductions["ul"] > 0,
            "no_dl_overload_regression": (
                mpc["overload_area_seconds"]["dl"]
                <= static["overload_area_seconds"]["dl"]
            ),
            "no_directional_drop_regression": all(
                mpc["dropped_bytes"][direction]
                <= static["dropped_bytes"][direction]
                for direction in ("ul", "dl")
            ),
            "no_session_failure_regression": (
                mpc["establishment_failures"] <= static["establishment_failures"]
            ),
        }
        scenarios.append({
            "seed": seed,
            "fault": fault,
            "static": static,
            "mpc": mpc,
            "overload_area_relative_reduction": reductions,
            "controller_decisions": controller.decision_count,
            "certified_decisions": controller.certified_decision_count,
            "gates": gates,
            "passes_all_gates": all(gates.values()),
        })
    mean_ul_reduction = sum(
        item["overload_area_relative_reduction"]["ul"] for item in scenarios
    ) / len(scenarios)
    all_guardrails = all(item["passes_all_gates"] for item in scenarios)
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
        "seeds": seeds,
        "steps": steps,
        "mean_ul_overload_area_relative_reduction": mean_ul_reduction,
        "decision": (
            "development_candidate_reaches_20_percent_gate"
            if all_guardrails and mean_ul_reduction >= 0.2
            else "continue_mpc_development_not_release_candidate"
        ),
        "scenarios": scenarios,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate cohort MPC on randomized, non-reserved development faults"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--forecast-bundle", type=Path, required=True)
    parser.add_argument("--mpc-profile", type=Path, required=True)
    parser.add_argument("--seed", action="append", type=int, required=True)
    parser.add_argument("--steps", type=int, default=480)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite MPC evaluation: {args.output}")
    payload = evaluate(
        args.manifest,
        args.forecast_bundle,
        args.mpc_profile,
        args.seed,
        steps=args.steps,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "seeds": args.seed,
        "certified_decisions": sum(item["certified_decisions"] for item in payload["scenarios"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
