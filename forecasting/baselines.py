from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Protocol

from schemas import ExistingLoad, Forecast, GroupKey, Quantiles, TimeWindow
from schemas.common import iso_utc, parse_utc


class ForecastingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResidualObservation:
    surviving_sessions: float
    ul_mbps: float
    dl_mbps: float

    def __post_init__(self) -> None:
        if min(self.surviving_sessions, self.ul_mbps, self.dl_mbps) < 0:
            raise ValueError("residual observations must be non-negative")


@dataclass(frozen=True, slots=True)
class DemandObservation:
    window: TimeWindow
    group: GroupKey
    new_session_count: float
    new_ul_mbps: float
    new_dl_mbps: float
    existing_load_by_upf: dict[str, ResidualObservation] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if min(self.new_session_count, self.new_ul_mbps, self.new_dl_mbps) < 0:
            raise ValueError("demand observations must be non-negative")


class Forecaster(Protocol):
    model_version: str

    def predict(
        self,
        history: Iterable[DemandObservation],
        *,
        issued_at: datetime,
        target_window: TimeWindow,
        horizon_steps: int = 1,
    ) -> Forecast: ...


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _quantiles(values: list[float]) -> Quantiles:
    # Linear interpolation can place mathematically equal quantiles a few ULPs
    # out of order (for example 55.35 versus 55.349999999999994).  Sorting is
    # the deterministic monotonicity correction also used by the fitted model.
    p50, p90, p95 = sorted((
        _quantile(values, 0.50),
        _quantile(values, 0.90),
        _quantile(values, 0.95),
    ))
    return Quantiles(
        p50=p50,
        p90=p90,
        p95=p95,
    )


def _point_with_error(point: float, errors: list[float]) -> Quantiles:
    return Quantiles(
        p50=max(0.0, point),
        p90=max(0.0, point + _quantile(errors, 0.90)),
        p95=max(0.0, point + _quantile(errors, 0.95)),
    )


def _prepare(
    history: Iterable[DemandObservation],
    issued_at: datetime,
    target_window: TimeWindow,
    horizon_steps: int,
) -> list[DemandObservation]:
    issued_at = parse_utc(issued_at)
    if horizon_steps not in {1, 2}:
        raise ForecastingError("horizon_steps must be 1 or 2")
    if issued_at > target_window.start:
        raise ForecastingError("forecast cannot be issued after target-window start")
    ordered = sorted(history, key=lambda item: item.window.end)
    if not ordered:
        raise ForecastingError("forecast history is empty")
    group_id = ordered[0].group.selection_id
    if any(item.group.selection_id != group_id for item in ordered):
        raise ForecastingError("history must contain exactly one selection group")
    if len({item.window.end for item in ordered}) != len(ordered):
        raise ForecastingError("history contains duplicate windows")
    if any(item.window.end > issued_at for item in ordered):
        raise ForecastingError("history leaks observations unavailable at issue time")
    if ordered[-1].window.end > target_window.start:
        raise ForecastingError("history overlaps target window")
    return ordered


def _forecast_id(model: str, group: GroupKey, issued_at: datetime, target: TimeWindow) -> str:
    payload = f"{model}|{group.selection_id}|{iso_utc(issued_at)}|{iso_utc(target.start)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _existing_from_observations(observations: list[DemandObservation]) -> list[ExistingLoad]:
    upf_ids = sorted({upf_id for item in observations for upf_id in item.existing_load_by_upf})
    result: list[ExistingLoad] = []
    for upf_id in upf_ids:
        values = [item.existing_load_by_upf[upf_id] for item in observations if upf_id in item.existing_load_by_upf]
        if not values:
            continue
        result.append(ExistingLoad(
            upf_id=upf_id,
            surviving_sessions=_quantiles([value.surviving_sessions for value in values]),
            ul_mbps=_quantiles([value.ul_mbps for value in values]),
            dl_mbps=_quantiles([value.dl_mbps for value in values]),
        ))
    return result


def _build_forecast(
    *,
    model_version: str,
    ordered: list[DemandObservation],
    issued_at: datetime,
    target_window: TimeWindow,
    horizon_steps: int,
    arrivals: Quantiles,
    ul: Quantiles,
    dl: Quantiles,
    residual_source: list[DemandObservation],
    flags: list[str],
) -> Forecast:
    group = ordered[0].group
    return Forecast(
        forecast_id=_forecast_id(model_version, group, issued_at, target_window),
        issued_at=issued_at,
        source_window_end=ordered[-1].window.end,
        target_window=target_window,
        horizon_steps=horizon_steps,
        group=GroupKey(group.zone, group.dnn, group.snssai),
        new_session_count=arrivals,
        new_load_ul_mbps=ul,
        new_load_dl_mbps=dl,
        existing_load_by_upf=_existing_from_observations(residual_source),
        model_version=model_version,
        quality_flags=flags,
    )


class MovingAverageForecaster:
    def __init__(self, window_size: int = 6) -> None:
        if window_size < 1:
            raise ValueError("window_size must be positive")
        self.window_size = window_size
        self.model_version = f"moving-average/{window_size}"

    def predict(
        self,
        history: Iterable[DemandObservation],
        *,
        issued_at: datetime,
        target_window: TimeWindow,
        horizon_steps: int = 1,
    ) -> Forecast:
        ordered = _prepare(history, issued_at, target_window, horizon_steps)
        selected = ordered[-self.window_size:]
        flags = [] if len(selected) == self.window_size else ["short_history"]
        return _build_forecast(
            model_version=self.model_version,
            ordered=ordered,
            issued_at=parse_utc(issued_at),
            target_window=target_window,
            horizon_steps=horizon_steps,
            arrivals=_quantiles([item.new_session_count for item in selected]),
            ul=_quantiles([item.new_ul_mbps for item in selected]),
            dl=_quantiles([item.new_dl_mbps for item in selected]),
            residual_source=selected,
            flags=flags + sorted({flag for item in selected for flag in item.quality_flags}),
        )


class SeasonalNaiveForecaster:
    def __init__(self, season_steps: int = 144) -> None:
        if season_steps < 1:
            raise ValueError("season_steps must be positive")
        self.season_steps = season_steps
        self.model_version = f"seasonal-naive/{season_steps}"

    @staticmethod
    def _errors(ordered: list[DemandObservation], season_steps: int, field_name: str) -> list[float]:
        return [
            abs(getattr(ordered[index], field_name) - getattr(ordered[index - season_steps], field_name))
            for index in range(season_steps, len(ordered))
        ]

    def predict(
        self,
        history: Iterable[DemandObservation],
        *,
        issued_at: datetime,
        target_window: TimeWindow,
        horizon_steps: int = 1,
    ) -> Forecast:
        ordered = _prepare(history, issued_at, target_window, horizon_steps)
        seasonal_index = len(ordered) - self.season_steps + horizon_steps - 1
        flags: list[str] = []
        if seasonal_index < 0 or seasonal_index >= len(ordered):
            seasonal_index = len(ordered) - 1
            flags.append("seasonal_history_unavailable")
        point = ordered[seasonal_index]
        return _build_forecast(
            model_version=self.model_version,
            ordered=ordered,
            issued_at=parse_utc(issued_at),
            target_window=target_window,
            horizon_steps=horizon_steps,
            arrivals=_point_with_error(
                point.new_session_count,
                self._errors(ordered, self.season_steps, "new_session_count"),
            ),
            ul=_point_with_error(
                point.new_ul_mbps,
                self._errors(ordered, self.season_steps, "new_ul_mbps"),
            ),
            dl=_point_with_error(
                point.new_dl_mbps,
                self._errors(ordered, self.season_steps, "new_dl_mbps"),
            ),
            residual_source=[point],
            flags=flags + list(point.quality_flags),
        )


class LightGBMQuantileForecaster:
    """Leakage-safe LightGBM p50/p90/p95 baseline.

    Training rows at target index ``i`` use an origin at ``i-horizon``. This
    makes the same feature builder valid for both one- and two-step actuation.
    """

    def __init__(
        self,
        *,
        season_steps: int = 144,
        rolling_steps: int = 6,
        n_estimators: int = 100,
        min_training_rows: int = 12,
    ) -> None:
        if min(season_steps, rolling_steps, n_estimators, min_training_rows) < 1:
            raise ValueError("LightGBM parameters must be positive")
        self.season_steps = season_steps
        self.rolling_steps = rolling_steps
        self.n_estimators = n_estimators
        self.min_training_rows = min_training_rows
        self.model_version = (
            f"lightgbm-quantile/season={season_steps},rolling={rolling_steps},trees={n_estimators}"
        )

    def _features(
        self,
        ordered: list[DemandObservation],
        field_name: str,
        origin_index: int,
        target_window: TimeWindow,
    ) -> list[float]:
        values = [float(getattr(item, field_name)) for item in ordered]
        recent = values[max(0, origin_index - self.rolling_steps + 1): origin_index + 1]
        seasonal_index = origin_index + 1 - self.season_steps
        seasonal = values[seasonal_index] if seasonal_index >= 0 else values[origin_index]
        seconds = (
            target_window.start.hour * 3600
            + target_window.start.minute * 60
            + target_window.start.second
        )
        angle = 2 * math.pi * seconds / 86_400
        return [
            values[origin_index],
            values[max(0, origin_index - 1)],
            seasonal,
            statistics.fmean(recent),
            math.sin(angle),
            math.cos(angle),
            float(len(ordered[origin_index].quality_flags)),
        ]

    def _fit_target(
        self,
        ordered: list[DemandObservation],
        field_name: str,
        horizon_steps: int,
        target_window: TimeWindow,
    ) -> Quantiles:
        try:
            import lightgbm as lgb
            import numpy as np
        except ImportError as error:
            raise ForecastingError("LightGBM is not installed") from error
        x_train: list[list[float]] = []
        y_train: list[float] = []
        for target_index in range(horizon_steps, len(ordered)):
            origin_index = target_index - horizon_steps
            x_train.append(self._features(
                ordered,
                field_name,
                origin_index,
                ordered[target_index].window,
            ))
            y_train.append(float(getattr(ordered[target_index], field_name)))
        if len(x_train) < self.min_training_rows:
            raise ForecastingError(
                f"LightGBM needs at least {self.min_training_rows + horizon_steps} observations"
            )
        x_target = [self._features(
            ordered,
            field_name,
            len(ordered) - 1,
            target_window,
        )]
        predictions: list[float] = []
        for quantile in (0.50, 0.90, 0.95):
            dataset = lgb.Dataset(
                np.asarray(x_train, dtype=float),
                label=np.asarray(y_train, dtype=float),
                free_raw_data=True,
            )
            model = lgb.train(
                {
                    "objective": "quantile",
                    "alpha": quantile,
                    "learning_rate": 0.05,
                    "num_leaves": 15,
                    "min_data_in_leaf": 5,
                    "seed": 0,
                    "deterministic": True,
                    "force_col_wise": True,
                    "verbosity": -1,
                    "num_threads": 1,
                },
                dataset,
                num_boost_round=self.n_estimators,
            )
            predictions.append(max(0.0, float(model.predict(np.asarray(x_target, dtype=float))[0])))
        # Independently fitted quantiles can cross. Sorting is an explicit,
        # deterministic monotonicity correction, not a policy normalization.
        p50, p90, p95 = sorted(predictions)
        return Quantiles(p50=p50, p90=p90, p95=p95)

    def predict(
        self,
        history: Iterable[DemandObservation],
        *,
        issued_at: datetime,
        target_window: TimeWindow,
        horizon_steps: int = 1,
    ) -> Forecast:
        ordered = _prepare(history, issued_at, target_window, horizon_steps)
        residual_source = ordered[-self.rolling_steps:]
        return _build_forecast(
            model_version=self.model_version,
            ordered=ordered,
            issued_at=parse_utc(issued_at),
            target_window=target_window,
            horizon_steps=horizon_steps,
            arrivals=self._fit_target(ordered, "new_session_count", horizon_steps, target_window),
            ul=self._fit_target(ordered, "new_ul_mbps", horizon_steps, target_window),
            dl=self._fit_target(ordered, "new_dl_mbps", horizon_steps, target_window),
            residual_source=residual_source,
            flags=sorted({flag for item in residual_source for flag in item.quality_flags}),
        )
