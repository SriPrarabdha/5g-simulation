"""Build a provisional, auditable advisory from the first C-DOT CSV drop.

This adapter deliberately keeps packet-rate units.  It uses the repository's
short-history SeasonalNaiveForecaster and HiGHS allocation solver, but labels
the resulting policy as a controlled-test advisory: the CSV drop does not
contain packet sizes, calibrated capacity envelopes, per-class session
arrivals, cohort ages, or the referenced smf.yaml.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from forecasting import DemandObservation, SeasonalNaiveForecaster
from optimization import OptimizationConfig, solve_allocation
from schemas import Capacity, GroupKey, TimeWindow, UPFState


RATE_RE = re.compile(r"^([0-9.]+)\s*([kM]?)p/s$")
CLASS_RE = re.compile(
    r"^upf=(upf-\d+):loc=(\d+):dnn=(\d+):dscp(\d+)$"
)


@dataclass(frozen=True, slots=True)
class ClassColumn:
    index: int
    upf_id: str
    tac: int
    dnn_id: int
    dscp: int


def parse_rate(value: str) -> float:
    match = RATE_RE.fullmatch(value.strip())
    if match is None:
        return float(value)
    return float(match.group(1)) * {"": 1.0, "k": 1_000.0, "M": 1_000_000.0}[
        match.group(2)
    ]


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.reader(stream))
    if not rows:
        raise ValueError(f"empty CSV: {path}")
    return rows[0], rows[1:]


def _single(root: Path, pattern: str, *, longest: bool = False) -> Path:
    matches = list(root.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"no C-DOT CSV matches {pattern!r}")
    if longest:
        return max(matches, key=lambda path: sum(1 for _ in path.open(encoding="utf-8-sig")))
    if len(matches) != 1:
        raise ValueError(f"expected one CSV for {pattern!r}, found {len(matches)}")
    return matches[0]


def _parse_local(value: str, timezone_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed


def _bucket_start(value: datetime, minutes: int = 10) -> datetime:
    return value.replace(
        minute=(value.minute // minutes) * minutes, second=0, microsecond=0
    )


def _class_columns(header: list[str]) -> list[ClassColumn]:
    result: list[ClassColumn] = []
    for index, name in enumerate(header[1:], start=1):
        match = CLASS_RE.fullmatch(name)
        if match is None:
            raise ValueError(f"unsupported per-class column: {name!r}")
        result.append(
            ClassColumn(
                index=index,
                upf_id=match.group(1),
                tac=int(match.group(2)),
                dnn_id=int(match.group(3)),
                dscp=int(match.group(4)),
            )
        )
    return result


def _class_buckets(
    path: Path, timezone_name: str
) -> tuple[dict[datetime, dict[tuple[int, int, int], float]], dict[datetime, int], float]:
    header, rows = _read_csv(path)
    columns = _class_columns(header)
    samples: dict[datetime, dict[tuple[int, int, int], float]] = defaultdict(
        lambda: defaultdict(float)
    )
    counts: dict[datetime, int] = defaultdict(int)
    times: list[datetime] = []
    for row in rows:
        timestamp = _parse_local(row[0], timezone_name)
        times.append(timestamp)
        bucket = _bucket_start(timestamp)
        counts[bucket] += 1
        for column in columns:
            samples[bucket][(column.tac, column.dnn_id, column.dscp)] += parse_rate(
                row[column.index]
            )
    for bucket, values in samples.items():
        divisor = counts[bucket]
        for key in list(values):
            values[key] /= divisor
    differences = [
        (right - left).total_seconds()
        for left, right in zip(times, times[1:])
        if right > left
    ]
    cadence = statistics.median(differences)
    return dict(samples), dict(counts), cadence


def _dnn_names(root: Path) -> dict[int, str]:
    header, rows = _read_csv(_single(root, "DNN Mapping*.csv"))
    index_id = header.index("dnnid")
    index_name = header.index("dnn")
    return {int(row[index_id]): row[index_name] for row in rows}


def _constraints(root: Path) -> dict[int, tuple[str, ...]]:
    header, rows = _read_csv(_single(root, "UPF - tac constrain*.csv"))
    index_upf = header.index("upf")
    index_tac = header.index("tacid")
    grouped: dict[int, list[str]] = defaultdict(list)
    for row in rows:
        grouped[int(row[index_tac])].append(row[index_upf])
    return {tac: tuple(sorted(upfs)) for tac, upfs in grouped.items()}


def _complete_windows(
    ul_counts: Mapping[datetime, int],
    dl_counts: Mapping[datetime, int],
    cadence_seconds: float,
) -> list[datetime]:
    expected = 600.0 / cadence_seconds
    threshold = math.floor(expected * 0.9)
    return sorted(
        bucket
        for bucket in set(ul_counts) & set(dl_counts)
        if ul_counts[bucket] >= threshold and dl_counts[bucket] >= threshold
    )


def _observations(
    ul: Mapping[datetime, Mapping[tuple[int, int, int], float]],
    dl: Mapping[datetime, Mapping[tuple[int, int, int], float]],
    windows: Iterable[datetime],
    dnn_names: Mapping[int, str],
) -> dict[str, list[DemandObservation]]:
    windows = list(windows)
    keys = sorted({key for bucket in windows for key in set(ul[bucket]) | set(dl[bucket])})
    result: dict[str, list[DemandObservation]] = {}
    for tac, dnn_id, dscp in keys:
        group = GroupKey(f"tac-{tac}", dnn_names.get(dnn_id, f"dnn-{dnn_id}"), f"dscp-{dscp}")
        values: list[DemandObservation] = []
        for start in windows:
            values.append(
                DemandObservation(
                    window=TimeWindow(start, start + timedelta(minutes=10)),
                    group=group,
                    new_session_count=0.0,
                    new_ul_mbps=float(ul[start].get((tac, dnn_id, dscp), 0.0)),
                    new_dl_mbps=float(dl[start].get((tac, dnn_id, dscp), 0.0)),
                    quality_flags=("pps_proxy", "session_arrivals_unavailable"),
                )
            )
        result[group.selection_id] = values
    return result


def _point_forecast(history: list[float], method: str) -> float:
    if method == "last":
        return history[-1]
    if method == "ma3":
        return statistics.fmean(history[-3:])
    if method == "ma6":
        return statistics.fmean(history[-6:])
    if method == "seasonal3":
        return history[-3]
    raise ValueError(method)


def backtest(series_by_group: Mapping[str, list[DemandObservation]]) -> dict[str, float]:
    methods = ("last", "ma3", "ma6", "seasonal3")
    absolute_error = {name: 0.0 for name in methods}
    denominator = {name: 0.0 for name in methods}
    for observations in series_by_group.values():
        for field in ("new_ul_mbps", "new_dl_mbps"):
            values = [float(getattr(item, field)) for item in observations]
            for target in range(6, len(values)):
                for method in methods:
                    predicted = _point_forecast(values[:target], method)
                    absolute_error[method] += abs(values[target] - predicted)
                    denominator[method] += abs(values[target])
    return {
        method: absolute_error[method] / denominator[method]
        if denominator[method]
        else 0.0
        for method in methods
    }


def _forecast(
    series_by_group: Mapping[str, list[DemandObservation]], data_end: datetime
):
    forecaster = SeasonalNaiveForecaster(season_steps=3)
    source_end = max(items[-1].window.end for items in series_by_group.values())
    target_start = _bucket_start(data_end) + timedelta(minutes=10)
    if target_start <= source_end:
        target_start = source_end
    horizon = round((target_start - source_end).total_seconds() / 600) + 1
    if horizon not in {1, 2}:
        raise ValueError(f"short-history forecaster cannot span {horizon} windows")
    target = TimeWindow(target_start, target_start + timedelta(minutes=10))
    forecasts = [
        forecaster.predict(
            items,
            issued_at=source_end,
            target_window=target,
            horizon_steps=horizon,
        )
        for items in series_by_group.values()
    ]
    return forecaster, forecasts, source_end, target


def _identity_audit(
    root: Path, constraints: Mapping[int, tuple[str, ...]]
) -> dict[str, object]:
    flow: dict[tuple[str, int], float] = defaultdict(float)
    for pattern in ("UPF wise Uplink*.csv", "UPF wise Downlink*.csv"):
        header, rows = _read_csv(_single(root, pattern))
        columns = _class_columns(header)
        for row in rows:
            for column in columns:
                flow[(column.upf_id, column.tac)] += parse_rate(row[column.index])
    total = sum(flow.values())

    def violation(rename: Mapping[str, str]) -> float:
        return sum(
            value
            for (metric_upf, tac), value in flow.items()
            if rename[metric_upf] not in constraints[tac]
        )

    metric_ids = tuple(f"upf-{index}" for index in range(1, 5))
    candidates = []
    for permutation in itertools.permutations(metric_ids):
        rename = dict(zip(metric_ids, permutation))
        candidates.append((violation(rename), rename))
    candidates.sort(key=lambda item: item[0])
    identity = {upf: upf for upf in metric_ids}
    return {
        "literal_violation_fraction": violation(identity) / total if total else 0.0,
        "best_metric_to_constraint_label_permutation": candidates[0][1],
        "best_permutation_violation_fraction": candidates[0][0] / total if total else 0.0,
        "permutation_is_inference_not_confirmed_mapping": True,
    }


def _inactive_tacs(
    ul: Mapping[datetime, Mapping[tuple[int, int, int], float]],
    dl: Mapping[datetime, Mapping[tuple[int, int, int], float]],
) -> list[int]:
    totals: dict[int, float] = defaultdict(float)
    for buckets in (ul, dl):
        for values in buckets.values():
            for (tac, _dnn, _dscp), value in values.items():
                totals[tac] += value
    return sorted(tac for tac, value in totals.items() if value == 0)


def _session_reset_audit(root: Path) -> dict[str, object]:
    per_file: dict[str, object] = {}
    for index in range(4):
        path = _single(root, f"UPF-{index}-Total Active Sessions*.csv")
        _header, rows = _read_csv(path)
        values = [float(row[1]) for row in rows]
        changes = [right - left for left, right in zip(values, values[1:])]
        per_file[f"UPF-file-{index}"] = {
            "minimum": min(values),
            "maximum": max(values),
            "negative_changes": sum(change < 0 for change in changes),
            "largest_single_sample_drop": min(changes, default=0.0),
        }
    return {
        "per_file": per_file,
        "contradicts_no_detach_description": any(
            item["negative_changes"] > 0 for item in per_file.values()
        ),
    }


def _trace_end(root: Path, timezone_name: str) -> tuple[datetime, datetime, int]:
    path = _single(root, "Uplink + Downlink*.csv", longest=True)
    _header, rows = _read_csv(path)
    times = [_parse_local(row[0], timezone_name) for row in rows]
    return min(times), max(times), len(times)


def _aggregate_last_window(root: Path, timezone_name: str) -> dict[str, dict[str, float]]:
    path = _single(root, "Uplink + Downlink*.csv", longest=True)
    header, rows = _read_csv(path)
    grouped: dict[datetime, list[list[str]]] = defaultdict(list)
    for row in rows:
        grouped[_bucket_start(_parse_local(row[0], timezone_name))].append(row)
    # Last full 10-minute window at the 15-second aggregate cadence.
    bucket, selected = max(
        ((start, items) for start, items in grouped.items() if len(items) >= 36),
        key=lambda item: item[0],
    )
    result: dict[str, dict[str, float]] = {
        f"upf-{index}": {"ul_pps": 0.0, "dl_pps": 0.0}
        for index in range(1, 5)
    }
    for upf_index in range(1, 5):
        for direction, prefix in (("ul_pps", "UL"), ("dl_pps", "DL")):
            column = header.index(f"{prefix} N3 upf-{upf_index}")
            result[f"upf-{upf_index}"][direction] = statistics.fmean(
                parse_rate(row[column]) for row in selected
            )
    result["window"] = {
        "start": bucket.isoformat(),
        "end": (bucket + timedelta(minutes=10)).isoformat(),
    }
    return result


def _run_highs(
    forecasts,
    constraints: Mapping[int, tuple[str, ...]],
    source_end: datetime,
) -> dict[str, object]:
    active = [
        forecast
        for forecast in forecasts
        if forecast.new_load_ul_mbps.p95 + forecast.new_load_dl_mbps.p95 > 0
    ]
    states = []
    for upf_index in range(1, 5):
        upf_id = f"upf-{upf_index}"
        eligible = [
            forecast.group.selection_id
            for forecast in active
            if upf_id in constraints[int(forecast.group.zone.split("-")[1])]
        ]
        states.append(
            UPFState(
                measurement_time=source_end,
                upf_id=upf_id,
                capacity_mbps=Capacity(1_000_000.0, 1_000_000.0),
                safe_utilization=Capacity(0.8, 0.8),
                session_capacity=1_000_000,
                session_safe_utilization=0.9,
                health="healthy",
                zone=f"upf-zone-{upf_index}",
                eligible_groups=eligible,
                path_latency_ms_by_zone={f"tac-{tac}": 0.0 for tac in constraints},
                state_ttl_seconds=3_600,
                calibration_version="unit-normalized-pps-proxy/not-capacity-calibration",
            )
        )
    result = solve_allocation(
        active,
        states,
        created_at=source_end,
        policy_version=1,
        config=OptimizationConfig(
            planning_quantile="p95",
            locality_cost=0.0,
            churn_cost=0.0,
            max_group_upf_weight=0.75,
        ),
    )
    return {
        "status": result.status,
        "message": result.message,
        "weights_by_group": {
            group.key.selection_id: {
                upf_id: weight for upf_id, weight in sorted(group.weights.items())
            }
            for group in (result.policy.groups if result.policy else [])
        },
        "projected_ul_pps_by_upf": result.projected_ul_mbps_by_upf,
        "projected_dl_pps_by_upf": result.projected_dl_mbps_by_upf,
        "capacity_values_are_unit_normalizers_not_measured_capacity": True,
        "cold_start_assumption": True,
    }


def topology_balancing_advisory(
    constraints: Mapping[int, tuple[str, ...]]
) -> dict[str, object]:
    """Return global scores that balance equal intended demand from each TAC.

    The supplied four-TAC topology has the exact positive ratio 1:2:3:2.  The
    numerical least-squares implementation keeps this reusable if the mapping
    changes in a later C-DOT drop.
    """
    from scipy.optimize import minimize

    upf_ids = sorted({upf for values in constraints.values() for upf in values})

    def loads(log_scores):
        scores = {upf: math.exp(log_scores[index]) for index, upf in enumerate(upf_ids)}
        projected = {upf: 0.0 for upf in upf_ids}
        for eligible in constraints.values():
            total = sum(scores[upf] for upf in eligible)
            for upf in eligible:
                projected[upf] += scores[upf] / total
        return scores, projected

    def objective(log_scores):
        _scores, projected = loads(log_scores)
        values = list(projected.values())
        mean = statistics.fmean(values)
        return statistics.fmean((value - mean) ** 2 for value in values) + 1e-9 * sum(
            value * value for value in log_scores
        )

    solution = minimize(objective, [0.0] * len(upf_ids), method="BFGS")
    scores, projected = loads(solution.x)
    total_score = sum(scores.values())
    normalized = {upf: scores[upf] / total_score for upf in upf_ids}
    minimum = min(normalized.values())
    ratios = {}
    for upf in upf_ids:
        ratio = normalized[upf] / minimum
        nearest_integer = round(ratio)
        ratios[upf] = (
            float(nearest_integer)
            if abs(ratio - nearest_integer) < 1e-3
            else round(ratio, 6)
        )
    conditional = {
        f"tac-{tac}": {
            upf: normalized[upf] / sum(normalized[item] for item in eligible)
            for upf in eligible
        }
        for tac, eligible in sorted(constraints.items())
    }
    return {
        "recommended_global_weight_ratio": ratios,
        "normalized_global_weights": normalized,
        "effective_weights_after_tac_constraint_normalization": conditional,
        "projected_equal_tac_load_units_by_upf": projected,
        "assumptions": [
            "equal offered demand for TAC 1, 2, 3, and 4 as stated in the email",
            "identical UPF capacity until C-DOT supplies calibrated envelopes",
            "the UPF/TAC constraint CSV is authoritative",
            "SMF normalizes global UPF weights across each TAC's eligible set",
        ],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_advisory(root: Path, timezone_name: str) -> dict[str, object]:
    dnn_names = _dnn_names(root)
    constraints = _constraints(root)
    ul_path = _single(root, "UPF wise Uplink*.csv")
    dl_path = _single(root, "UPF wise Downlink*.csv")
    ul, ul_counts, ul_cadence = _class_buckets(ul_path, timezone_name)
    dl, dl_counts, dl_cadence = _class_buckets(dl_path, timezone_name)
    if ul_cadence != dl_cadence:
        raise ValueError("UL and DL per-class cadences differ")
    windows = _complete_windows(ul_counts, dl_counts, ul_cadence)
    series = _observations(ul, dl, windows, dnn_names)
    trace_start, trace_end, aggregate_samples = _trace_end(root, timezone_name)
    forecaster, forecasts, source_end, target = _forecast(series, trace_end)
    forecast_rows = {
        forecast.group.selection_id: {
            "ul_pps": {
                "p50": forecast.new_load_ul_mbps.p50,
                "p90": forecast.new_load_ul_mbps.p90,
                "p95": forecast.new_load_ul_mbps.p95,
            },
            "dl_pps": {
                "p50": forecast.new_load_dl_mbps.p50,
                "p90": forecast.new_load_dl_mbps.p90,
                "p95": forecast.new_load_dl_mbps.p95,
            },
            "quality_flags": forecast.quality_flags,
        }
        for forecast in forecasts
    }
    return {
        "schema_version": "cdot-real-advisory/0.1",
        "status": "provisional_controlled_test_only",
        "created_at": datetime.now().astimezone().isoformat(),
        "input": {
            "directory": str(root.resolve()),
            "timezone_assumption": timezone_name,
            "csv_count": len(list(root.glob("*.csv"))),
            "aggregate_trace_start": trace_start.isoformat(),
            "aggregate_trace_end": trace_end.isoformat(),
            "aggregate_trace_hours": (trace_end - trace_start).total_seconds() / 3600,
            "aggregate_samples": aggregate_samples,
            "per_class_cadence_seconds": ul_cadence,
            "complete_ten_minute_windows": len(windows),
            "source_hashes": {
                path.name: _sha256(path) for path in sorted(root.glob("*.csv"))
            },
        },
        "forecaster": {
            "model_version": forecaster.model_version,
            "selection_basis": "email-declared 30-minute loop plus descriptive rolling replay",
            "backtest_wape": backtest(series),
            "issued_at": source_end.isoformat(),
            "target_window": {
                "start": target.start.isoformat(),
                "end": target.end.isoformat(),
            },
            "forecasts_by_group": forecast_rows,
            "limitations": [
                "forecasts carried PPS, not new-session arrivals",
                "replay is only descriptive because it uses the same short trace used to choose the 30-minute lag",
                "TAC 1 has zero classified traffic in this drop",
            ],
        },
        "highs_optimizer": _run_highs(forecasts, constraints, source_end),
        "basic_smf_advisory": topology_balancing_advisory(constraints),
        "mpc": {
            "run": False,
            "decision": "fail_closed_insufficient_real_state",
            "reasons": [
                "no per-class new-session counter",
                "no session age, lifetime, or active cohort state",
                "no calibrated directional PPS/Mbps and session capacities",
                "packet size is absent, so PPS cannot be converted to Mbps",
                "the referenced smf.yaml is absent",
                "UPF metric identity is ambiguous",
            ],
        },
        "observed_last_complete_aggregate_window": _aggregate_last_window(
            root, timezone_name
        ),
        "data_quality": {
            "identity": _identity_audit(root, constraints),
            "zero_traffic_tacs": _inactive_tacs(ul, dl),
            "session_resets": _session_reset_audit(root),
            "missing_expected_files": ["smf.yaml"],
        },
        "application_gate": {
            "automatic_application_allowed": False,
            "controlled_second_run_allowed_after": [
                "C-DOT confirms the metric-label to UPF-ID mapping",
                "C-DOT confirms SMF weight semantics and whether weights are global or per TAC/DNN",
                "the pre-run baseline is captured and a rollback-to-equal policy is ready",
            ],
        },
    }


def render_report(payload: Mapping[str, object]) -> str:
    input_data = payload["input"]
    forecaster = payload["forecaster"]
    quality = payload["data_quality"]
    identity = quality["identity"]
    advisory = payload["basic_smf_advisory"]
    weights = advisory["normalized_global_weights"]
    effective = advisory["effective_weights_after_tac_constraint_normalization"]
    highs = payload["highs_optimizer"]
    local_zone = ZoneInfo(input_data["timezone_assumption"])
    target_start_local = datetime.fromisoformat(
        forecaster["target_window"]["start"]
    ).astimezone(local_zone)
    target_end_local = datetime.fromisoformat(
        forecaster["target_window"]["end"]
    ).astimezone(local_zone)
    backtest_rows = "\n".join(
        f"| {name} | {value:.2%} |"
        for name, value in sorted(forecaster["backtest_wape"].items())
    )
    effective_rows = "\n".join(
        f"| {tac} | "
        + ", ".join(f"{upf}={weight:.1%}" for upf, weight in values.items())
        + " |"
        for tac, values in effective.items()
    )
    highs_rows = "\n".join(
        f"| {group} | "
        + ", ".join(f"{upf}={weight:.1%}" for upf, weight in values.items())
        + " |"
        for group, values in highs["weights_by_group"].items()
    )
    permutation = ", ".join(
        f"{source}→{target}"
        for source, target in identity[
            "best_metric_to_constraint_label_permutation"
        ].items()
    )
    return f"""# First C-DOT metrics review and provisional advisory

## Outcome

The existing synthetic trained bundles are not directly transferable to this
drop: their 96 group identities and 144-window feature history do not match the
24-window C-DOT `(TAC,DNN,DSCP)` trace. The repository's short-history
`seasonal-naive/3` forecaster and HiGHS optimizer do run end to end in PPS proxy
units. Cohort MPC correctly remains fail-closed because its required real
cohort state and capacity calibration are absent.

This is a **controlled second-run advisory, not an automatically deployable
policy**.

## Basic SMF advisory

Use the topology-safe global ratio **upf-1:upf-2:upf-3:upf-4 =
1:2:3:2** (normalized: {weights['upf-1']:.1%}, {weights['upf-2']:.1%},
{weights['upf-3']:.1%}, {weights['upf-4']:.1%}) only after C-DOT confirms the
UPF identity and weight-normalization semantics. Under the email's equal load
per TAC and the supplied TAC constraints, the effective shares are:

| Selection scope | Effective eligible-UPF shares |
|---|---|
{effective_rows}

Equal global weights produce a structural bias toward upf-1 because it is
eligible for every TAC. The 1:2:3:2 score exactly balances the four equal-TAC
load units when SMF renormalizes scores inside each eligible set.

## Forecast replay

The trace covers {input_data['aggregate_trace_hours']:.1f} hours and contains
{input_data['complete_ten_minute_windows']} complete ten-minute windows. The
email-declared 30-minute loop is visible in both packet rates and session
resets. Descriptive one-step carried-PPS WAPE is:

| Forecaster | WAPE |
|---|---:|
{backtest_rows}

`seasonal-naive/3` is the best of these existing short-history choices. Its
forecast targets {target_start_local.isoformat()} through
{target_end_local.isoformat()} ({input_data['timezone_assumption']}). This is carried PPS, not a forecast of
new sessions, so it is suitable for a test advisory and trace replay only.

## Trace-conditioned HiGHS output

HiGHS status: `{highs['status']}`. It used p95 carried-PPS forecasts, a 75%
per-group cap, equal unit-normalized UPF envelopes, and a cold-start assumption.
The detailed conditional result is:

| Group | Conditional weights |
|---|---|
{highs_rows}

These conditional weights are diagnostic. Prefer the simpler 1:2:3:2 global
advisory unless SMF confirms that it supports independent TAC/DNN policies.

## Blocking data-quality findings

- TAC 1 has zero classified traffic despite the four-TAC scenario.
- Under literal labels, {identity['literal_violation_fraction']:.1%} of class
  PPS appears on UPF/TAC pairs forbidden by the constraint CSV. The inferred
  permutation `{permutation}` reduces this to
  {identity['best_permutation_violation_fraction']:.1%}, but that is an
  inference and must be confirmed by C-DOT.
- Active-session gauges contain repeated downward resets, contradicting the
  statement that no subscribers detach. The loop appears to tear down sessions
  between passes.
- `smf.yaml` is not in the directory.
- Packet sizes, calibrated directional/session capacities, per-class session
  arrivals, and session lifetimes/ages are absent.

## What to ask C-DOT before the second run

1. Confirm the canonical UPF identity for every dashboard series and provide
   `smf.yaml`.
2. Confirm whether weights are global per UPF or scoped by TAC/DNN, their
   normalization rule, allowed range, update API, and rollback semantics.
3. Explain why TAC 1 is zero and why the observed class labels violate the
   supplied constraint mapping.
4. Add per-class session-create/session-delete counters, 5QI, packet/byte rate,
   session lifetime or age buckets, and calibrated UL/DL/session capacity.
5. Label loop boundaries and the exact times at which each advisory was
   activated.

The machine-readable policy, forecasts, hashes, and audit findings are in
`advisory.json` beside this report.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("cdot-upf-metrics/metrics"),
        help="directory containing the extracted C-DOT CSV files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/cdot-real/2026-08-20-first-drop"),
    )
    parser.add_argument("--timezone", default="Asia/Kolkata")
    args = parser.parse_args()
    payload = build_advisory(args.input, args.timezone)
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "advisory.json"
    report_path = args.output / "REPORT.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(render_report(payload), encoding="utf-8")
    print(json_path)
    print(report_path)


if __name__ == "__main__":
    main()
