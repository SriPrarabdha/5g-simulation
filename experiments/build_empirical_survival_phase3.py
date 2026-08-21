"""Build deployment-shaped Phase-3 survival bundles and calibration evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json
from optimization import (
    EmpiricalSurvivalProvider,
    SessionTelemetry,
    SurvivalTable,
    extract_session_lifecycles,
    static_survival_table,
    write_survival_tables,
)
from simulator.macro import load_scenario


SAMPLE_SIZES = (100, 1_000, 10_000)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _service_class(group: Any) -> str:
    return f"{group.key.dnn}|{group.key.snssai}"


def _oracle_table(group: Any, bucket_steps: int, count: int, at: datetime) -> SurvivalTable:
    lifetimes = range(group.lifetime_steps_min, group.lifetime_steps_max + 1)
    denominator = group.lifetime_steps_max - group.lifetime_steps_min + 1
    values = tuple(
        sum(lifetime > lag * bucket_steps for lifetime in lifetimes) / denominator
        for lag in range(count)
    )
    return SurvivalTable(values, "oracle-simulator-upper-bound", at, 0, "oracle")


def _uniform_naive_table(count: int, at: datetime) -> SurvivalTable:
    denominator = max(1, count - 1)
    return SurvivalTable(
        tuple(max(0.0, 1.0 - lag / denominator) for lag in range(count)),
        "uniform-naive-horizon", at, 0, "low", False, False,
    )


def _telemetry_for_group(
    group: Any, *, sample_count: int, cutoff: int, seed: int,
) -> list[SessionTelemetry]:
    rng = random.Random(f"phase3-survival:{seed}:{group.key.selection_id}:{sample_count}")
    service_class = _service_class(group)
    records = []
    # Variable entry times create ordinary administrative right-censoring.
    observation_width = max(group.lifetime_steps_max * 2, cutoff)
    for index in range(sample_count):
        started = rng.randrange(max(1, cutoff - observation_width), cutoff + 1)
        started = max(0, started)
        lifetime = rng.randint(group.lifetime_steps_min, group.lifetime_steps_max)
        ended = started + lifetime - 1
        records.append(SessionTelemetry(
            f"{group.key.selection_id}:{index}", group.key.selection_id,
            started, ended, service_class,
        ))
    return records


def _calibration(
    candidate: dict[str, SurvivalTable], oracle: dict[str, SurvivalTable],
    groups: tuple[Any, ...], horizon: int,
) -> dict[str, Any]:
    by_group = []
    weighted_absolute = 0.0
    weighted_oracle = 0.0
    for group in groups:
        group_id = group.key.selection_id
        estimate = candidate[group_id].values(horizon)
        truth = oracle[group_id].values(horizon)
        errors = [abs(float(a) - float(b)) for a, b in zip(estimate, truth)]
        load_weight = group.arrivals_per_step * (
            group.offered_ul_mbps_per_session + group.offered_dl_mbps_per_session
        )
        weighted_absolute += load_weight * sum(errors)
        weighted_oracle += load_weight * sum(float(value) for value in truth)
        by_group.append({
            "group_id": group_id,
            "source": candidate[group_id].source,
            "sample_count": candidate[group_id].sample_count,
            "mean_absolute_calibration_error": sum(errors) / len(errors),
            "max_absolute_calibration_error": max(errors),
            "load_exposure_relative_absolute_error": (
                sum(errors) / sum(float(value) for value in truth)
                if sum(float(value) for value in truth) else 0.0
            ),
            "by_horizon": [
                {
                    "lag": lag,
                    "estimated_survival": float(estimate[lag]),
                    "oracle_survival": float(truth[lag]),
                    "absolute_error": errors[lag],
                }
                for lag in range(horizon)
            ],
        })
    return {
        "groups": len(by_group),
        "mean_group_absolute_calibration_error": sum(
            item["mean_absolute_calibration_error"] for item in by_group
        ) / len(by_group),
        "max_group_horizon_absolute_calibration_error": max(
            item["max_absolute_calibration_error"] for item in by_group
        ),
        "load_exposure_relative_absolute_error": (
            weighted_absolute / weighted_oracle if weighted_oracle else 0.0
        ),
        "by_group": by_group,
    }


def build(manifest: Path, output_root: Path, *, seed: int, horizon: int) -> dict[str, Any]:
    scenario = load_scenario(manifest)
    generated_at = scenario.start_time + timedelta(days=60)
    groups = scenario.groups
    group_classes = {group.key.selection_id: _service_class(group) for group in groups}
    oracle = {
        group.key.selection_id: _oracle_table(
            group, scenario.decision_interval_steps, horizon, generated_at
        )
        for group in groups
    }
    uniform = {
        group.key.selection_id: _uniform_naive_table(horizon, generated_at)
        for group in groups
    }
    output_root.mkdir(parents=True, exist_ok=True)
    unmeasured = {
        "measured": False, "passed": False, "comparison_sha256": None,
        "criteria": {}, "reason": "pending_paired_mpc_comparison",
    }
    provenance = {
        "manifest": str(manifest.resolve()), "manifest_sha256": _sha256(manifest),
        "seed": seed, "horizon_windows": horizon,
        "oracle_is_non_deployable_upper_bound": True,
    }
    paths: dict[str, str] = {}
    for name, tables in (("oracle", oracle), ("uniform", uniform)):
        path = output_root / f"{name}.json"
        write_survival_tables(
            str(path), tables, guardrail_evidence=unmeasured,
            provenance={**provenance, "regime": name},
        )
        paths[name] = str(path.resolve())

    calibration: dict[str, Any] = {
        "oracle": _calibration(oracle, oracle, groups, horizon),
        "uniform": _calibration(uniform, oracle, groups, horizon),
    }
    censoring: dict[str, Any] = {}
    empirical_by_size: dict[int, dict[str, SurvivalTable]] = {}
    cutoff = max(group.lifetime_steps_max for group in groups) * 2
    for sample_size in SAMPLE_SIZES:
        telemetry = [
            record
            for group in groups
            for record in _telemetry_for_group(
                group, sample_count=sample_size, cutoff=cutoff, seed=seed
            )
        ]
        lifecycles = extract_session_lifecycles(
            telemetry, observed_through_step=cutoff
        )
        provider = EmpiricalSurvivalProvider(
            lifecycles,
            bucket_steps=scenario.decision_interval_steps,
            bucket_count=horizon,
            minimum_group_samples=100,
            generated_at=generated_at,
        )
        tables = provider.tables(group_classes, now=generated_at)
        empirical_by_size[sample_size] = tables
        name = f"empirical-n{sample_size}"
        path = output_root / f"{name}.json"
        write_survival_tables(
            str(path), tables, guardrail_evidence=unmeasured,
            provenance={
                **provenance, "regime": "empirical", "per_group_sample_size": sample_size,
                "completed_lifecycles": sum(row.completed for row in lifecycles),
                "right_censored_lifecycles": sum(not row.completed for row in lifecycles),
            },
        )
        paths[name] = str(path.resolve())
        calibration[name] = _calibration(tables, oracle, groups, horizon)
        censoring[name] = {
            "records": len(lifecycles),
            "completed": sum(row.completed for row in lifecycles),
            "right_censored": sum(not row.completed for row in lifecycles),
        }

    empirical = empirical_by_size[10_000]
    stale = {
        group_id: SurvivalTable(
            table.probabilities, table.source, generated_at - timedelta(days=30),
            table.sample_count, "low", table.upper_confidence, True,
        )
        for group_id, table in empirical.items()
    }
    static = {
        group.key.selection_id: static_survival_table(
            bucket_count=horizon, generated_at=generated_at
        )
        for group in groups
    }
    for name, tables in (("stale-empirical", stale), ("static-fallback", static)):
        path = output_root / f"{name}.json"
        write_survival_tables(
            str(path), tables, guardrail_evidence=unmeasured,
            provenance={**provenance, "regime": name},
        )
        paths[name] = str(path.resolve())
        calibration[name] = _calibration(tables, oracle, groups, horizon)

    # Explicitly exercise both sparse class pooling and no-telemetry fallback.
    sample_group = groups[0]
    sparse = extract_session_lifecycles(
        _telemetry_for_group(sample_group, sample_count=50, cutoff=cutoff, seed=seed),
        observed_through_step=cutoff,
    )
    fallback_provider = EmpiricalSurvivalProvider(
        sparse, bucket_steps=scenario.decision_interval_steps,
        bucket_count=horizon, minimum_group_samples=100, generated_at=generated_at,
    )
    exercise = fallback_provider.tables({
        sample_group.key.selection_id: _service_class(sample_group),
        "synthetic-same-class": _service_class(sample_group),
        "synthetic-no-telemetry": "absent-service-class",
    }, now=generated_at)

    report = {
        "schema_version": "empirical-survival-phase3/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "manifest": provenance,
        "sample_sizes_per_group": list(SAMPLE_SIZES),
        "right_censoring": censoring,
        "fallback_exercise": {
            group_id: {
                "source": table.source, "sample_count": table.sample_count,
                "confidence": table.confidence,
            }
            for group_id, table in exercise.items()
        },
        "calibration": calibration,
        "bundles": {
            name: {"path": path, "sha256": _sha256(Path(path))}
            for name, path in paths.items()
        },
    }
    atomic_json(output_root / "calibration-v1.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=46100)
    parser.add_argument("--horizon", type=int, default=12)
    args = parser.parse_args()
    if (args.output_root / "calibration-v1.json").exists():
        raise FileExistsError("refusing to overwrite Phase-3 survival evidence")
    result = build(args.manifest, args.output_root, seed=args.seed, horizon=args.horizon)
    print(json.dumps({
        "output": str((args.output_root / "calibration-v1.json").resolve()),
        "bundles": sorted(result["bundles"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
