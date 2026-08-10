from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "optimizer-tuning-evaluation/1.0"
METRICS = (
    "overload_area_seconds",
    "overload_duration_seconds",
    "dropped_bytes",
    "residual_overload_area_seconds",
    "incremental_new_session_overload_area_seconds",
)


def _mean(rows: list[tuple[dict[str, Any], dict[str, Any]]], metric: str, direction: str, index: int) -> float:
    return statistics.fmean(pair[index][metric][direction] for pair in rows)


def evaluate(root: Path, *, campaign_prefix: str) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob(f"campaign={campaign_prefix}*/scenario=*/controller=*/seed=*/metadata.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        item["_metadata_path"] = str(path)
        records.append(item)
    static = {
        (item["scenario_id"], int(item["seed"])): item
        for item in records
        if item["controller"] == "static-capacity-v1"
    }
    if not static:
        raise ValueError("no static validation shards found")
    profiles = sorted({
        item["predictive_profile"]["profile_id"]
        for item in records
        if item.get("predictive_profile")
    })
    results: list[dict[str, Any]] = []
    for profile_id in profiles:
        candidates = {
            (item["scenario_id"], int(item["seed"])): item
            for item in records
            if (item.get("predictive_profile") or {}).get("profile_id") == profile_id
        }
        if set(candidates) != set(static):
            raise ValueError(f"profile {profile_id} is not exactly paired with static")
        rows = [(static[key]["summary"], candidates[key]["summary"]) for key in sorted(static)]
        metrics: dict[str, Any] = {}
        for metric in METRICS:
            metrics[metric] = {}
            for direction in ("ul", "dl"):
                baseline = _mean(rows, metric, direction, 0)
                candidate = _mean(rows, metric, direction, 1)
                metrics[metric][direction] = {
                    "static_mean": baseline,
                    "candidate_mean": candidate,
                    "relative_reduction": (
                        (baseline - candidate) / baseline if baseline else None
                    ),
                }
        failure_delta = sum(
            candidate["establishment_failures"] - baseline["establishment_failures"]
            for baseline, candidate in rows
        )
        required = [
            metrics["overload_area_seconds"][direction]["relative_reduction"]
            for direction in ("ul", "dl")
        ] + [
            metrics["dropped_bytes"][direction]["relative_reduction"]
            for direction in ("ul", "dl")
        ]
        gates = {
            "primary_ul_overload_area_improves": required[0] is not None and required[0] > 0,
            "no_dl_overload_area_regression": required[1] is not None and required[1] >= 0,
            "no_directional_drop_regression": all(
                value is not None and value >= 0 for value in required[2:]
            ),
            "no_session_failure_regression": failure_delta <= 0,
        }
        profile_record = next(
            item["predictive_profile"]
            for item in candidates.values()
            if item.get("predictive_profile")
        )
        results.append({
            "profile_id": profile_id,
            "profile": profile_record,
            "paired_validation_days": len(rows),
            "metrics": metrics,
            "session_failure_delta": failure_delta,
            "gates": gates,
            "accepted_for_fresh_seed_test": all(gates.values()),
        })
    results.sort(
        key=lambda item: (
            not item["accepted_for_fresh_seed_test"],
            -(
                item["metrics"]["overload_area_seconds"]["ul"]["relative_reduction"]
                or -float("inf")
            ),
            item["profile_id"],
        )
    )
    accepted = [item for item in results if item["accepted_for_fresh_seed_test"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "campaign_prefix": campaign_prefix,
        "validation_pairs": len(static),
        "selection_rule": {
            "tuning_data_only": True,
            "primary": "maximize total UL overload-area reduction versus exactly paired static",
            "guardrails": [
                "no DL overload-area regression",
                "no UL or DL dropped-byte regression",
                "no establishment-failure regression",
            ],
        },
        "selected_profile_id": accepted[0]["profile_id"] if accepted else None,
        "test_seeds_consumed": False,
        "decision": (
            "proceed_to_fresh_seed_test" if accepted
            else "stop_tuning_no_candidate_beats_static_guardrails"
        ),
        "profiles": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank predictive profiles on paired validation days")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--campaign-prefix", default="extreme-opt-validation-")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite tuning decision: {args.output}")
    result = evaluate(args.root, campaign_prefix=args.campaign_prefix)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "decision": result["decision"],
        "selected_profile_id": result["selected_profile_id"],
        "profiles": [
            {
                "profile_id": item["profile_id"],
                "accepted": item["accepted_for_fresh_seed_test"],
                "ul_overload_area_reduction": item["metrics"]["overload_area_seconds"]["ul"]["relative_reduction"],
                "dl_overload_area_reduction": item["metrics"]["overload_area_seconds"]["dl"]["relative_reduction"],
                "ul_drop_reduction": item["metrics"]["dropped_bytes"]["ul"]["relative_reduction"],
                "dl_drop_reduction": item["metrics"]["dropped_bytes"]["dl"]["relative_reduction"],
            }
            for item in result["profiles"]
        ],
    }, indent=2, sort_keys=True))
    return 0 if result["selected_profile_id"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
