"""Forecaster refit on the C-DOT trace.

Same model class as ``forecasting/bundle.py`` -- ridge regression on a small
causal feature vector, split-conformal residual bands, ``median_bias``
recentering -- but refit on C-DOT data with cycle-phase features instead of
daily/weekly Fourier terms.

Why not reuse the frozen bundle: it predicts *new-session* Mbps and arrivals
from 144 ten-minute buckets keyed on time-of-day and day-of-week.  C-DOT gives
four hours of *carried packet rate*.  The 24 h lag feature never exists and the
calendar features are meaningless over a four hour window, so the frozen
coefficients have nothing to stand on.  The algorithm transfers; the fit does not.

What replaces the calendar features: their traffic is a repeating staircase with
a period near 31 minutes (autocorrelation peaks at 31 / 62 / 93 min).  Encoding
that period -- as same-phase lags and as sine/cosine of the cycle phase -- halves
the ten-minute-ahead error on the ``internet`` DNN, which is the DNN that drives
the overload.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from .demand import DemandCube, GroupId, group_id

FEATURE_NAMES: tuple[str, ...] = (
    "intercept",
    "last",
    "rolling_mean_6",
    "rolling_std_6",
    "recent_trend",
    "lag_period",
    "lag_two_period",
    "sin_cycle_phase",
    "cos_cycle_phase",
    "samples_since_step_edge",
)

CALIBRATION_LEVELS: tuple[float, ...] = (0.50, 0.80, 0.90, 0.95, 0.99)
DIRECTIONS: tuple[str, str] = ("ul", "dl")

# A step edge is a jump of more than this fraction of the series' own scale
# between adjacent samples.  Their transitions are 40-100% moves, so this is
# well clear of noise.
_STEP_EDGE_FRACTION = 0.15
_MIN_TRAINING_ROWS = 24
# ACI target miss rate for the p90 band.
_TARGET_MISS = 0.08


def _horizon_path(horizon: int) -> tuple[int, ...]:
    """Horizons to fit: the next decision, the middle, and the full lead time."""
    return tuple(sorted({max(1, horizon // 10), max(1, horizon // 2), horizon}))


def _shift(level: float, offset: float) -> float:
    """Move a conformal level by an ACI offset, staying inside the fitted grid."""
    return float(min(CALIBRATION_LEVELS[-1], max(CALIBRATION_LEVELS[0], level + offset)))


class ForecastError(RuntimeError):
    """Raised when a series cannot support a fit."""


# --------------------------------------------------------------- period search


def estimate_period(
    series: np.ndarray,
    step_seconds: int,
    *,
    min_minutes: float = 8.0,
    max_minutes: float = 75.0,
) -> int:
    """Cycle period in samples, from the first strong autocorrelation peak.

    Returns 0 when no candidate lag clears the noise floor, which is the signal
    to fall back to the non-cyclic features.
    """
    values = np.asarray(series, dtype=float)
    if values.size < 8:
        return 0
    centred = values - values.mean()
    denominator = float(centred @ centred)
    if denominator <= 0:
        return 0
    low = max(2, int(round(min_minutes * 60 / step_seconds)))
    high = min(values.size // 2, int(round(max_minutes * 60 / step_seconds)))
    if high <= low:
        return 0
    scores = np.array(
        [float(centred[lag:] @ centred[:-lag]) / denominator for lag in range(low, high + 1)]
    )
    best = int(np.argmax(scores))
    # Require the peak to be a genuine local maximum and meaningfully positive;
    # a monotone decay means there is no cycle, only trend.
    if scores[best] < 0.25:
        return 0
    if 0 < best < len(scores) - 1 and not (
        scores[best] >= scores[best - 1] and scores[best] >= scores[best + 1]
    ):
        return 0
    return low + best


# -------------------------------------------------------------------- features


def _step_edge_ages(values: np.ndarray) -> np.ndarray:
    """Samples elapsed since the last abrupt transition, per index."""
    ages = np.zeros(values.size, dtype=float)
    scale = float(np.mean(np.abs(values))) or 1.0
    threshold = _STEP_EDGE_FRACTION * scale
    age = 0.0
    for i in range(values.size):
        if i > 0 and abs(values[i] - values[i - 1]) > threshold:
            age = 0.0
        else:
            age += 1.0
        ages[i] = age
    return ages


def build_features(
    values: np.ndarray,
    origin: int,
    horizon: int,
    period: int,
    *,
    edge_ages: np.ndarray | None = None,
) -> np.ndarray:
    """Feature row observed at ``origin``, predicting ``origin + horizon``.

    Strictly causal: no index above ``origin`` is read.  The cycle features are
    evaluated at the *target* phase, which is knowable in advance because the
    phase is a function of the clock, not of the data.
    """
    if origin < 0 or origin >= values.size:
        raise ForecastError("feature origin outside the series")
    ages = _step_edge_ages(values) if edge_ages is None else edge_ages
    last = float(values[origin])
    window = values[max(0, origin - 5) : origin + 1]
    rolling_mean = float(window.mean())
    rolling_std = float(window.std())
    trend_base = float(values[max(0, origin - 3)])
    target = origin + horizon

    if period > 0:
        lag_one = target - period
        lag_two = target - 2 * period
        lag_period = float(values[lag_one]) if 0 <= lag_one <= origin else last
        lag_two_period = float(values[lag_two]) if 0 <= lag_two <= origin else lag_period
        phase = 2.0 * math.pi * (target % period) / period
        sin_phase = math.sin(phase)
        cos_phase = math.cos(phase)
    else:
        lag_period = last
        lag_two_period = last
        sin_phase = 0.0
        cos_phase = 0.0

    return np.array(
        [
            1.0,
            last,
            rolling_mean,
            rolling_std,
            last - trend_base,
            lag_period,
            lag_two_period,
            sin_phase,
            cos_phase,
            float(ages[origin]),
        ],
        dtype=float,
    )


# ------------------------------------------------------------------- the model


def _wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denominator = float(np.sum(np.abs(actual)))
    return float(np.sum(np.abs(actual - predicted)) / denominator) if denominator else 0.0



def _quantile(sorted_source: Sequence[float], level: float) -> float:
    values = sorted(float(item) for item in sorted_source)
    if not values:
        return 0.0
    position = level * (len(values) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(values) - 1)
    weight = position - low
    return values[low] * (1.0 - weight) + values[high] * weight


@dataclass(slots=True)
class SeriesModel:
    """One fitted series predictor plus its split-conformal widths.

    ``family`` records which candidate won the held-out selection block.  Their
    ``ims`` DNN is nearly constant, and a ten-feature ridge fit on ~150 rows
    makes it visibly worse than simply repeating the last value; letting a
    persistence candidate win there is what keeps the ensemble honest.
    """

    family: str
    coefficients: np.ndarray
    median_bias: float
    calibration_levels: tuple[float, ...]
    calibration_widths: tuple[float, ...]
    period: int
    horizon: int
    metrics: dict[str, float]

    def point(self, values: np.ndarray, origin: int, edge_ages: np.ndarray | None = None) -> float:
        return max(0.0, self._raw(values, origin, edge_ages) + self.median_bias)

    def _raw(self, values: np.ndarray, origin: int, edge_ages: np.ndarray | None = None) -> float:
        if self.family == "persistence":
            return float(values[origin])
        if self.family in ("cycle_naive", "cycle_ridge"):
            index = origin + self.horizon - self.period
            baseline = (
                float(values[index])
                if self.period > 0 and 0 <= index <= origin
                else float(values[origin])
            )
            if self.family == "cycle_naive":
                return baseline
            features = build_features(values, origin, self.horizon, self.period, edge_ages=edge_ages)
            return baseline + float(features @ self.coefficients)
        features = build_features(values, origin, self.horizon, self.period, edge_ages=edge_ages)
        return float(features @ self.coefficients)

    def width(self, level: float) -> float:
        levels = self.calibration_levels
        widths = self.calibration_widths
        if level <= levels[0]:
            return widths[0]
        if level >= levels[-1]:
            return widths[-1]
        for i in range(1, len(levels)):
            if level <= levels[i]:
                span = levels[i] - levels[i - 1]
                weight = 0.0 if span <= 0 else (level - levels[i - 1]) / span
                return widths[i - 1] * (1.0 - weight) + widths[i] * weight
        return widths[-1]


@dataclass(slots=True)
class Prediction:
    p50: float
    p90: float
    p95: float
    family: str = "ridge"

    def as_dict(self) -> dict[str, float | str]:
        return {"p50": self.p50, "p90": self.p90, "p95": self.p95, "family": self.family}


def _ridge_coefficients(
    rows: np.ndarray, targets: np.ndarray, ridge: float
) -> np.ndarray:
    """RMS-scaled ridge solve, folded back to raw-feature coefficients.

    Same conditioning trick as ``forecasting.bundle._fit_model``: packet rates
    run to hundreds of thousands while the phase features sit in [-1, 1], so the
    unscaled normal equations are hopeless.
    """
    identity = np.eye(rows.shape[1], dtype=float)
    identity[0, 0] = 0.0
    scale = np.sqrt(np.mean(np.square(rows), axis=0))
    scale[0] = 1.0
    scale[~np.isfinite(scale) | (scale < 1e-12)] = 1.0
    scaled = rows / scale
    strength = ridge * max(1.0, float(rows.shape[0]))
    return np.linalg.solve(scaled.T @ scaled + strength * identity, scaled.T @ targets) / scale


def fit_series(
    values: np.ndarray,
    *,
    horizon: int,
    period: int,
    ridge: float = 1e-2,
) -> SeriesModel:
    """Fit, select a family on held-out data, then conformalise on recent data.

    Three blocks, in time order:

    * **train** (first 55%) -- fits the ridge coefficients.
    * **select** (next 20%) -- scores ridge against persistence and a
      same-phase cycle-naive baseline; lowest WAPE wins.  Never used for fitting,
      so the comparison is honest.
    * **calibrate** (last 25%) -- ``median_bias`` and the conformal widths.
      Deliberately the *most recent* block: their traffic changes regime between
      cycles, and residual spread from an hour ago under-covers the next ten
      minutes.
    """
    values = np.asarray(values, dtype=float)
    usable = values.size - horizon
    if usable < _MIN_TRAINING_ROWS:
        raise ForecastError(
            f"need at least {_MIN_TRAINING_ROWS + horizon} samples to fit horizon {horizon}"
        )
    ages = _step_edge_ages(values)
    rows = np.stack(
        [build_features(values, i, horizon, period, edge_ages=ages) for i in range(usable)]
    )
    targets = values[horizon : horizon + usable]

    train_end = max(12, int(usable * 0.55))
    select_end = min(max(train_end + 4, int(usable * 0.75)), usable - 4)
    if select_end <= train_end:
        select_end = min(train_end + 1, usable - 1)

    coefficients = _ridge_coefficients(rows[:train_end], targets[:train_end], ridge)
    baseline = _cycle_naive(values, usable, horizon, period)
    # Ridge on the *residual* of the cycle baseline.  Their staircase repeats
    # almost exactly, so the seasonal term carries the level and the ridge only
    # has to explain drift and step timing -- a far easier target than the raw
    # packet rate, and it keeps the model useful if the cycle shifts.
    residual_coefficients = _ridge_coefficients(
        rows[:train_end], targets[:train_end] - baseline[:train_end], ridge
    )

    candidates: dict[str, np.ndarray] = {
        "ridge": rows @ coefficients,
        "cycle_ridge": baseline + rows @ residual_coefficients,
        "persistence": values[:usable],
        "cycle_naive": baseline,
    }
    select_slice = slice(train_end, select_end)
    select_y = targets[select_slice]
    scores = {
        name: _wape(select_y, np.maximum(0.0, series[select_slice]))
        for name, series in candidates.items()
    }
    family = min(scores, key=lambda name: (scores[name], name))
    fitted = residual_coefficients if family == "cycle_ridge" else coefficients

    calibration_slice = slice(select_end, usable)
    calibration_raw = candidates[family][calibration_slice]
    calibration_y = targets[calibration_slice]
    median_bias = float(np.median(calibration_y - calibration_raw)) if calibration_y.size else 0.0
    calibration_point = np.maximum(0.0, calibration_raw + median_bias)
    residuals = np.abs(calibration_y - calibration_point).tolist()
    widths = tuple(_quantile(residuals, level) for level in CALIBRATION_LEVELS)

    metrics = {
        "rows": float(usable),
        "train_rows": float(train_end),
        "select_rows": float(select_end - train_end),
        "calibration_rows": float(usable - select_end),
        "wape_select": float(scores[family]),
        **{f"wape_select_{name}": float(score) for name, score in scores.items()},
    }
    return SeriesModel(
        family=family,
        coefficients=fitted,
        median_bias=median_bias,
        calibration_levels=CALIBRATION_LEVELS,
        calibration_widths=widths,
        period=period,
        horizon=horizon,
        metrics=metrics,
    )


def _cycle_naive(values: np.ndarray, usable: int, horizon: int, period: int) -> np.ndarray:
    """Same-phase value one cycle before the target, falling back to the last value."""
    out = np.empty(usable, dtype=float)
    for i in range(usable):
        index = i + horizon - period
        out[i] = values[index] if period > 0 and 0 <= index <= i else values[i]
    return out


# ---------------------------------------------------------------- the ensemble


@dataclass(slots=True)
class CdotForecaster:
    """One model per (selection group, direction), refit on the live window."""

    horizon: int
    step_seconds: int
    horizons: tuple[int, ...] = ()
    models: dict[tuple[str, str, int], SeriesModel] = field(default_factory=dict)
    period: int = 0
    fallbacks: dict[str, str] = field(default_factory=dict)
    fitted_rows: int = 0
    # Adaptive conformal offsets, per (group, direction).  Split conformal alone
    # under-covers here: the calibration block is the tail of the training
    # window, and their traffic changes regime between cycles, so residual
    # spread from twenty minutes ago is too tight for the next ten.  ACI walks
    # the effective level up when we miss and back down when we do not.
    alpha_offsets: dict[tuple[str, str], float] = field(default_factory=dict)

    # ----------------------------------------------------------------- fitting

    @classmethod
    def fit(
        cls,
        cube: DemandCube,
        *,
        horizon: int,
        ridge: float = 1e-2,
        carry_over: "CdotForecaster | None" = None,
        horizons: Iterable[int] | None = None,
    ) -> "CdotForecaster":
        """Fit one model per (group, direction, horizon).

        Several horizons, not one: weights posted now govern the traffic that
        arrives over the next few minutes, but their load is a staircase, so
        planning on a single ten-minute-ahead point either ignores a step that
        lands in two minutes or over-reacts to one that has already passed.
        The optimizer plans on the **envelope** -- the worst p95 across the
        horizon path -- which is what lets it pre-position before a step.
        """
        if horizon < 1:
            raise ForecastError("horizon must be at least one sample")
        path = tuple(sorted({max(1, int(item)) for item in (horizons or _horizon_path(horizon))}))
        totals = cube.demand["ul"].sum(axis=0) + cube.demand["dl"].sum(axis=0)
        period = estimate_period(totals, cube.step_seconds)
        forecaster = cls(
            horizon=horizon,
            step_seconds=cube.step_seconds,
            period=period,
            fitted_rows=len(cube),
            horizons=path,
            alpha_offsets=dict(carry_over.alpha_offsets) if carry_over else {},
        )
        for gi, group in enumerate(cube.groups):
            key = group_id(*group)
            for direction in DIRECTIONS:
                series = cube.demand[direction][gi]
                if float(series.max(initial=0.0)) <= 0.0:
                    forecaster.fallbacks[f"{key}|{direction}"] = "empty-series"
                    continue
                for step in path:
                    try:
                        forecaster.models[(key, direction, step)] = fit_series(
                            series, horizon=step, period=period, ridge=ridge
                        )
                    except (ForecastError, np.linalg.LinAlgError) as error:
                        forecaster.fallbacks[f"{key}|{direction}|h{step}"] = str(error)
        if not forecaster.models:
            raise ForecastError("no selection group had enough history to fit")
        return forecaster

    # -------------------------------------------------------------- prediction

    def predict(self, cube: DemandCube) -> dict[str, dict[str, Prediction]]:
        """Demand per selection group over the horizon path.

        ``p50`` is the primary horizon's point estimate -- what the charts show.
        ``p90``/``p95`` are the **envelope**: the widest band over any horizon on
        the path, so the optimizer sizes for the worst moment it can see coming
        rather than for one instant ten minutes out.
        """
        origin = len(cube) - 1
        if origin < 0:
            raise ForecastError("cannot predict from an empty demand cube")
        out: dict[str, dict[str, Prediction]] = {}
        for gi, group in enumerate(cube.groups):
            key = group_id(*group)
            per_direction: dict[str, Prediction] = {}
            for direction in DIRECTIONS:
                series = cube.demand[direction][gi]
                offset = self.alpha_offsets.get((key, direction), 0.0)
                path: list[Prediction] = []
                for step in self.horizons or (self.horizon,):
                    model = self.models.get((key, direction, step))
                    if model is None:
                        continue
                    point = model.point(series, origin)
                    path.append(
                        Prediction(
                            p50=point,
                            p90=point + model.width(_shift(0.90, offset)),
                            p95=point + model.width(_shift(0.95, offset)),
                            family=model.family,
                        )
                    )
                if not path:
                    last = float(series[origin]) if series.size else 0.0
                    per_direction[direction] = Prediction(last, last, last, family="unfitted")
                    continue
                primary = self.models.get((key, direction, self.horizon))
                head = (
                    path[[step for step in self.horizons].index(self.horizon)]
                    if primary is not None and self.horizon in self.horizons
                    else path[-1]
                )
                per_direction[direction] = Prediction(
                    p50=head.p50,
                    p90=max(item.p90 for item in path),
                    p95=max(item.p95 for item in path),
                    family=head.family,
                )
            out[key] = per_direction
        return out

    def record_outcome(
        self, key: str, direction: str, actual: float, prediction: Prediction, *, gamma: float = 0.03
    ) -> None:
        """Online conformal update from a realised value (ACI, target miss 10%)."""
        missed = 1.0 if actual > prediction.p90 else 0.0
        offset = self.alpha_offsets.get((key, direction), 0.0) + gamma * (missed - _TARGET_MISS)
        self.alpha_offsets[(key, direction)] = float(min(0.09, max(-0.35, offset)))

    # ------------------------------------------------------------- diagnostics

    def summary(self) -> dict[str, Any]:
        primary = [
            model for (_, _, step), model in self.models.items() if step == self.horizon
        ] or list(self.models.values())
        wapes = [model.metrics["wape_select"] for model in primary]
        baseline = [model.metrics["wape_select_persistence"] for model in primary]
        families: dict[str, int] = {}
        for model in primary:
            families[model.family] = families.get(model.family, 0) + 1
        return {
            "model": "cdot-ridge-conformal/1.0",
            "features": list(FEATURE_NAMES),
            "horizon_samples": self.horizon,
            "horizon_seconds": self.horizon * self.step_seconds,
            "step_seconds": self.step_seconds,
            "cycle_period_samples": self.period,
            "cycle_period_minutes": (
                round(self.period * self.step_seconds / 60.0, 1) if self.period else None
            ),
            "fitted_series": len(primary),
            "horizon_path_samples": list(self.horizons),
            "fitted_rows": self.fitted_rows,
            "fallbacks": dict(self.fallbacks),
            "families": families,
            "alpha_offset_mean": (
                float(np.mean(list(self.alpha_offsets.values()))) if self.alpha_offsets else 0.0
            ),
            "wape_select_mean": float(np.mean(wapes)) if wapes else None,
            "wape_select_persistence_mean": float(np.mean(baseline)) if baseline else None,
        }


# ------------------------------------------------------------------- backtest


def walk_forward_backtest(
    cube: DemandCube,
    *,
    horizon: int,
    warmup_fraction: float = 0.55,
    refit_every: int = 20,
    ridge: float = 1e-2,
) -> dict[str, Any]:
    """Honest walk-forward evaluation: refit only on the past, score the future.

    The model is refit every ``refit_every`` samples on the history available at
    that moment, then used to predict ``horizon`` samples ahead until the next
    refit.  Nothing after the origin is ever read.
    """
    total = len(cube)
    warmup = max(2 * horizon + _MIN_TRAINING_ROWS, int(total * warmup_fraction))
    if warmup + horizon >= total:
        raise ForecastError("demand cube is too short for a walk-forward backtest")

    per_group_actual: dict[str, list[float]] = {}
    per_group_model: dict[str, list[float]] = {}
    per_group_persistence: dict[str, list[float]] = {}
    covered_p90: list[float] = []
    covered_p95: list[float] = []
    period_used = 0
    forecaster: CdotForecaster | None = None

    for origin in range(warmup, total - horizon):
        if forecaster is None or (origin - warmup) % refit_every == 0:
            history = _slice_cube(cube, origin + 1)
            try:
                forecaster = CdotForecaster.fit(
                    history, horizon=horizon, ridge=ridge, carry_over=forecaster
                )
                period_used = forecaster.period
            except ForecastError:
                continue
        window = _slice_cube(cube, origin + 1)
        predictions = forecaster.predict(window)
        for gi, group in enumerate(cube.groups):
            key = group_id(*group)
            for direction in DIRECTIONS:
                actual = float(cube.demand[direction][gi][origin + horizon])
                prediction = predictions[key][direction]
                per_group_actual.setdefault(key, []).append(actual)
                per_group_model.setdefault(key, []).append(prediction.p50)
                per_group_persistence.setdefault(key, []).append(
                    float(cube.demand[direction][gi][origin])
                )
                covered_p90.append(1.0 if actual <= prediction.p90 else 0.0)
                covered_p95.append(1.0 if actual <= prediction.p95 else 0.0)
                forecaster.record_outcome(key, direction, actual, prediction)

    if not per_group_actual:
        raise ForecastError("backtest produced no comparisons")

    per_group: dict[str, dict[str, float]] = {}
    all_actual: list[float] = []
    all_model: list[float] = []
    all_persistence: list[float] = []
    for key, actual in per_group_actual.items():
        a = np.asarray(actual)
        m = np.asarray(per_group_model[key])
        p = np.asarray(per_group_persistence[key])
        per_group[key] = {
            "wape_model": _wape(a, m),
            "wape_persistence": _wape(a, p),
            "samples": float(a.size),
        }
        all_actual.extend(actual)
        all_model.extend(per_group_model[key])
        all_persistence.extend(per_group_persistence[key])

    a = np.asarray(all_actual)
    return {
        "horizon_samples": horizon,
        "horizon_seconds": horizon * cube.step_seconds,
        "cycle_period_samples": period_used,
        "cycle_period_minutes": (
            round(period_used * cube.step_seconds / 60.0, 1) if period_used else None
        ),
        "origins": int(total - horizon - warmup),
        "wape_model": _wape(a, np.asarray(all_model)),
        "wape_persistence": _wape(a, np.asarray(all_persistence)),
        "coverage_p90": float(np.mean(covered_p90)) if covered_p90 else 0.0,
        "coverage_p95": float(np.mean(covered_p95)) if covered_p95 else 0.0,
        "per_group": per_group,
    }


def _slice_cube(cube: DemandCube, end: int) -> DemandCube:
    """A causal prefix view of the cube -- everything from index 0 up to ``end``."""
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
