"""Turn paired imperfect-versus-oracle development runs into guardrail evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json
from optimization import load_survival_tables, write_survival_tables


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pairs(report: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (str(row["scenario_kind"]), int(row["seed"])): row
        for row in report["pairs"]
    }


def _no_outcome_regression(report: dict[str, Any]) -> bool:
    guardrails = report.get("aggregate_guardrails", {})
    return bool(guardrails) and all(bool(value) for value in guardrails.values())


def evaluate(
    oracle: dict[str, Any], empirical: dict[str, Any], small: list[dict[str, Any]],
    stale: dict[str, Any], calibration: dict[str, Any],
) -> dict[str, Any]:
    oracle_pairs = _pairs(oracle)
    empirical_pairs = _pairs(empirical)
    if set(oracle_pairs) != set(empirical_pairs) or len(oracle_pairs) != 12:
        raise ValueError("survival guardrail requires the same 12 development pairs")
    deltas = []
    for key in sorted(oracle_pairs):
        oracle_value = float(oracle_pairs[key]["relative_reduction"]["overload_area_seconds"]["ul"])
        empirical_value = float(empirical_pairs[key]["relative_reduction"]["overload_area_seconds"]["ul"])
        deltas.append(empirical_value - oracle_value)
    mean_gap = sum(deltas) / len(deltas)
    exposure_error = float(
        calibration["calibration"]["empirical-n10000"]
        ["load_exposure_relative_absolute_error"]
    )
    stale_static_exact = all(
        abs(float(row["relative_reduction"][metric][direction])) <= 1e-12
        for row in stale["pairs"]
        for metric in ("overload_area_seconds", "dropped_bytes")
        for direction in ("ul", "dl")
    )
    stale_reasons = all(
        any(reason.startswith("stale_or_insufficient_survival:") for reason in row["decision_reasons"])
        for row in stale["pairs"]
    )
    oracle_timeouts = sum(
        int(row.get("solver_timeout_count", 0)) for row in oracle["pairs"]
    )
    empirical_timeouts = sum(
        int(row.get("solver_timeout_count", 0)) for row in empirical["pairs"]
    )
    empirical_errors = sum(
        int(row.get("solver_error_count", 0)) for row in empirical["pairs"]
    )
    criteria = {
        "same_12_development_pairs": len(deltas) == 12,
        "empirical_mean_pair_gap_vs_oracle_no_worse_than_5_points": mean_gap >= -0.05,
        "empirical_load_exposure_relative_absolute_error_at_most_5_percent": exposure_error <= 0.05,
        "empirical_no_dl_drop_or_establishment_regression": _no_outcome_regression(empirical),
        "n100_and_n1000_no_dl_drop_or_establishment_regression": all(
            _no_outcome_regression(report) for report in small
        ),
        "stale_empirical_returns_exactly_to_static": stale_static_exact and stale_reasons,
        "empirical_solver_errors_zero_and_timeouts_within_5_percent_of_oracle": (
            empirical_errors == 0
            and empirical_timeouts <= max(oracle_timeouts, 1) * 1.05
        ),
    }
    return {
        "schema_version": "empirical-survival-guardrail/1.0",
        "development_seeds": sorted({seed for _, seed in oracle_pairs}),
        "mean_pair_ul_improvement_gap_empirical_minus_oracle": mean_gap,
        "worst_pair_gap_empirical_minus_oracle": min(deltas),
        "load_exposure_relative_absolute_error": exposure_error,
        "oracle_solver_timeouts": oracle_timeouts,
        "empirical_solver_timeouts": empirical_timeouts,
        "empirical_solver_errors": empirical_errors,
        "criteria": criteria,
        "passed": all(criteria.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle-evaluation", required=True, type=Path)
    parser.add_argument("--empirical-evaluation", required=True, type=Path)
    parser.add_argument("--small-evaluation", required=True, action="append", type=Path)
    parser.add_argument("--stale-evaluation", required=True, type=Path)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--empirical-bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--guarded-bundle", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.guarded_bundle.exists():
        raise FileExistsError("refusing to overwrite survival guardrail evidence")
    load = lambda path: json.loads(path.read_text(encoding="utf-8"))
    result = evaluate(
        load(args.oracle_evaluation), load(args.empirical_evaluation),
        [load(path) for path in args.small_evaluation],
        load(args.stale_evaluation), load(args.calibration),
    )
    result["inputs"] = {
        "oracle_evaluation_sha256": _sha256(args.oracle_evaluation),
        "empirical_evaluation_sha256": _sha256(args.empirical_evaluation),
        "small_evaluation_sha256": [_sha256(path) for path in args.small_evaluation],
        "stale_evaluation_sha256": _sha256(args.stale_evaluation),
        "calibration_sha256": _sha256(args.calibration),
        "empirical_bundle_sha256": _sha256(args.empirical_bundle),
    }
    atomic_json(args.output, result)
    comparison_sha = _sha256(args.output)
    source_payload = load(args.empirical_bundle)
    evidence = {
        "measured": True,
        "passed": bool(result["passed"]),
        "comparison_sha256": comparison_sha,
        "comparison_path": str(args.output.resolve()),
        "criteria": result["criteria"],
    }
    write_survival_tables(
        str(args.guarded_bundle), load_survival_tables(str(args.empirical_bundle)),
        guardrail_evidence=evidence,
        provenance={
            **source_payload.get("provenance", {}),
            "guardrail_comparison_sha256": comparison_sha,
        },
    )
    print(json.dumps({
        "passed": result["passed"], "output": str(args.output.resolve()),
        "guarded_bundle": str(args.guarded_bundle.resolve()),
    }, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
