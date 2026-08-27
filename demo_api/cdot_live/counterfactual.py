"""Baseline vs advisory replay -- the visual that carries the demo.

C-DOT's UPFs will not drop packets no matter how hard they are pushed: drop rate
reads 0.000 on three of four pods, CPU is pinned at 3.00 cores by DPDK busy-spin,
and DL forwarding efficiency sits at 100%.  Nothing in their telemetry ever says
"overloaded".  So "UPFs getting blasted" versus "traffic handled" has to be drawn
against a *declared* capacity line, from the same measured demand, under two
different weight tables:

* **baseline** -- the routing their SMF is doing today, taken either from
  ``GET /upf-admin`` or from the observed per-group split in the trace;
* **advisory** -- what our forecaster + optimizer would have done, recomputed on
  a decision cadence using only history available at that moment.

Both curves come from the same ``D[dnn,tac](t)``, so the comparison is exact:
the only difference between them is the weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .cdot_forecaster import CdotForecaster, ForecastError, estimate_period
from .config import LiveConfig
from .demand import DemandCube, group_id
from .optimizer import OptimizerError, solve


@dataclass(slots=True)
class ArmResult:
    """One side of the comparison, sampled on the cube's time grid."""

    label: str
    load_pps: dict[str, list[float]]
    hottest_pps: list[float]
    capacity_pps: float
    safe_pps: float
    overload_seconds: float
    safe_breach_seconds: float
    overload_fraction: float
    peak_pps: float
    mean_hottest_pps: float
    cumulative_overload_seconds: list[float] = field(default_factory=list)
    score_from: int = 0
    weight_changes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "unit": "pps",
            "load_pps": {upf: [round(v, 1) for v in series] for upf, series in self.load_pps.items()},
            "hottest_pps": [round(v, 1) for v in self.hottest_pps],
            "capacity_pps": self.capacity_pps,
            "safe_pps": self.safe_pps,
            "overload_seconds": round(self.overload_seconds, 1),
            "safe_breach_seconds": round(self.safe_breach_seconds, 1),
            "overload_fraction": round(self.overload_fraction, 4),
            "peak_pps": round(self.peak_pps, 1),
            "mean_hottest_pps": round(self.mean_hottest_pps, 1),
            "weight_changes": self.weight_changes,
            "cumulative_overload_seconds": [
                round(value, 1) for value in self.cumulative_overload_seconds
            ],
            "score_from_index": self.score_from,
        }


@dataclass(slots=True)
class Counterfactual:
    times: list[str]
    step_seconds: int
    baseline: ArmResult
    advisory: ArmResult
    decisions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    warmup_index: int = 0

    def playback(self, target_minutes: float = 12.0) -> dict[str, Any]:
        """How to replay this window compressed into a few minutes on stage.

        Every frame here was computed causally during :func:`run`; compressing
        the playback changes only how fast the finished result is revealed, never what
        the forecaster or the optimizer could see.
        """
        frames = len(self.times)
        span_seconds = frames * self.step_seconds
        target = max(60.0, target_minutes * 60.0)
        return {
            "frames": frames,
            "trace_span_seconds": span_seconds,
            "warmup_index": self.warmup_index,
            "suggested_minutes": target_minutes,
            "compression": round(span_seconds / target, 1) if target else 1.0,
            "frame_interval_ms": round(target * 1000.0 / frames) if frames else 0,
            "decision_indices": [item["index"] for item in self.decisions],
        }

    @property
    def peak_reduction(self) -> float | None:
        return (
            1.0 - self.advisory.peak_pps / self.baseline.peak_pps
            if self.baseline.peak_pps > 0
            else None
        )

    @property
    def mean_hottest_reduction(self) -> float | None:
        return (
            1.0 - self.advisory.mean_hottest_pps / self.baseline.mean_hottest_pps
            if self.baseline.mean_hottest_pps > 0
            else None
        )

    def scorecard(self) -> dict[str, Any]:
        return {
            "peak_hottest_pps": {
                "baseline": round(self.baseline.peak_pps, 1),
                "advisory": round(self.advisory.peak_pps, 1),
                "reduction": round(self.peak_reduction, 4) if self.peak_reduction else None,
            },
            "mean_hottest_pps": {
                "baseline": round(self.baseline.mean_hottest_pps, 1),
                "advisory": round(self.advisory.mean_hottest_pps, 1),
                "reduction": (
                    round(self.mean_hottest_reduction, 4) if self.mean_hottest_reduction else None
                ),
            },
            "overload_seconds": {
                "baseline": round(self.baseline.overload_seconds, 1),
                "advisory": round(self.advisory.overload_seconds, 1),
            },
            "overload_fraction": {
                "baseline": round(self.baseline.overload_fraction, 4),
                "advisory": round(self.advisory.overload_fraction, 4),
            },
            "scored_from_index": self.baseline.score_from,
            "capacity_pps": self.baseline.capacity_pps,
            "safe_pps": self.baseline.safe_pps,
            "decisions": len(self.decisions),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "times": self.times,
            "step_seconds": self.step_seconds,
            "baseline": self.baseline.as_dict(),
            "advisory": self.advisory.as_dict(),
            "decisions": self.decisions,
            "warnings": self.warnings,
            "warmup_index": self.warmup_index,
            "playback": self.playback(),
            "scorecard": self.scorecard(),
        }


# ---------------------------------------------------------------- projection


def project(
    cube: DemandCube, weights_over_time: list[dict[str, dict[str, float]]]
) -> dict[str, np.ndarray]:
    """Per-UPF load from a per-timestep weight table.

    ``L[u](t) = sum over (dnn,tac) of w_t[dnn,tac,u] * D[dnn,tac](t)``
    """
    loads = {upf: np.zeros(len(cube)) for upf in cube.upfs}
    index = {upf: i for i, upf in enumerate(cube.upfs)}
    for gi, group in enumerate(cube.groups):
        key = group_id(*group)
        demand = cube.demand["ul"][gi] + cube.demand["dl"][gi]
        for t in range(len(cube)):
            weights = weights_over_time[t].get(key)
            if not weights:
                continue
            total = sum(weights.values())
            if total <= 0:
                continue
            for upf, weight in weights.items():
                if upf in index:
                    loads[upf][t] += demand[t] * weight / total
    return loads


def _score(
    label: str,
    loads: dict[str, np.ndarray],
    *,
    capacity_pps: float,
    safe_pps: float,
    step_seconds: int,
    score_from: int = 0,
    weight_changes: int = 0,
) -> ArmResult:
    """Score both arms over the same window.

    ``score_from`` skips the warmup during which the advisory arm has not yet
    made a decision.  Comparing a fully-warmed baseline against an advisory arm
    that spent the first 40% of the trace doing nothing would understate the
    result -- and, more importantly, would not be the comparison the demo makes:
    Act 1 is a baseline cycle, Act 2 is an optimised cycle.
    """
    full = np.stack([loads[upf] for upf in sorted(loads)]) if loads else np.zeros((1, 0))
    stacked = full[:, score_from:] if full.size else full
    hottest = stacked.max(axis=0) if stacked.size else np.zeros(0)
    # Cumulative UPF-seconds over the line, from the first sample -- the number
    # that ticks upward on the baseline card and stays flat on the advisory one.
    if full.size:
        per_step = (full > capacity_pps).sum(axis=0) * float(step_seconds)
        cumulative = np.cumsum(per_step).tolist()
    else:
        cumulative = []
    # Overload-seconds is summed per UPF, not per timestep: two UPFs over the
    # line for a minute is two UPF-minutes of overload, which is what an
    # operator actually pays for.
    over = float((stacked > capacity_pps).sum()) * step_seconds
    breach = float((stacked > safe_pps).sum()) * step_seconds
    return ArmResult(
        label=label,
        load_pps={upf: [float(v) for v in loads[upf]] for upf in sorted(loads)},
        score_from=score_from,
        hottest_pps=[float(v) for v in hottest],
        capacity_pps=capacity_pps,
        safe_pps=safe_pps,
        overload_seconds=over,
        safe_breach_seconds=breach,
        overload_fraction=(
            float(np.mean(hottest > capacity_pps)) if hottest.size else 0.0
        ),
        peak_pps=float(hottest.max()) if hottest.size else 0.0,
        mean_hottest_pps=float(hottest.mean()) if hottest.size else 0.0,
        cumulative_overload_seconds=cumulative,
        weight_changes=weight_changes,
    )


# ------------------------------------------------------------------- the run


def run(
    cube: DemandCube,
    config: LiveConfig,
    *,
    baseline_weights: dict[str, dict[str, float]] | None = None,
    warmup_fraction: float = 0.18,
    decision_interval_samples: int | None = None,
) -> Counterfactual:
    """Replay the trace under static baseline weights and under rolling advice.

    Strictly causal: at each decision the forecaster is refit and the LP solved
    on ``cube[:origin+1]`` only, and the resulting weights take effect from the
    *next* sample onwards -- never retroactively.
    """
    total = len(cube)
    if total < 8:
        raise OptimizerError("counterfactual needs a longer demand window")

    horizon = config.cadence.horizon_steps
    step = cube.step_seconds
    every = decision_interval_samples or max(
        1, round(config.cadence.decision_interval_seconds / step)
    )
    # Long enough for the cycle features to have two full periods behind them,
    # short enough that a compressed playback is not spent watching nothing
    # happen: the advisory engages about a fifth of the way in.
    # The forecaster needs two full cycles behind it before lag_2P exists, but
    # no more than that: every warmup sample is a sample where the advisory arm
    # is still doing nothing, which on a compressed playback is dead air.
    period = estimate_period(
        cube.demand["ul"].sum(axis=0) + cube.demand["dl"].sum(axis=0), cube.step_seconds
    )
    floor = max(2 * horizon + 24, 2 * period + 8 if period else 0)
    warmup = min(max(floor, int(total * warmup_fraction)), total - 2)

    static = baseline_weights or cube.current_weights(lookback=total)
    baseline_over_time = [static] * total

    advisory_over_time: list[dict[str, dict[str, float]]] = []
    decisions: list[dict[str, Any]] = []
    warnings: list[str] = []
    active = static
    forecaster: CdotForecaster | None = None
    changes = 0

    for t in range(total):
        if t >= warmup and (t - warmup) % every == 0:
            window = _prefix(cube, t + 1)
            try:
                forecaster = CdotForecaster.fit(
                    window, horizon=horizon, carry_over=forecaster
                )
                predictions = forecaster.predict(window)
                plan = solve(
                    window,
                    predictions,
                    config,
                    issued_at=cube.times[t],
                    baseline_weights=static,
                )
            except (ForecastError, OptimizerError, ValueError) as error:
                warnings.append(f"t={t}: {error}")
            else:
                if plan.policy is not None and plan.weights:
                    if plan.weights != active:
                        changes += 1
                    active = plan.weights
                    decisions.append(
                        {
                            "index": t,
                            "time": cube.times[t].isoformat().replace("+00:00", "Z"),
                            "status": plan.status,
                            "weights": plan.integer_weights,
                            "max_safe_utilization": plan.max_safe_utilization,
                            "solver_runtime_ms": plan.solver_runtime_ms,
                            # What the forecaster saw when it made this call --
                            # replayed frame by frame, this is the band that
                            # moves ahead of the actual line on stage.
                            "horizon_index": min(t + horizon, total - 1),
                            "forecast": {
                                key: {
                                    "p50": round(value["ul"].p50 + value["dl"].p50, 1),
                                    "p90": round(value["ul"].p90 + value["dl"].p90, 1),
                                    "p95": round(value["ul"].p95 + value["dl"].p95, 1),
                                }
                                for key, value in predictions.items()
                            },
                            "forecast_network_p50": round(
                                sum(
                                    value["ul"].p50 + value["dl"].p50
                                    for value in predictions.values()
                                ),
                                1,
                            ),
                            "families": forecaster.summary().get("families", {}),
                        }
                    )
                else:
                    warnings.append(f"t={t}: solver status {plan.status}: {plan.message}")
        advisory_over_time.append(active)

    capacity = config.capacity
    baseline = _score(
        "baseline",
        project(cube, baseline_over_time),
        capacity_pps=capacity.per_upf_pps,
        safe_pps=capacity.safe_pps,
        step_seconds=step,
        score_from=warmup,
    )
    advisory = _score(
        "advisory",
        project(cube, advisory_over_time),
        capacity_pps=capacity.per_upf_pps,
        safe_pps=capacity.safe_pps,
        step_seconds=step,
        score_from=warmup,
        weight_changes=changes,
    )
    return Counterfactual(
        times=[item.isoformat().replace("+00:00", "Z") for item in cube.times],
        step_seconds=step,
        baseline=baseline,
        advisory=advisory,
        decisions=decisions,
        warnings=warnings[:20],
        warmup_index=warmup,
    )


def _prefix(cube: DemandCube, end: int) -> DemandCube:
    return DemandCube(
        times=cube.times[:end],
        step_seconds=cube.step_seconds,
        groups=list(cube.groups),
        upfs=list(cube.upfs),
        demand={key: value[:, :end] for key, value in cube.demand.items()},
        carried={key: value[:, :end] for key, value in cube.carried.items()},
        share=cube.share[:, :, :end],
        observed_eligibility=cube.observed_eligibility,
    )
