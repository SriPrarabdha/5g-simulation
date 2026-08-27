"""Joint allocation solve over every selection group at once.

The Codex version called ``solve_allocation`` once per ``(dnn, tac)`` group with
``existing_load_by_upf=[]``, so no UPF ever saw its combined load and nothing
could ever appear overloaded -- the optimizer had nothing to balance.  The LP in
``optimization/highs.py`` already couples groups through per-UPF capacity rows
(``highs.py:215-251``); it just has to be handed all of them together.

Units: every ``*_mbps`` field below carries **packets per second**.  C-DOT
publishes N3 rates in pps and never publishes byte rates, so converting would
mean inventing a packet size.  The schema field names are load-bearing elsewhere
in the codebase, so they stay -- but nothing in this pipeline or the UI may
present these numbers as Mbps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from optimization.highs import OptimizationConfig, solve_allocation
from schemas.forecast import Forecast, Quantiles
from schemas.policy import Policy
from schemas.common import GroupKey, TimeWindow
from schemas.upf import Capacity as SchemaCapacity, UPFState

from .config import LiveConfig
from .demand import DemandCube, parse_group_id

MODEL_VERSION = "cdot-ridge-conformal/1.0"
# Their SMF re-evaluates weights per PDU session establishment, and their loop
# attaches subscribers continuously, so the whole offered load is re-routable.
# Sessions already established stay pinned -- see the stickiness assumption in
# docs/cdot-live-demo-worklog.md.
_RESIDUAL_PPS = 0.0


class OptimizerError(RuntimeError):
    pass


@dataclass(slots=True)
class AllocationPlan:
    status: str
    weights: dict[str, dict[str, float]]
    integer_weights: dict[str, dict[str, int]]
    projected_load_pps: dict[str, dict[str, float]]
    baseline_load_pps: dict[str, dict[str, float]]
    max_safe_utilization: float | None
    solver_runtime_ms: int
    policy: Policy | None = None
    message: str | None = None
    eligibility: dict[int, list[str]] = field(default_factory=dict)
    unit: str = "pps"

    def hottest(self, which: str = "projected") -> tuple[str | None, float]:
        table = self.projected_load_pps if which == "projected" else self.baseline_load_pps
        best: tuple[str | None, float] = (None, 0.0)
        for upf, value in table.items():
            total = value.get("total", value.get("ul", 0.0) + value.get("dl", 0.0))
            if total >= best[1]:
                best = (upf, total)
        return best

    def as_dict(self) -> dict[str, Any]:
        hot_upf, hot_value = self.hottest("projected")
        base_upf, base_value = self.hottest("baseline")
        return {
            "status": self.status,
            "message": self.message,
            "unit": self.unit,
            "weights": self.weights,
            "integer_weights": self.integer_weights,
            "projected_load_pps": self.projected_load_pps,
            "baseline_load_pps": self.baseline_load_pps,
            "max_safe_utilization": self.max_safe_utilization,
            "solver_runtime_ms": self.solver_runtime_ms,
            "eligibility": {str(key): value for key, value in self.eligibility.items()},
            "hottest_projected": {"upf": hot_upf, "pps": hot_value},
            "hottest_baseline": {"upf": base_upf, "pps": base_value},
            "peak_reduction": (
                round(1.0 - hot_value / base_value, 4) if base_value > 0 else None
            ),
        }


# ------------------------------------------------------------------- bounds


def apply_bounds(
    target: dict[str, float],
    current: dict[str, float],
    *,
    min_share: float,
    max_share: float,
    max_step_delta: float,
) -> dict[str, float]:
    """Project a target weight vector onto the configured band, summing to one.

    Clamping and then renormalising does not work: clamp {0.95, 0.05} to a 0.75
    cap and renormalise and you get 0.9375, straight back over the cap.  This
    water-fills instead -- clamp, redistribute the excess or deficit across the
    entries that are still free, repeat -- so the band actually holds.

    ``max_step_delta`` is a fraction, not percentage points.  The Codex default
    of 0.10 meant four or more decisions to shift the ~40% of load this demo
    needs to move; on stage that reads as "nothing happened".  The config
    default is now a single unconstrained step.
    """
    upfs = sorted(set(target) | set(current))
    if not upfs:
        return {}
    raw: dict[str, float] = {}
    for upf in upfs:
        want = max(0.0, float(target.get(upf, 0.0)))
        have = float(current.get(upf, 0.0))
        if want <= 0.0 and have <= 0.0:
            continue
        raw[upf] = min(max(want, have - max_step_delta), have + max_step_delta)
    if not raw:
        share = 1.0 / len(upfs)
        return {upf: share for upf in upfs}

    count = len(raw)
    # A band that cannot contain a probability vector is a config error, but the
    # demo must not die on it: widen just enough to stay feasible.
    low = min(min_share, 1.0 / count)
    high = max(max_share, 1.0 / count)

    total = sum(raw.values())
    values = (
        {upf: value / total for upf, value in raw.items()}
        if total > 0
        else {upf: 1.0 / count for upf in raw}
    )
    for _ in range(count + 1):
        clamped = {upf: min(max(value, low), high) for upf, value in values.items()}
        excess = 1.0 - sum(clamped.values())
        if abs(excess) < 1e-12:
            return clamped
        free = [
            upf
            for upf, value in clamped.items()
            if (excess > 0 and value < high - 1e-12) or (excess < 0 and value > low + 1e-12)
        ]
        if not free:
            return clamped
        headroom = sum(
            (high - clamped[upf]) if excess > 0 else (clamped[upf] - low) for upf in free
        )
        if headroom <= 1e-12:
            return clamped
        values = dict(clamped)
        for upf in free:
            room = (high - clamped[upf]) if excess > 0 else (clamped[upf] - low)
            values[upf] = clamped[upf] + excess * room / headroom
    return {upf: min(max(value, low), high) for upf, value in values.items()}


def integer_weights(weights: dict[str, float], *, total: int = 100) -> dict[str, int]:
    """Largest-remainder rounding to integers summing to ``total``."""
    clean = {key: max(0.0, float(value)) for key, value in weights.items() if value > 0}
    scale = sum(clean.values())
    if not clean or not math.isfinite(scale) or scale <= 0:
        raise OptimizerError("weights must contain a positive finite value")
    scaled = {key: value / scale * total for key, value in clean.items()}
    result = {key: int(math.floor(value)) for key, value in scaled.items()}
    order = sorted(scaled, key=lambda key: (-(scaled[key] - result[key]), key))
    for key in order[: total - sum(result.values())]:
        result[key] += 1
    return {key: value for key, value in result.items() if value > 0}


# --------------------------------------------------------- schema translation


def build_forecasts(
    predictions: dict[str, dict[str, Any]],
    *,
    issued_at: datetime,
    horizon_seconds: int,
    model_version: str = MODEL_VERSION,
) -> list[Forecast]:
    """Wrap demand predictions in ``Forecast`` objects for the LP.

    ``horizon_steps`` is pinned to 1 because ``schemas/forecast.py`` restricts it
    to 1..8 while our internal horizon is 20 telemetry samples.  One decision
    window ahead is exactly what it means here; the sample count lives in the
    forecaster, not the contract.
    """
    issued_at = issued_at.astimezone(timezone.utc)
    target = TimeWindow(
        start=issued_at + timedelta(seconds=horizon_seconds),
        end=issued_at + timedelta(seconds=2 * horizon_seconds),
    )
    forecasts: list[Forecast] = []
    for selection_id, per_direction in sorted(predictions.items()):
        dnn, tac = parse_group_id(selection_id)
        ul = per_direction["ul"]
        dl = per_direction["dl"]
        if _p50(ul) <= 0.0 and _p50(dl) <= 0.0:
            continue  # a group with no traffic contributes nothing and only adds rows
        forecasts.append(
            Forecast(
                forecast_id=f"cdot-{selection_id}-{int(issued_at.timestamp())}",
                issued_at=issued_at,
                source_window_end=issued_at,
                target_window=target,
                horizon_steps=1,
                group=GroupKey(zone=f"tac-{tac}", dnn=dnn, snssai="dscp-0"),
                new_session_count=Quantiles(p50=0.0, p95=0.0, p90=0.0),
                new_load_ul_mbps=_quantiles(ul),
                new_load_dl_mbps=_quantiles(dl),
                existing_load_by_upf=[],
                model_version=model_version,
                quality_flags=["unit:pps"],
            )
        )
    if not forecasts:
        raise OptimizerError("every selection group forecast is zero")
    return forecasts


def _p50(value: Any) -> float:
    return float(getattr(value, "p50", value["p50"] if isinstance(value, dict) else value))


def _quantiles(value: Any) -> Quantiles:
    if isinstance(value, dict):
        p50, p90, p95 = value["p50"], value.get("p90"), value.get("p95")
    else:
        p50, p90, p95 = value.p50, value.p90, value.p95
    p50 = max(0.0, float(p50))
    p95 = max(p50, float(p95 if p95 is not None else p50))
    p90 = min(max(p50, float(p90 if p90 is not None else p50)), p95)
    return Quantiles(p50=p50, p95=p95, p90=p90)


def build_upf_states(
    config: LiveConfig,
    groups: Iterable[str],
    *,
    measurement_time: datetime,
    observed_eligibility: dict[int, set[str]] | None = None,
    health: dict[str, str] | None = None,
) -> list[UPFState]:
    """One state per UPF, with a **uniform** capacity.

    Uniform is the whole point.  Codex set each UPF's capacity to its own
    observed p99, which is circular -- it makes the idle upf-3 look as full as
    the saturated upf-1 and inverts the entire result.  Until C-DOT gives a real
    number, one placeholder ceiling applies to all four.
    """
    measurement_time = measurement_time.astimezone(timezone.utc)
    eligible_by_tac = config.eligibility(observed_eligibility)
    group_ids = list(groups)
    per_upf: dict[str, list[str]] = {upf: [] for upf in config.upf_ids}
    for selection_id in group_ids:
        _, tac = parse_group_id(selection_id)
        for upf in eligible_by_tac.get(tac, []):
            if upf in per_upf:
                per_upf[upf].append(selection_id)
    zones = sorted({f"tac-{parse_group_id(item)[1]}" for item in group_ids})
    capacity = config.capacity
    states: list[UPFState] = []
    for upf in config.upf_ids:
        states.append(
            UPFState(
                measurement_time=measurement_time,
                upf_id=upf,
                capacity_mbps=SchemaCapacity(ul=capacity.per_upf_pps, dl=capacity.per_upf_pps),
                safe_utilization=SchemaCapacity(
                    ul=capacity.safe_utilization, dl=capacity.safe_utilization
                ),
                # Sessions are not a binding resource here: C-DOT publishes no
                # trustworthy per-UPF session ceiling, and the session gauges
                # reset downward mid-run.  A zero session forecast plus a
                # nominal ceiling keeps that LP row slack.
                session_capacity=1_000_000,
                session_safe_utilization=capacity.safe_utilization,
                health=(health or {}).get(upf, "healthy"),
                zone=zones[0] if zones else "tac-0",
                eligible_groups=sorted(per_upf[upf]),
                # Uniform latency: we have no per-path measurement from C-DOT, and
                # a uniform value makes the locality term a per-group constant,
                # so it cannot bias the allocation.
                path_latency_ms_by_zone={zone: 1.0 for zone in zones},
                state_ttl_seconds=config.cadence.decision_stale_seconds,
                calibration_version="cdot-live/2.0",
            )
        )
    return states


# ------------------------------------------------------------------- the solve


def solve(
    cube: DemandCube,
    predictions: dict[str, dict[str, Any]],
    config: LiveConfig,
    *,
    issued_at: datetime | None = None,
    previous_policy: Policy | None = None,
    policy_version: int = 1,
    baseline_weights: dict[str, dict[str, float]] | None = None,
) -> AllocationPlan:
    """One joint LP over every selection group, then bound and round the result."""
    issued_at = issued_at or cube.latest_time or datetime.now(timezone.utc)
    forecasts = build_forecasts(
        predictions,
        issued_at=issued_at,
        horizon_seconds=config.cadence.forecast_horizon_seconds,
    )
    states = build_upf_states(
        config,
        [item.group.selection_id for item in forecasts],
        measurement_time=issued_at,
        observed_eligibility=cube.observed_eligibility,
    )
    settings = OptimizationConfig(
        planning_quantile=config.solver.planning_quantile,
        max_group_upf_weight=config.weight_bounds.max_share,
        timeout_seconds=max(1.0, config.timeout_seconds / 2.0),
    )
    result = solve_allocation(
        forecasts,
        states,
        created_at=issued_at,
        policy_version=policy_version,
        previous_policy=previous_policy,
        config=settings,
    )

    current = baseline_weights if baseline_weights is not None else cube.current_weights()
    demand_p50 = {
        item.group.selection_id: {
            "ul": item.new_load_ul_mbps.p50,
            "dl": item.new_load_dl_mbps.p50,
        }
        for item in forecasts
    }
    baseline_load = _with_totals(cube.projected_upf_load(current, demand_p50))

    if result.policy is None:
        return AllocationPlan(
            status=result.status,
            weights=current,
            integer_weights={
                key: integer_weights(value) for key, value in current.items() if value
            },
            projected_load_pps=baseline_load,
            baseline_load_pps=baseline_load,
            max_safe_utilization=result.max_safe_utilization,
            solver_runtime_ms=result.solver_runtime_ms if hasattr(result, "solver_runtime_ms") else 0,
            message=result.message or "solver returned no policy",
            eligibility=config.eligibility(cube.observed_eligibility),
        )

    bounds = config.weight_bounds
    step = bounds.max_step_delta_pp / 100.0
    eligibility = config.eligibility(cube.observed_eligibility)
    weights: dict[str, dict[str, float]] = {}
    integers: dict[str, dict[str, int]] = {}
    for group in result.policy.groups:
        selection_id = group.key.selection_id
        # Eligibility has to gate the *written* vector, not just the LP.
        #
        # ``apply_bounds`` water-fills over ``set(target) | set(current)``, and
        # ``current`` is the observed routing -- which under ``declared`` mode
        # contains UPF/TAC pairs the constraint CSV forbids.  Without this
        # filter the LP would correctly refuse to place load on such a pair and
        # the min_share floor would then put it straight back at 2%, writing a
        # weight for a (tac, upf) combination C-DOT says cannot carry it.
        _, tac = parse_group_id(selection_id)
        allowed = set(eligibility.get(tac, ()))
        target = group.weights
        observed = current.get(selection_id, {})
        if allowed:
            target = {upf: value for upf, value in target.items() if upf in allowed}
            observed = {upf: value for upf, value in observed.items() if upf in allowed}
        bounded = apply_bounds(
            target,
            observed,
            min_share=bounds.min_share,
            max_share=bounds.max_share,
            max_step_delta=step,
        )
        if not bounded:
            continue
        weights[selection_id] = bounded
        integers[selection_id] = integer_weights(bounded)

    return AllocationPlan(
        status=result.status,
        weights=weights,
        integer_weights=integers,
        projected_load_pps=_with_totals(cube.projected_upf_load(weights, demand_p50)),
        baseline_load_pps=baseline_load,
        max_safe_utilization=result.max_safe_utilization,
        solver_runtime_ms=result.policy.solver.runtime_ms,
        policy=result.policy,
        eligibility=eligibility,
    )


def _with_totals(table: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    return {
        upf: {"ul": value["ul"], "dl": value["dl"], "total": value["ul"] + value["dl"]}
        for upf, value in table.items()
    }
