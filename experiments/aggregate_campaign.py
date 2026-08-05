from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from .run_campaign_shard import atomic_json


class CampaignError(ValueError):
    pass


def load_shard(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        first_line = stream.readline()
    if not first_line:
        raise CampaignError(f"empty shard: {path}")
    record = json.loads(first_line)
    if record.get("record_type") != "simulation_metadata":
        raise CampaignError(f"invalid first record in {path}")
    record["path"] = str(path)
    return record


def aggregate(root: Path, expected_shards: int | None = None) -> dict[str, Any]:
    shard_paths = sorted(root.rglob("run.jsonl"))
    if not shard_paths:
        raise CampaignError(f"no run.jsonl shards below {root}")
    shards = [load_shard(path) for path in shard_paths]
    identities = {(item["scenario_id"], item["controller"], item["seed"]) for item in shards}
    if len(identities) != len(shards):
        raise CampaignError("duplicate scenario/controller/seed shards found")
    if expected_shards is not None and len(shards) != expected_shards:
        raise CampaignError(f"expected {expected_shards} shards, found {len(shards)}")

    def values(section: str, direction: str) -> list[float]:
        return [float(item[section][direction]) for item in shards]

    failure_values = [int(item["establishment_failures"]) for item in shards]
    return {
        "schema_version": "campaign-summary/1.0",
        "root": str(root.resolve()),
        "shard_count": len(shards),
        "scenarios": sorted({item["scenario_id"] for item in shards}),
        "controllers": sorted({item["controller"] for item in shards}),
        "seeds": sorted(int(item["seed"]) for item in shards),
        "mean_offered_bytes": {direction: mean(values("offered_bytes", direction)) for direction in ("ul", "dl")},
        "mean_carried_bytes": {direction: mean(values("carried_bytes", direction)) for direction in ("ul", "dl")},
        "mean_dropped_bytes": {direction: mean(values("dropped_bytes", direction)) for direction in ("ul", "dl")},
        "total_establishment_failures": sum(failure_values),
        "shards": shards,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and aggregate macro-campaign shards")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-shards", type=int)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = aggregate(args.root, args.expected_shards)
    atomic_json(args.output, summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "shards"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

