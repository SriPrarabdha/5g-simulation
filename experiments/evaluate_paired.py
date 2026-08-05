from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import mean
from typing import Any

from .run_campaign_shard import atomic_json


class EvaluationError(ValueError):
    pass


def _nested(payload: dict[str, Any], path: str) -> float:
    value: Any = payload
    for component in path.split("."):
        value = value[component]
    return float(value)


def _bootstrap_mean_ci(values: list[float], *, samples: int = 10_000) -> list[float]:
    if not values:
        raise EvaluationError("cannot bootstrap an empty sample")
    stream = random.Random(20260805)
    estimates = sorted(
        mean(stream.choice(values) for _ in values)
        for _ in range(samples)
    )
    return [estimates[int(0.025 * (samples - 1))], estimates[int(0.975 * (samples - 1))]]


def _load(root: Path) -> dict[tuple[str, str, int], dict[str, Any]]:
    records: dict[tuple[str, str, int], dict[str, Any]] = {}
    for path in sorted(root.rglob("metadata.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        key = (item["scenario_id"], item["controller"], int(item["seed"]))
        if key in records:
            raise EvaluationError(f"duplicate shard {key}")
        records[key] = item
    if not records:
        raise EvaluationError(f"no metadata shards below {root}")
    return records


def evaluate(
    root: Path,
    *,
    candidate: str = "predictive-highs-v1",
    static: str = "static-capacity-v1",
    reactive: str = "reactive-threshold-v1",
    bootstrap_samples: int = 10_000,
) -> dict[str, Any]:
    records = _load(root)
    scenarios = sorted({key[0] for key in records})
    metrics = {item["summary"]["primary_overload_metric"] for item in records.values()}
    if len(metrics) != 1:
        raise EvaluationError("shards disagree on the primary overload metric")
    primary_metric = metrics.pop()
    paired_rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        controller_seeds = {
            controller: {seed for item_scenario, item_controller, seed in records if item_scenario == scenario and item_controller == controller}
            for controller in (static, reactive, candidate)
        }
        if any(not seeds for seeds in controller_seeds.values()):
            raise EvaluationError(f"scenario {scenario} is missing a required controller")
        common = set.intersection(*controller_seeds.values())
        if any(seeds != common for seeds in controller_seeds.values()):
            raise EvaluationError(f"scenario {scenario} controller seeds are not exactly paired")
        for seed in sorted(common):
            summaries = {
                controller: records[(scenario, controller, seed)]["summary"]
                for controller in (static, reactive, candidate)
            }
            paired_rows.append({
                "scenario_id": scenario,
                "seed": seed,
                "static": _nested(summaries[static], primary_metric),
                "reactive": _nested(summaries[reactive], primary_metric),
                "candidate": _nested(summaries[candidate], primary_metric),
                "static_failures": int(summaries[static]["establishment_failures"]),
                "candidate_failures": int(summaries[candidate]["establishment_failures"]),
            })
    static_values = [row["static"] for row in paired_rows]
    reactive_values = [row["reactive"] for row in paired_rows]
    candidate_values = [row["candidate"] for row in paired_rows]
    versus_static = [candidate - baseline for candidate, baseline in zip(candidate_values, static_values)]
    versus_reactive = [candidate - baseline for candidate, baseline in zip(candidate_values, reactive_values)]
    static_mean = mean(static_values)
    candidate_mean = mean(candidate_values)
    reduction = (static_mean - candidate_mean) / static_mean if static_mean > 0 else None
    failure_delta = sum(row["candidate_failures"] - row["static_failures"] for row in paired_rows)
    enough_seeds = all(
        sum(row["scenario_id"] == scenario for row in paired_rows) >= 30
        for scenario in scenarios
    )
    gates = {
        "at_least_30_paired_seeds_per_scenario": enough_seeds,
        "at_least_20_percent_reduction_vs_static": reduction is not None and reduction >= 0.20,
        "no_regression_vs_reactive": mean(candidate_values) <= mean(reactive_values),
        "no_increase_in_session_failures": failure_delta <= 0,
    }
    directional_means: dict[str, dict[str, dict[str, float]]] = {}
    for metric in ("overload_area_seconds", "overload_duration_seconds"):
        directional_means[metric] = {}
        for direction in ("ul", "dl"):
            directional_means[metric][direction] = {
                controller: mean(
                    _nested(
                        records[(row["scenario_id"], controller, row["seed"])]["summary"],
                        f"{metric}.{direction}",
                    )
                    for row in paired_rows
                )
                for controller in (static, reactive, candidate)
            }
    return {
        "schema_version": "paired-evaluation/1.0",
        "primary_overload_metric": primary_metric,
        "candidate": candidate,
        "static": static,
        "reactive": reactive,
        "paired_seed_count": len(paired_rows),
        "scenario_count": len(scenarios),
        "means": {"static": static_mean, "reactive": mean(reactive_values), "candidate": candidate_mean},
        "relative_reduction_vs_static": reduction,
        "paired_difference_ci95": {
            "versus_static": _bootstrap_mean_ci(versus_static, samples=bootstrap_samples),
            "versus_reactive": _bootstrap_mean_ci(versus_reactive, samples=bootstrap_samples),
        },
        "session_failure_delta": failure_delta,
        "directional_overload_means": directional_means,
        "acceptance_gates": gates,
        "accepted": all(gates.values()),
        "directional_results_must_be_reviewed_separately": True,
        "pairs": paired_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate an exactly paired controller campaign")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--candidate", default="predictive-highs-v1")
    parser.add_argument("--static", default="static-capacity-v1")
    parser.add_argument("--reactive", default="reactive-threshold-v1")
    args = parser.parse_args()
    result = evaluate(args.root, candidate=args.candidate, static=args.static, reactive=args.reactive)
    atomic_json(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key != "pairs"}, indent=2, sort_keys=True))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
