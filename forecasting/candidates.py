"""Causal forecasting challengers sharing one auditable feature contract."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from schemas import ExistingLoad, Forecast, GroupKey, Quantiles, TimeWindow
from schemas.common import iso_utc, parse_utc

from .baselines import DemandObservation, ForecastingError


CAUSAL_LAGS = (1, 2, 3, 6, 12, 18, 36, 144, 288, 1008)
ROLLING_WINDOWS = (3, 6, 12, 36)
EVENT_FEATURES = (
    "same_zone_aggregate", "neighboring_group_aggregate", "known_event_phase",
    "known_event_lead_minutes", "time_since_observable_anomaly_minutes",
)
CAUSAL_FEATURE_NAMES = (
    "intercept",
    *(f"lag_{lag}" for lag in CAUSAL_LAGS),
    *(f"rolling_{stat}_{window}" for window in ROLLING_WINDOWS for stat in ("mean", "std", "max", "slope")),
    "sin_daily", "cos_daily", "sin_weekly", "cos_weekly",
    "ewma_residual", "surge_score", "telemetry_age_seconds",
    "telemetry_missing", "counter_reset",
    *EVENT_FEATURES,
)
SUPPORTED_HORIZONS = (1, 2, 3, 8)
TARGET_FIELDS = ("new_session_count", "new_ul_mbps", "new_dl_mbps")


def _slope(values: np.ndarray) -> float:
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values), dtype=float)
    centered = x - x.mean()
    denominator = float(centered @ centered)
    return float(centered @ (values - values.mean()) / denominator) if denominator else 0.0


def causal_features(
    observations: Sequence[DemandObservation],
    field_name: str,
    origin: int,
    target_start: datetime,
) -> np.ndarray:
    """Build only features available at ``observations[origin].window.end``."""

    if field_name not in TARGET_FIELDS or not 0 <= origin < len(observations):
        raise ValueError("invalid causal feature request")
    observation = observations[origin]
    observation.assert_causal_at(observation.window.end)
    values = np.asarray([float(getattr(item, field_name)) for item in observations], dtype=float)
    row: list[float] = [1.0]
    for lag in CAUSAL_LAGS:
        index = origin + 1 - lag
        row.append(float(values[index]) if index >= 0 else float(values[0]))
    for window in ROLLING_WINDOWS:
        recent = values[max(0, origin - window + 1):origin + 1]
        row.extend((float(recent.mean()), float(recent.std()), float(recent.max()), _slope(recent)))
    target_start = parse_utc(target_start)
    daily = 2 * math.pi * (target_start.hour * 3600 + target_start.minute * 60 + target_start.second) / 86_400
    weekly = 2 * math.pi * (target_start.weekday() + daily / (2 * math.pi)) / 7
    row.extend((math.sin(daily), math.cos(daily), math.sin(weekly), math.cos(weekly)))
    recent = values[max(0, origin - 35):origin + 1]
    ewma = float(recent[0])
    for value in recent[1:]:
        ewma = 0.25 * float(value) + 0.75 * ewma
    residual = float(values[origin] - ewma)
    scale = max(1e-9, float(recent.std()))
    row.extend((residual, residual / scale, observation.telemetry_age_seconds,
                float(observation.telemetry_missing), float(observation.counter_reset)))
    row.extend(float(observation.event_features.get(name, 0.0)) for name in EVENT_FEATURES)
    result = np.asarray(row, dtype=float)
    if len(result) != len(CAUSAL_FEATURE_NAMES) or not np.all(np.isfinite(result)):
        raise ForecastingError("causal feature row is invalid")
    return result


def _training_rows(
    sequences: Sequence[Sequence[DemandObservation]], field_name: str, horizon: int,
) -> tuple[np.ndarray, np.ndarray]:
    rows: list[np.ndarray] = []
    targets: list[float] = []
    for sequence in sequences:
        ordered = sorted(sequence, key=lambda item: item.window.end)
        for origin in range(max(CAUSAL_LAGS) - 1, len(ordered) - horizon):
            target = origin + horizon
            rows.append(causal_features(ordered, field_name, origin, ordered[target].window.start))
            targets.append(float(getattr(ordered[target], field_name)))
    if len(rows) < 24:
        raise ForecastingError(
            f"candidate needs at least 24 rows after {max(CAUSAL_LAGS)} causal history windows"
        )
    return np.vstack(rows), np.asarray(targets, dtype=float)


def _existing(observations: Sequence[DemandObservation]) -> list[ExistingLoad]:
    residual = observations[-1].existing_load_by_upf
    return [
        ExistingLoad(
            upf_id,
            Quantiles(value.surviving_sessions, value.surviving_sessions, value.surviving_sessions),
            Quantiles(value.ul_mbps, value.ul_mbps, value.ul_mbps),
            Quantiles(value.dl_mbps, value.dl_mbps, value.dl_mbps),
        )
        for upf_id, value in sorted(residual.items())
    ]


@dataclass(slots=True)
class _CandidateBase:
    model_version: str
    horizons: tuple[int, ...] = SUPPORTED_HORIZONS
    models: dict[tuple[str, str, int], Any] = field(default_factory=dict, init=False, repr=False)
    group_keys: dict[str, GroupKey] = field(default_factory=dict, init=False, repr=False)
    calibration_widths: dict[tuple[str, str, int], tuple[float, float]] = field(
        default_factory=dict, init=False, repr=False
    )

    @property
    def required_history_windows(self) -> int:
        return max(CAUSAL_LAGS)

    @property
    def feature_schema(self) -> dict[str, Any]:
        return {
            "names": list(CAUSAL_FEATURE_NAMES),
            "lags": list(CAUSAL_LAGS),
            "rolling_windows": list(ROLLING_WINDOWS),
            "available_at_enforced": True,
        }

    def _validate_predict(
        self, history: Iterable[DemandObservation], issued_at: datetime, horizon_steps: int,
    ) -> list[DemandObservation]:
        ordered = sorted(history, key=lambda item: item.window.end)
        if not ordered or horizon_steps not in self.horizons:
            raise ForecastingError("candidate history or horizon is unsupported")
        issued_at = parse_utc(issued_at)
        if ordered[-1].window.end > issued_at:
            raise ForecastingError("forecast history leaks future observations")
        group_id = ordered[0].group.selection_id
        if any(item.group.selection_id != group_id for item in ordered):
            raise ForecastingError("history must contain one selection group")
        if len(ordered) < self.required_history_windows:
            raise ForecastingError("insufficient causal history")
        return ordered

    def _forecast(
        self, ordered: list[DemandObservation], issued_at: datetime,
        target_window: TimeWindow, horizon_steps: int, predictions: Mapping[str, Quantiles],
        flags: list[str],
    ) -> Forecast:
        group_id = ordered[0].group.selection_id
        identity = f"{self.model_version}|{group_id}|{iso_utc(issued_at)}|{horizon_steps}"
        return Forecast(
            hashlib.sha256(identity.encode()).hexdigest()[:24], issued_at,
            ordered[-1].window.end, target_window, horizon_steps,
            self.group_keys[group_id], predictions["new_session_count"],
            predictions["new_ul_mbps"], predictions["new_dl_mbps"],
            _existing(ordered), self.model_version, flags,
        )


@dataclass(slots=True)
class CalendarRidgeV2Forecaster(_CandidateBase):
    ridge: float = 1e-4
    model_family: str = field(default="ridge-v2", init=False)

    def fit(self, series_by_group: Mapping[str, Sequence[Sequence[DemandObservation]]]) -> "CalendarRidgeV2Forecaster":
        for group_id, sequences in sorted(series_by_group.items()):
            nonempty = [list(items) for items in sequences if items]
            if not nonempty:
                raise ForecastingError(f"group {group_id} has no observations")
            self.group_keys[group_id] = nonempty[0][0].group
            for field_name in TARGET_FIELDS:
                for horizon in self.horizons:
                    x, y = _training_rows(nonempty, field_name, horizon)
                    split = max(12, int(len(y) * 0.85))
                    scale = np.sqrt(np.mean(np.square(x[:split]), axis=0))
                    scale[~np.isfinite(scale) | (scale < 1e-12)] = 1.0
                    identity = np.eye(x.shape[1]); identity[0, 0] = 0.0
                    coefficients = np.linalg.solve(
                        (x[:split] / scale).T @ (x[:split] / scale) + self.ridge * identity,
                        (x[:split] / scale).T @ y[:split],
                    ) / scale
                    residual = np.abs(y[split:] - np.maximum(0.0, x[split:] @ coefficients))
                    widths = np.quantile(residual, (0.90, 0.95)) if len(residual) else (0.0, 0.0)
                    self.models[(group_id, field_name, horizon)] = (
                        coefficients, float(widths[0]), float(widths[1])
                    )
        return self

    def predict(self, history: Iterable[DemandObservation], *, issued_at: datetime,
                target_window: TimeWindow, horizon_steps: int = 1) -> Forecast:
        ordered = self._validate_predict(history, issued_at, horizon_steps)
        group_id = ordered[0].group.selection_id
        predictions: dict[str, Quantiles] = {}
        for field_name in TARGET_FIELDS:
            try:
                coefficients, p90_width, p95_width = self.models[(group_id, field_name, horizon_steps)]
            except KeyError as error:
                raise ForecastingError("ridge-v2 model is not fitted for this group/horizon") from error
            row = causal_features(ordered, field_name, len(ordered) - 1, target_window.start)
            p50 = max(0.0, float(row @ coefficients))
            if (group_id, field_name, horizon_steps) in self.calibration_widths:
                p90_width, p95_width = self.calibration_widths[
                    (group_id, field_name, horizon_steps)
                ]
            predictions[field_name] = Quantiles(p50, p50 + p95_width, p50 + p90_width)
        return self._forecast(ordered, parse_utc(issued_at), target_window, horizon_steps,
                              predictions, ["causal_features", "split_conformal"])


@dataclass(slots=True)
class HistGradientBoostingQuantileForecaster(_CandidateBase):
    max_iter: int = 150
    max_leaf_nodes: int = 31
    random_state: int = 0
    model_family: str = field(default="hist-gradient-quantile", init=False)

    def fit(self, series_by_group: Mapping[str, Sequence[Sequence[DemandObservation]]]) -> "HistGradientBoostingQuantileForecaster":
        try:
            from sklearn.ensemble import HistGradientBoostingRegressor
        except ImportError as error:
            raise ForecastingError("scikit-learn is required for histogram gradient boosting") from error
        for group_id, sequences in sorted(series_by_group.items()):
            nonempty = [list(items) for items in sequences if items]
            if not nonempty:
                raise ForecastingError(f"group {group_id} has no observations")
            self.group_keys[group_id] = nonempty[0][0].group
            for field_name in TARGET_FIELDS:
                for horizon in self.horizons:
                    x, y = _training_rows(nonempty, field_name, horizon)
                    fitted = []
                    for quantile in (0.50, 0.90, 0.95):
                        model = HistGradientBoostingRegressor(
                            loss="quantile", quantile=quantile,
                            max_iter=self.max_iter, max_leaf_nodes=self.max_leaf_nodes,
                            random_state=self.random_state, early_stopping=False,
                        )
                        fitted.append(model.fit(x, y))
                    self.models[(group_id, field_name, horizon)] = tuple(fitted)
        return self

    def predict(self, history: Iterable[DemandObservation], *, issued_at: datetime,
                target_window: TimeWindow, horizon_steps: int = 1) -> Forecast:
        ordered = self._validate_predict(history, issued_at, horizon_steps)
        group_id = ordered[0].group.selection_id
        predictions: dict[str, Quantiles] = {}
        for field_name in TARGET_FIELDS:
            try:
                models = self.models[(group_id, field_name, horizon_steps)]
            except KeyError as error:
                raise ForecastingError("hist-gradient model is not fitted for this group/horizon") from error
            row = causal_features(ordered, field_name, len(ordered) - 1, target_window.start)[None, :]
            p50, p90, p95 = sorted(max(0.0, float(model.predict(row)[0])) for model in models)
            if (group_id, field_name, horizon_steps) in self.calibration_widths:
                p90_width, p95_width = self.calibration_widths[
                    (group_id, field_name, horizon_steps)
                ]
                p90, p95 = p50 + p90_width, p50 + p95_width
            predictions[field_name] = Quantiles(p50, p95, p90)
        return self._forecast(ordered, parse_utc(issued_at), target_window, horizon_steps,
                              predictions, ["causal_features", "quantile_order_corrected"])


@dataclass(slots=True)
class LightGBMQuantileCandidate(_CandidateBase):
    n_estimators: int = 150
    max_depth: int = -1
    random_state: int = 0
    model_family: str = field(default="lightgbm-quantile", init=False)

    def fit(self, series_by_group: Mapping[str, Sequence[Sequence[DemandObservation]]]) -> "LightGBMQuantileCandidate":
        try:
            from lightgbm import LGBMRegressor
        except ImportError as error:
            raise ForecastingError("LightGBM is required for the LightGBM candidate") from error
        for group_id, sequences in sorted(series_by_group.items()):
            nonempty = [list(items) for items in sequences if items]
            if not nonempty:
                raise ForecastingError(f"group {group_id} has no observations")
            self.group_keys[group_id] = nonempty[0][0].group
            for field_name in TARGET_FIELDS:
                for horizon in self.horizons:
                    x, y = _training_rows(nonempty, field_name, horizon)
                    fitted = []
                    for quantile in (0.50, 0.90, 0.95):
                        fitted.append(LGBMRegressor(
                            objective="quantile", alpha=quantile,
                            n_estimators=self.n_estimators, max_depth=self.max_depth,
                            random_state=self.random_state, n_jobs=1,
                            deterministic=True, force_col_wise=True, verbosity=-1,
                        ).fit(x, y))
                    self.models[(group_id, field_name, horizon)] = tuple(fitted)
        return self

    def predict(self, history: Iterable[DemandObservation], *, issued_at: datetime,
                target_window: TimeWindow, horizon_steps: int = 1) -> Forecast:
        ordered = self._validate_predict(history, issued_at, horizon_steps)
        group_id = ordered[0].group.selection_id
        predictions: dict[str, Quantiles] = {}
        for field_name in TARGET_FIELDS:
            try:
                models = self.models[(group_id, field_name, horizon_steps)]
            except KeyError as error:
                raise ForecastingError("LightGBM candidate is not fitted for this group/horizon") from error
            row = causal_features(ordered, field_name, len(ordered) - 1, target_window.start)[None, :]
            p50, p90, p95 = sorted(max(0.0, float(model.predict(row)[0])) for model in models)
            if (group_id, field_name, horizon_steps) in self.calibration_widths:
                p90_width, p95_width = self.calibration_widths[
                    (group_id, field_name, horizon_steps)
                ]
                p90, p95 = p50 + p90_width, p50 + p95_width
            predictions[field_name] = Quantiles(p50, p95, p90)
        return self._forecast(ordered, parse_utc(issued_at), target_window, horizon_steps,
                              predictions, ["causal_features", "lightgbm_quantile"])


@dataclass(slots=True)
class CausalRegimeEnsemble:
    normal_model: CalendarRidgeV2Forecaster
    surge_threshold: float = 2.0
    model_family: str = field(default="regime-ensemble", init=False)
    model_version: str = field(init=False)

    def __post_init__(self) -> None:
        self.model_version = f"regime-ensemble/{self.normal_model.model_version}"

    @property
    def required_history_windows(self) -> int:
        return self.normal_model.required_history_windows

    @property
    def feature_schema(self) -> dict[str, Any]:
        return self.normal_model.feature_schema

    def predict(self, history: Iterable[DemandObservation], *, issued_at: datetime,
                target_window: TimeWindow, horizon_steps: int = 1) -> Forecast:
        ordered = sorted(history, key=lambda item: item.window.end)
        baseline = self.normal_model.predict(
            ordered, issued_at=issued_at, target_window=target_window,
            horizon_steps=horizon_steps,
        )
        latest = ordered[-1]
        lead = float(latest.event_features.get("known_event_lead_minutes", 0.0))
        known_event = (
            latest.regime == "scheduled_event"
            or float(latest.event_features.get("known_event_phase", 0.0)) != 0.0
            or 0.0 < lead <= horizon_steps * 10.0
        )
        prior = [item.new_session_count for item in ordered[-7:-1]]
        detected = bool(prior) and latest.new_session_count > self.surge_threshold * max(1.0, float(np.median(prior)))
        if not (known_event or detected):
            baseline.model_version = self.model_version
            baseline.quality_flags.append("normal_calendar_ridge")
            return baseline
        # A declared event may be used immediately; an unknown surge switches
        # only after the latest completed bucket provides observable evidence.
        for attr, field_name in (
            ("new_session_count", "new_session_count"),
            ("new_load_ul_mbps", "new_ul_mbps"),
            ("new_load_dl_mbps", "new_dl_mbps"),
        ):
            values = [float(getattr(item, field_name)) for item in ordered]
            trend = values[-1] - values[-2]
            point = max(0.0, values[-1] + horizon_steps * trend)
            current = getattr(baseline, attr)
            p50 = max(current.p50, point)
            spread90 = max(0.0, (current.p90 or current.p95) - current.p50)
            spread95 = max(spread90, current.p95 - current.p50)
            setattr(baseline, attr, Quantiles(p50, p50 + spread95, p50 + spread90))
        baseline.model_version = self.model_version
        baseline.quality_flags.append(
            "declared_event_model" if known_event else "post_observation_surge_model"
        )
        return baseline
