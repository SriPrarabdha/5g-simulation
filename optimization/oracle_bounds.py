"""Offline, non-deployable action-space bounds for macro steering.

The evaluator deliberately uses a continuous relaxation.  A decision variable
is the fraction of a selection group's arrivals placed on an eligible UPF in a
decision bucket.  Expected cohort survival carries that decision into later
buckets.  Consequently, the optimum is at least as optimistic as a realizable
weighted-rendezvous policy and is suitable as a reachability bound, not as a
controller implementation.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix, csr_matrix, eye, hstack, vstack

from simulator.macro.config import ScenarioConfig, ScenarioEvent


KnowledgeRegime = Literal[
    "arrival_only",
    "scheduled_fault",
    "clairvoyant_fault",
]


@dataclass(frozen=True, slots=True)
class OracleMetrics:
    overload_area_seconds: dict[str, float]
    dropped_bytes: dict[str, float]
    peak_safe_utilization: dict[str, float]


@dataclass(frozen=True, slots=True)
class OracleBoundResult:
    regime: str
    status: str
    message: str
    runtime_ms: int
    objective_ul_overload_area_seconds: float | None
    allocation: dict[tuple[str, int], dict[str, float]]
    metrics: OracleMetrics | None
    continuous_relaxation: bool = True
    deployable: bool = False


@dataclass(frozen=True, slots=True)
class _CapacityPath:
    safe_ul: np.ndarray
    safe_dl: np.ndarray
    physical_ul: np.ndarray
    physical_dl: np.ndarray
    session_capacity: np.ndarray
    admission_healthy: np.ndarray


def bucket_arrivals_from_steps(
    config: ScenarioConfig,
    arrivals_by_step: Sequence[Mapping[str, int]],
) -> dict[str, np.ndarray]:
    """Aggregate an exact simulator arrival trace into decision buckets."""

    if len(arrivals_by_step) != config.steps:
        raise ValueError("arrival trace length does not match scenario steps")
    bucket_steps = config.decision_interval_steps
    bucket_count = math.ceil(config.steps / bucket_steps)
    result = {
        group.key.selection_id: np.zeros(bucket_count, dtype=float)
        for group in config.groups
    }
    for step, arrivals in enumerate(arrivals_by_step):
        bucket = step // bucket_steps
        for group_id, count in arrivals.items():
            if group_id not in result:
                raise ValueError(f"arrival trace references unknown group {group_id}")
            if count < 0:
                raise ValueError("arrival counts must be non-negative")
            result[group_id][bucket] += count
    return result


def expected_bucket_arrivals(config: ScenarioConfig) -> dict[str, np.ndarray]:
    """Return deterministic expected arrivals after applying scenario events."""

    bucket_steps = config.decision_interval_steps
    bucket_count = math.ceil(config.steps / bucket_steps)
    factors = {group.key.selection_id: 1.0 for group in config.groups}
    events: dict[int, list[ScenarioEvent]] = {}
    for event in config.events:
        events.setdefault(event.step, []).append(event)
    result = {
        group.key.selection_id: np.zeros(bucket_count, dtype=float)
        for group in config.groups
    }
    for step in range(config.steps):
        for event in events.get(step, ()):
            if event.event_type == "arrival_factor":
                factors[event.group_id or ""] = event.arrival_factor or 0.0
        bucket = step // bucket_steps
        for group in config.groups:
            group_id = group.key.selection_id
            result[group_id][bucket] += group.arrivals_per_step * factors[group_id]
    return result


def static_capacity_allocation(
    config: ScenarioConfig,
) -> dict[tuple[str, int], dict[str, float]]:
    """Reproduce the static controller in the continuous bucket model."""

    path = _capacity_path(config, config.events)
    upf_index = {upf.upf_id: index for index, upf in enumerate(config.upfs)}
    bucket_count = path.safe_ul.shape[1]
    allocation: dict[tuple[str, int], dict[str, float]] = {}
    for group in config.groups:
        group_id = group.key.selection_id
        for bucket in range(bucket_count):
            scores = {
                upf_id: path.safe_ul[upf_index[upf_id], bucket]
                + path.safe_dl[upf_index[upf_id], bucket]
                for upf_id in group.eligible_upfs
                if path.admission_healthy[upf_index[upf_id], bucket]
            }
            total = sum(scores.values())
            allocation[(group_id, bucket)] = (
                {upf_id: score / total for upf_id, score in scores.items()}
                if total > 0
                else {}
            )
    return allocation


def evaluate_allocation(
    config: ScenarioConfig,
    bucket_arrivals: Mapping[str, np.ndarray],
    allocation: Mapping[tuple[str, int], Mapping[str, float]],
) -> OracleMetrics:
    """Evaluate a continuous allocation against the actual capacity path."""

    model = _CohortModel(config, bucket_arrivals)
    path = _capacity_path(config, config.events)
    vector = np.zeros(len(model.x_keys), dtype=float)
    for index, (group_id, bucket, upf_id) in enumerate(model.x_keys):
        vector[index] = allocation.get((group_id, bucket), {}).get(upf_id, 0.0)
    return _metrics_from_vector(config, model, path, vector)


def solve_new_session_bound(
    config: ScenarioConfig,
    bucket_arrivals: Mapping[str, np.ndarray],
    *,
    regime: KnowledgeRegime = "clairvoyant_fault",
    guardrail_metrics: OracleMetrics | None = None,
    timeout_seconds: float = 300.0,
) -> OracleBoundResult:
    """Solve a new-session-only oracle under the selected information regime.

    ``arrival_only`` learns capacity/health changes only when they occur.
    ``scheduled_fault`` additionally learns events at ``known_at_step``.
    ``clairvoyant_fault`` knows the complete path at the beginning of the day.
    All regimes know the full arrival trace and are evaluator-only.
    """

    if regime not in {"arrival_only", "scheduled_fault", "clairvoyant_fault"}:
        raise ValueError(f"unsupported knowledge regime: {regime}")
    model = _CohortModel(config, bucket_arrivals)
    actual_path = _capacity_path(config, config.events)
    if regime == "clairvoyant_fault":
        return _solve_model(
            config,
            model,
            actual_path,
            actual_path,
            regime=regime,
            guardrail_metrics=guardrail_metrics,
            fixed_allocation={},
            timeout_seconds=timeout_seconds,
        )

    # Receding information sets are evaluated only when capacity knowledge
    # changes.  Allocations already issued are frozen; future capacities are
    # projected from the currently known event set.
    boundary_steps = {0, config.steps}
    for event in config.events:
        if event.event_type not in {"capacity_factor", "health"}:
            continue
        boundary_steps.add(event.step)
        if regime == "scheduled_fault" and event.known_at_step is not None:
            boundary_steps.add(event.known_at_step)
    bucket_steps = config.decision_interval_steps
    boundaries = sorted({min(config.steps, math.ceil(step / bucket_steps) * bucket_steps) for step in boundary_steps})
    fixed: dict[tuple[str, int], dict[str, float]] = {}
    last_result: OracleBoundResult | None = None
    started = time.perf_counter()
    for start_step, end_step in zip(boundaries, boundaries[1:]):
        known = _known_events(config, regime, start_step)
        planning_path = _capacity_path(config, known)
        remaining = timeout_seconds - (time.perf_counter() - started)
        if remaining <= 0:
            break
        last_result = _solve_model(
            config,
            model,
            planning_path,
            actual_path,
            regime=regime,
            # A causal policy cannot use a full-day realized guardrail as an
            # optimization constraint without leaking the future fault path.
            # Guardrails are therefore assessed on the final replay below.
            guardrail_metrics=None,
            fixed_allocation=fixed,
            timeout_seconds=remaining,
        )
        if last_result.status != "optimal":
            return last_result
        start_bucket = start_step // bucket_steps
        end_bucket = math.ceil(end_step / bucket_steps)
        for key, weights in last_result.allocation.items():
            if start_bucket <= key[1] < end_bucket:
                fixed[key] = weights
    if last_result is None:
        raise RuntimeError("oracle produced no information interval")
    metrics = evaluate_allocation(config, bucket_arrivals, fixed)
    return OracleBoundResult(
        regime=regime,
        status="optimal",
        message="receding-information continuous relaxation solved",
        runtime_ms=round((time.perf_counter() - started) * 1000),
        objective_ul_overload_area_seconds=metrics.overload_area_seconds["ul"],
        allocation=fixed,
        metrics=metrics,
    )


def solve_bounded_migration_bound(
    config: ScenarioConfig,
    bucket_arrivals: Mapping[str, np.ndarray],
    *,
    migration_fraction_per_bucket: float = 0.1,
    guardrail_metrics: OracleMetrics | None = None,
    timeout_seconds: float = 300.0,
) -> OracleBoundResult:
    """Solve an optimistic bounded established-session relocation relaxation.

    At most ``migration_fraction_per_bucket`` of each group's active population
    may change UPF share in one decision bucket (half-L1 turnover).  Cohort age
    is aggregated, so this is intentionally an upper bound on a concrete
    migration implementation, not an assertion that C-DOT exposes the action.
    """

    if not 0 <= migration_fraction_per_bucket <= 1:
        raise ValueError("migration_fraction_per_bucket must be in [0, 1]")
    model = _ActiveShareModel(config, bucket_arrivals)
    path = _capacity_path(config, config.events)
    started = time.perf_counter()
    nz = len(model.z_keys)
    load_rows = len(config.upfs) * model.bucket_count
    change_count = sum(
        len(group.eligible_upfs) * (model.bucket_count - 1)
        for group in config.groups
    )
    sul = slice(nz, nz + load_rows)
    sdl = slice(sul.stop, sul.stop + load_rows)
    dul = slice(sdl.stop, sdl.stop + load_rows)
    ddl = slice(dul.stop, dul.stop + load_rows)
    change = slice(ddl.stop, ddl.stop + change_count)
    variable_count = change.stop
    seconds = config.step_seconds * config.decision_interval_steps

    objective = np.zeros(variable_count)
    objective[sul] = seconds / np.maximum(path.safe_ul.reshape(-1), 1e-12)
    objective[sdl] = 1e-10 * seconds / np.maximum(path.safe_dl.reshape(-1), 1e-12)

    equality_rows: list[int] = []
    equality_columns: list[int] = []
    for row, columns in enumerate(model.columns_by_group_bucket.values()):
        equality_rows.extend([row] * len(columns))
        equality_columns.extend(columns)
    equality = coo_matrix(
        (np.ones(len(equality_rows)), (equality_rows, equality_columns)),
        shape=(len(model.columns_by_group_bucket), variable_count),
    ).tocsr()

    zero_z = csr_matrix((load_rows, nz))
    identity = eye(load_rows, format="csr")
    zero_load = csr_matrix((load_rows, load_rows))
    zero_change = csr_matrix((load_rows, change_count))
    inequalities = [
        hstack([model.ul, -identity, zero_load, zero_load, zero_load, zero_change], format="csr"),
        hstack([model.dl, zero_load, -identity, zero_load, zero_load, zero_change], format="csr"),
        hstack([
            model.sessions,
            csr_matrix((load_rows, variable_count - nz)),
        ], format="csr"),
        hstack([zero_z, identity, zero_load, -identity, zero_load, zero_change], format="csr"),
        hstack([zero_z, zero_load, identity, zero_load, -identity, zero_change], format="csr"),
    ]
    rhs = [
        path.safe_ul.reshape(-1),
        path.safe_dl.reshape(-1),
        path.session_capacity.reshape(-1),
        (path.physical_ul - path.safe_ul).reshape(-1),
        (path.physical_dl - path.safe_dl).reshape(-1),
    ]

    variation_rows: list[int] = []
    variation_columns: list[int] = []
    variation_data: list[float] = []
    budget_rows: list[int] = []
    budget_columns: list[int] = []
    change_index = change.start
    variation_row = 0
    budget_row = 0
    for group in config.groups:
        group_id = group.key.selection_id
        for bucket in range(1, model.bucket_count):
            for upf_id in group.eligible_upfs:
                current = model.z_index[(group_id, bucket, upf_id)]
                previous = model.z_index[(group_id, bucket - 1, upf_id)]
                # z_t - z_(t-1) <= q and its negative counterpart.
                variation_rows.extend([variation_row] * 3)
                variation_columns.extend([current, previous, change_index])
                variation_data.extend([1.0, -1.0, -1.0])
                variation_row += 1
                variation_rows.extend([variation_row] * 3)
                variation_columns.extend([current, previous, change_index])
                variation_data.extend([-1.0, 1.0, -1.0])
                variation_row += 1
                budget_rows.append(budget_row)
                budget_columns.append(change_index)
                change_index += 1
            budget_row += 1
    variation = coo_matrix(
        (variation_data, (variation_rows, variation_columns)),
        shape=(variation_row, variable_count),
    ).tocsr()
    budget = coo_matrix(
        (np.ones(len(budget_rows)), (budget_rows, budget_columns)),
        shape=(budget_row, variable_count),
    ).tocsr()
    inequalities.extend([variation, budget])
    rhs.extend([
        np.zeros(variation_row),
        np.full(budget_row, 2.0 * migration_fraction_per_bucket),
    ])

    if guardrail_metrics is not None:
        dl_coefficients = np.zeros(variable_count)
        dl_coefficients[sdl] = seconds / np.maximum(path.safe_dl.reshape(-1), 1e-12)
        inequalities.append(csr_matrix(dl_coefficients[None, :]))
        rhs.append(np.array([guardrail_metrics.overload_area_seconds["dl"]]))
        drop_factor = seconds * 1_000_000 / 8
        for variable_slice, direction in ((dul, "ul"), (ddl, "dl")):
            coefficients = np.zeros(variable_count)
            coefficients[variable_slice] = drop_factor
            inequalities.append(csr_matrix(coefficients[None, :]))
            rhs.append(np.array([guardrail_metrics.dropped_bytes[direction]]))

    lower = np.zeros(variable_count)
    upper = np.full(variable_count, np.inf)
    upper[:nz] = 1.0
    for column, (_, bucket, upf_id) in enumerate(model.z_keys):
        if bucket == 0 and not path.admission_healthy[model.upf_index[upf_id], bucket]:
            upper[column] = 0.0
    result = linprog(
        objective,
        A_ub=vstack(inequalities, format="csr"),
        b_ub=np.concatenate(rhs),
        A_eq=equality,
        b_eq=np.ones(equality.shape[0]),
        bounds=np.column_stack((lower, upper)),
        method="highs",
        options={"time_limit": max(0.001, timeout_seconds)},
    )
    runtime_ms = round((time.perf_counter() - started) * 1000)
    regime = f"bounded_migration_{migration_fraction_per_bucket:g}_per_bucket"
    if not result.success or result.x is None:
        status = "timeout" if result.status == 1 else "infeasible" if result.status == 2 else "error"
        return OracleBoundResult(
            regime, status, result.message, runtime_ms, None, {}, None
        )
    allocation: dict[tuple[str, int], dict[str, float]] = {}
    for key, columns in model.columns_by_group_bucket.items():
        weights = {
            model.z_keys[column][2]: float(result.x[column])
            for column in columns
            if result.x[column] > 1e-9
        }
        total = sum(weights.values())
        allocation[key] = {upf_id: value / total for upf_id, value in weights.items()}
    metrics = _active_share_metrics(config, model, path, result.x[:nz])
    return OracleBoundResult(
        regime=regime,
        status="optimal",
        message=result.message,
        runtime_ms=runtime_ms,
        objective_ul_overload_area_seconds=metrics.overload_area_seconds["ul"],
        allocation=allocation,
        metrics=metrics,
    )


def _known_events(
    config: ScenarioConfig,
    regime: KnowledgeRegime,
    at_step: int,
) -> tuple[ScenarioEvent, ...]:
    known: list[ScenarioEvent] = []
    for event in config.events:
        if event.event_type not in {"capacity_factor", "health"}:
            continue
        if event.step <= at_step:
            known.append(event)
        elif (
            regime == "scheduled_fault"
            and event.known_at_step is not None
            and event.known_at_step <= at_step
        ):
            known.append(event)
    return tuple(known)


class _CohortModel:
    def __init__(
        self,
        config: ScenarioConfig,
        bucket_arrivals: Mapping[str, np.ndarray],
    ) -> None:
        self.bucket_steps = config.decision_interval_steps
        self.bucket_count = math.ceil(config.steps / self.bucket_steps)
        self.groups = {group.key.selection_id: group for group in config.groups}
        self.upf_index = {upf.upf_id: index for index, upf in enumerate(config.upfs)}
        self.bucket_arrivals = {
            group_id: np.asarray(values, dtype=float)
            for group_id, values in bucket_arrivals.items()
        }
        if set(self.bucket_arrivals) != set(self.groups):
            raise ValueError("bucket arrivals must exactly match scenario groups")
        if any(values.shape != (self.bucket_count,) for values in self.bucket_arrivals.values()):
            raise ValueError("bucket arrival arrays have the wrong length")
        if any(np.any(values < 0) for values in self.bucket_arrivals.values()):
            raise ValueError("bucket arrivals must be non-negative")

        self.x_keys: list[tuple[str, int, str]] = []
        self.columns_by_group_bucket: dict[tuple[str, int], list[int]] = {}
        for group in config.groups:
            group_id = group.key.selection_id
            for bucket in range(self.bucket_count):
                columns: list[int] = []
                for upf_id in group.eligible_upfs:
                    columns.append(len(self.x_keys))
                    self.x_keys.append((group_id, bucket, upf_id))
                self.columns_by_group_bucket[(group_id, bucket)] = columns

        survival_by_range: dict[tuple[int, int], np.ndarray] = {}
        for group in config.groups:
            lifetime = (group.lifetime_steps_min, group.lifetime_steps_max)
            survival_by_range.setdefault(
                lifetime,
                _bucket_survival(
                    lifetime[0], lifetime[1], self.bucket_steps, self.bucket_count
                ),
            )
        rows: list[int] = []
        columns: list[int] = []
        data: list[float] = []
        for column, (group_id, source_bucket, upf_id) in enumerate(self.x_keys):
            group = self.groups[group_id]
            arrivals = self.bucket_arrivals[group_id][source_bucket]
            if arrivals <= 0:
                continue
            survival = survival_by_range[(group.lifetime_steps_min, group.lifetime_steps_max)]
            upf = self.upf_index[upf_id]
            values = arrivals * survival[: self.bucket_count - source_bucket]
            nonzero = np.flatnonzero(values > 0)
            rows.extend((upf * self.bucket_count + source_bucket + nonzero).tolist())
            columns.extend([column] * len(nonzero))
            data.extend(values[nonzero].tolist())
        shape = (len(config.upfs) * self.bucket_count, len(self.x_keys))
        self.sessions = coo_matrix((data, (rows, columns)), shape=shape).tocsr()
        ul_rates = np.array([
            self.groups[group_id].offered_ul_mbps_per_session
            for group_id, _, _ in self.x_keys
        ])
        dl_rates = np.array([
            self.groups[group_id].offered_dl_mbps_per_session
            for group_id, _, _ in self.x_keys
        ])
        self.ul = self.sessions.multiply(ul_rates).tocsr()
        self.dl = self.sessions.multiply(dl_rates).tocsr()


class _ActiveShareModel:
    def __init__(
        self,
        config: ScenarioConfig,
        bucket_arrivals: Mapping[str, np.ndarray],
    ) -> None:
        cohort = _CohortModel(config, bucket_arrivals)
        self.bucket_count = cohort.bucket_count
        self.upf_index = cohort.upf_index
        self.z_keys: list[tuple[str, int, str]] = []
        self.z_index: dict[tuple[str, int, str], int] = {}
        self.columns_by_group_bucket: dict[tuple[str, int], list[int]] = {}
        active_by_group: dict[str, np.ndarray] = {}
        for group in config.groups:
            group_id = group.key.selection_id
            survival = _bucket_survival(
                group.lifetime_steps_min,
                group.lifetime_steps_max,
                config.decision_interval_steps,
                self.bucket_count,
            )
            active_by_group[group_id] = np.convolve(
                cohort.bucket_arrivals[group_id], survival
            )[: self.bucket_count]
            for bucket in range(self.bucket_count):
                columns: list[int] = []
                for upf_id in group.eligible_upfs:
                    column = len(self.z_keys)
                    key = (group_id, bucket, upf_id)
                    columns.append(column)
                    self.z_keys.append(key)
                    self.z_index[key] = column
                self.columns_by_group_bucket[(group_id, bucket)] = columns
        rows: list[int] = []
        columns: list[int] = []
        data: list[float] = []
        for column, (group_id, bucket, upf_id) in enumerate(self.z_keys):
            rows.append(self.upf_index[upf_id] * self.bucket_count + bucket)
            columns.append(column)
            data.append(float(active_by_group[group_id][bucket]))
        shape = (len(config.upfs) * self.bucket_count, len(self.z_keys))
        self.sessions = coo_matrix((data, (rows, columns)), shape=shape).tocsr()
        group_by_id = {group.key.selection_id: group for group in config.groups}
        ul_rates = np.array([
            group_by_id[group_id].offered_ul_mbps_per_session
            for group_id, _, _ in self.z_keys
        ])
        dl_rates = np.array([
            group_by_id[group_id].offered_dl_mbps_per_session
            for group_id, _, _ in self.z_keys
        ])
        self.ul = self.sessions.multiply(ul_rates).tocsr()
        self.dl = self.sessions.multiply(dl_rates).tocsr()


def _bucket_survival(
    lifetime_min: int,
    lifetime_max: int,
    bucket_steps: int,
    bucket_count: int,
) -> np.ndarray:
    """Mean active fraction by bucket lag for uniform arrivals/lifetimes."""

    lifetime_count = lifetime_max - lifetime_min + 1
    result = np.zeros(bucket_count, dtype=float)
    offsets = np.arange(bucket_steps)
    for lag in range(bucket_count):
        age = lag * bucket_steps + offsets[:, None] - offsets[None, :] + 1
        not_arrived = age <= 0
        probability = np.where(
            not_arrived,
            0.0,
            np.where(
                age <= lifetime_min,
                1.0,
                np.maximum(0, lifetime_max - age + 1) / lifetime_count,
            ),
        )
        result[lag] = float(probability.mean())
        if lag > 0 and result[lag] == 0:
            break
    return result


def _capacity_path(
    config: ScenarioConfig,
    events: Sequence[ScenarioEvent],
) -> _CapacityPath:
    bucket_steps = config.decision_interval_steps
    bucket_count = math.ceil(config.steps / bucket_steps)
    upf_count = len(config.upfs)
    index = {upf.upf_id: position for position, upf in enumerate(config.upfs)}
    ul_factor = np.ones(upf_count)
    dl_factor = np.ones(upf_count)
    healthy = np.ones(upf_count, dtype=bool)
    by_step: dict[int, list[ScenarioEvent]] = {}
    for event in events:
        if event.event_type in {"capacity_factor", "health"}:
            by_step.setdefault(event.step, []).append(event)
    safe_ul = np.zeros((upf_count, bucket_count))
    safe_dl = np.zeros((upf_count, bucket_count))
    physical_ul = np.zeros((upf_count, bucket_count))
    physical_dl = np.zeros((upf_count, bucket_count))
    admission_healthy = np.zeros((upf_count, bucket_count), dtype=bool)
    for bucket in range(bucket_count):
        step = bucket * bucket_steps
        for event_step in sorted(key for key in by_step if key <= step):
            for event in by_step.pop(event_step):
                upf = index[event.upf_id or ""]
                if event.event_type == "capacity_factor":
                    if event.ul_factor is not None:
                        ul_factor[upf] = event.ul_factor
                    if event.dl_factor is not None:
                        dl_factor[upf] = event.dl_factor
                else:
                    healthy[upf] = event.health in {"healthy", "degraded"}
        for upf, profile in enumerate(config.upfs):
            available = healthy[upf]
            physical_ul[upf, bucket] = profile.capacity_ul_mbps * ul_factor[upf] if available else 0.0
            physical_dl[upf, bucket] = profile.capacity_dl_mbps * dl_factor[upf] if available else 0.0
            safe_ul[upf, bucket] = physical_ul[upf, bucket] * profile.safe_utilization_ul
            safe_dl[upf, bucket] = physical_dl[upf, bucket] * profile.safe_utilization_dl
            admission_healthy[upf, bucket] = available
    session_capacity = np.repeat(
        np.array([upf.session_capacity for upf in config.upfs], dtype=float)[:, None],
        bucket_count,
        axis=1,
    )
    return _CapacityPath(
        safe_ul=safe_ul,
        safe_dl=safe_dl,
        physical_ul=physical_ul,
        physical_dl=physical_dl,
        session_capacity=session_capacity,
        admission_healthy=admission_healthy,
    )


def _solve_model(
    config: ScenarioConfig,
    model: _CohortModel,
    planning_path: _CapacityPath,
    evaluation_path: _CapacityPath,
    *,
    regime: str,
    guardrail_metrics: OracleMetrics | None,
    fixed_allocation: Mapping[tuple[str, int], Mapping[str, float]],
    timeout_seconds: float,
) -> OracleBoundResult:
    started = time.perf_counter()
    nx = len(model.x_keys)
    load_rows = len(config.upfs) * model.bucket_count
    # Variables are x, safe UL/DL excess, then physical UL/DL excess.
    sul = slice(nx, nx + load_rows)
    sdl = slice(sul.stop, sul.stop + load_rows)
    dul = slice(sdl.stop, sdl.stop + load_rows)
    ddl = slice(dul.stop, dul.stop + load_rows)
    variable_count = ddl.stop

    objective = np.zeros(variable_count)
    seconds = config.step_seconds * config.decision_interval_steps
    safe_ul_flat = planning_path.safe_ul.reshape(-1)
    positive_ul = np.maximum(safe_ul_flat, 1e-12)
    objective[sul] = seconds / positive_ul
    objective[sdl] = 1e-10 * seconds / np.maximum(planning_path.safe_dl.reshape(-1), 1e-12)

    equality_rows: list[int] = []
    equality_columns: list[int] = []
    for row, columns in enumerate(model.columns_by_group_bucket.values()):
        equality_rows.extend([row] * len(columns))
        equality_columns.extend(columns)
    equality = coo_matrix(
        (np.ones(len(equality_rows)), (equality_rows, equality_columns)),
        shape=(len(model.columns_by_group_bucket), variable_count),
    ).tocsr()
    equality_rhs = np.ones(equality.shape[0])

    zero_x = csr_matrix((load_rows, nx))
    identity = eye(load_rows, format="csr")
    zero = csr_matrix((load_rows, load_rows))
    safe_ul_rows = hstack([model.ul, -identity, zero, zero, zero], format="csr")
    safe_dl_rows = hstack([model.dl, zero, -identity, zero, zero], format="csr")
    session_rows = hstack([
        model.sessions,
        csr_matrix((load_rows, variable_count - nx)),
    ], format="csr")
    drop_ul_rows = hstack([zero_x, identity, zero, -identity, zero], format="csr")
    drop_dl_rows = hstack([zero_x, zero, identity, zero, -identity], format="csr")
    inequalities = [safe_ul_rows, safe_dl_rows, session_rows, drop_ul_rows, drop_dl_rows]
    rhs = [
        planning_path.safe_ul.reshape(-1),
        planning_path.safe_dl.reshape(-1),
        planning_path.session_capacity.reshape(-1),
        (planning_path.physical_ul - planning_path.safe_ul).reshape(-1),
        (planning_path.physical_dl - planning_path.safe_dl).reshape(-1),
    ]

    if guardrail_metrics is not None:
        dl_coefficients = np.zeros(variable_count)
        dl_coefficients[sdl] = seconds / np.maximum(
            planning_path.safe_dl.reshape(-1), 1e-12
        )
        inequalities.append(csr_matrix(dl_coefficients[None, :]))
        rhs.append(np.array([guardrail_metrics.overload_area_seconds["dl"]]))
        drop_factor = seconds * 1_000_000 / 8
        for variable_slice, direction in ((dul, "ul"), (ddl, "dl")):
            coefficients = np.zeros(variable_count)
            coefficients[variable_slice] = drop_factor
            inequalities.append(csr_matrix(coefficients[None, :]))
            rhs.append(np.array([guardrail_metrics.dropped_bytes[direction]]))

    lower = np.zeros(variable_count)
    upper = np.full(variable_count, np.inf)
    upper[:nx] = 1.0
    for column, (group_id, bucket, upf_id) in enumerate(model.x_keys):
        fixed = fixed_allocation.get((group_id, bucket))
        if fixed is not None:
            value = fixed.get(upf_id, 0.0)
            lower[column] = value
            upper[column] = value
        elif not planning_path.admission_healthy[model.upf_index[upf_id], bucket]:
            upper[column] = 0.0

    result = linprog(
        objective,
        A_ub=vstack(inequalities, format="csr"),
        b_ub=np.concatenate(rhs),
        A_eq=equality,
        b_eq=equality_rhs,
        bounds=np.column_stack((lower, upper)),
        method="highs",
        options={"time_limit": max(0.001, timeout_seconds)},
    )
    runtime_ms = round((time.perf_counter() - started) * 1000)
    if not result.success or result.x is None:
        status = "timeout" if result.status == 1 else "infeasible" if result.status == 2 else "error"
        return OracleBoundResult(
            regime=regime,
            status=status,
            message=result.message,
            runtime_ms=runtime_ms,
            objective_ul_overload_area_seconds=None,
            allocation={},
            metrics=None,
        )
    allocation: dict[tuple[str, int], dict[str, float]] = {}
    for (group_id, bucket), columns in model.columns_by_group_bucket.items():
        weights = {
            model.x_keys[column][2]: float(result.x[column])
            for column in columns
            if result.x[column] > 1e-9
        }
        total = sum(weights.values())
        allocation[(group_id, bucket)] = {
            upf_id: value / total for upf_id, value in weights.items()
        }
    metrics = _metrics_from_vector(config, model, evaluation_path, result.x[:nx])
    return OracleBoundResult(
        regime=regime,
        status="optimal",
        message=result.message,
        runtime_ms=runtime_ms,
        objective_ul_overload_area_seconds=metrics.overload_area_seconds["ul"],
        allocation=allocation,
        metrics=metrics,
    )


def _metrics_from_vector(
    config: ScenarioConfig,
    model: _CohortModel,
    path: _CapacityPath,
    allocation: np.ndarray,
) -> OracleMetrics:
    seconds = config.step_seconds * config.decision_interval_steps
    overload: dict[str, float] = {}
    dropped: dict[str, float] = {}
    peak: dict[str, float] = {}
    for direction, matrix, safe, physical in (
        ("ul", model.ul, path.safe_ul, path.physical_ul),
        ("dl", model.dl, path.safe_dl, path.physical_dl),
    ):
        load = np.asarray(matrix @ allocation).reshape(safe.shape)
        safe_positive = np.maximum(safe, 1e-12)
        excess = np.maximum(0.0, load - safe)
        overload[direction] = float(np.sum(excess / safe_positive) * seconds)
        dropped[direction] = float(
            np.sum(np.maximum(0.0, load - physical)) * seconds * 1_000_000 / 8
        )
        peak[direction] = float(np.max(load / safe_positive))
    return OracleMetrics(overload, dropped, peak)


def _active_share_metrics(
    config: ScenarioConfig,
    model: _ActiveShareModel,
    path: _CapacityPath,
    allocation: np.ndarray,
) -> OracleMetrics:
    seconds = config.step_seconds * config.decision_interval_steps
    overload: dict[str, float] = {}
    dropped: dict[str, float] = {}
    peak: dict[str, float] = {}
    for direction, matrix, safe, physical in (
        ("ul", model.ul, path.safe_ul, path.physical_ul),
        ("dl", model.dl, path.safe_dl, path.physical_dl),
    ):
        load = np.asarray(matrix @ allocation).reshape(safe.shape)
        safe_positive = np.maximum(safe, 1e-12)
        overload[direction] = float(
            np.sum(np.maximum(0.0, load - safe) / safe_positive) * seconds
        )
        dropped[direction] = float(
            np.sum(np.maximum(0.0, load - physical)) * seconds * 1_000_000 / 8
        )
        peak[direction] = float(np.max(load / safe_positive))
    return OracleMetrics(overload, dropped, peak)
