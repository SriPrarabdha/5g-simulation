from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from schemas import ExistingLoad, Forecast, GroupKey, Quantiles, TimeWindow
from schemas.common import iso_utc, parse_utc

from .baselines import DemandObservation, ForecastingError


SCHEMA_VERSION = "trained-forecast-bundle/1.0"
TARGET_FIELDS = ("new_session_count", "new_ul_mbps", "new_dl_mbps")
FEATURE_NAMES = (
    "intercept",
    "last",
    "rolling_mean_6",
    "recent_trend",
    "daily_seasonal",
    "sin_time_of_day",
    "cos_time_of_day",
    "sin_day_of_week",
    "cos_day_of_week",
)
CALIBRATION_LEVELS = (0.50, 0.75, 0.80, 0.85, 0.90, 0.925, 0.95, 0.975, 0.99)


def _group_dict(group: GroupKey) -> dict[str, Any]:
    return {
        "zone": group.zone,
        "dnn": group.dnn,
        "snssai": group.snssai,
        "five_qi": group.five_qi,
    }


def _features(
    observations: list[DemandObservation],
    field: str,
    origin: int,
    target_start: datetime,
) -> list[float]:
    values = [float(getattr(item, field)) for item in observations]
    recent = values[max(0, origin - 5): origin + 1]
    trend = values[origin] - values[max(0, origin - 1)]
    daily_index = origin - 143
    seasonal = values[daily_index] if daily_index >= 0 else statistics.fmean(recent)
    seconds = target_start.hour * 3600 + target_start.minute * 60 + target_start.second
    daily_angle = 2 * math.pi * seconds / 86_400
    weekly_angle = 2 * math.pi * target_start.weekday() / 7
    return [
        1.0,
        values[origin],
        statistics.fmean(recent),
        trend,
        seasonal,
        math.sin(daily_angle),
        math.cos(daily_angle),
        math.sin(weekly_angle),
        math.cos(weekly_angle),
    ]


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    return float(np.quantile(np.asarray(values, dtype=float), probability, method="linear"))


def _interpolate_width(model: dict[str, Any], probability: float) -> float:
    levels = [float(item) for item in model["calibration_levels"]]
    widths = [float(item) for item in model["calibration_widths"]]
    return float(np.interp(probability, levels, widths))


def _fit_direct_model(
    sequences: list[list[DemandObservation]],
    field: str,
    horizon: int,
    *,
    ridge: float,
) -> tuple[dict[str, Any], dict[str, float]]:
    rows: list[list[float]] = []
    targets: list[float] = []
    for observations in sequences:
        for origin in range(5, len(observations) - horizon):
            target_index = origin + horizon
            rows.append(_features(
                observations,
                field,
                origin,
                observations[target_index].window.start,
            ))
            targets.append(float(getattr(observations[target_index], field)))
    if len(rows) < 24:
        raise ForecastingError(
            f"offline bundle needs at least 24 training rows for {field} horizon {horizon}"
        )
    train_end = max(12, int(len(rows) * 0.70))
    calibration_end = max(train_end + 6, int(len(rows) * 0.85))
    calibration_end = min(calibration_end, len(rows) - 1)
    x = np.asarray(rows, dtype=float)
    y = np.asarray(targets, dtype=float)
    identity = np.eye(x.shape[1], dtype=float)
    identity[0, 0] = 0.0
    x_train = x[:train_end]
    # National-scale session counts make the raw normal equations poorly
    # conditioned.  RMS scaling preserves the intercept, keeps ridge strength
    # comparable across features, and converts back to the existing bundle
    # coefficient contract for inference.
    feature_scale = np.sqrt(np.mean(np.square(x_train), axis=0))
    feature_scale[0] = 1.0
    feature_scale[~np.isfinite(feature_scale) | (feature_scale < 1e-12)] = 1.0
    scaled_train = x_train / feature_scale
    scaled_coefficients = np.linalg.solve(
        scaled_train.T @ scaled_train + ridge * identity,
        scaled_train.T @ y[:train_end],
    )
    coefficients = scaled_coefficients / feature_scale
    calibration_raw = x[train_end:calibration_end] @ coefficients
    calibration_y = y[train_end:calibration_end]
    median_bias = float(np.median(calibration_y - calibration_raw))
    calibration_point = np.maximum(0.0, calibration_raw + median_bias)
    absolute_residuals = np.abs(calibration_y - calibration_point).tolist()
    widths = [_quantile(absolute_residuals, level) for level in CALIBRATION_LEVELS]
    test_point = np.maximum(0.0, x[calibration_end:] @ coefficients + median_bias)
    test_y = y[calibration_end:]
    absolute = np.abs(test_y - test_point)
    denominator = float(np.sum(np.abs(test_y)))
    p90_width = _quantile(absolute_residuals, 0.90)
    p95_width = _quantile(absolute_residuals, 0.95)
    metrics = {
        "rows": float(len(rows)),
        "train_rows": float(train_end),
        "calibration_rows": float(calibration_end - train_end),
        "test_rows": float(len(rows) - calibration_end),
        "mae_p50": float(np.mean(absolute)) if len(absolute) else 0.0,
        "wape_p50": float(np.sum(absolute) / denominator) if denominator else 0.0,
        "coverage_p90": float(np.mean(test_y <= test_point + p90_width)) if len(test_y) else 0.0,
        "coverage_p95": float(np.mean(test_y <= test_point + p95_width)) if len(test_y) else 0.0,
    }
    return ({
        "coefficients": [float(item) for item in coefficients],
        "median_bias": median_bias,
        "calibration_levels": list(CALIBRATION_LEVELS),
        "calibration_widths": widths,
    }, metrics)


def train_forecast_bundle(
    series_by_group: dict[str, list[list[DemandObservation]]],
    *,
    model_version: str,
    source: dict[str, Any] | None = None,
    ridge: float = 1e-6,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Train a deterministic direct multi-horizon model and freeze JSON-safe state."""
    if not series_by_group:
        raise ForecastingError("no grouped training series were supplied")
    groups: dict[str, Any] = {}
    metric_rows: list[float] = []
    ordered_groups = sorted(series_by_group.items())
    for group_index, (group_id, sequences) in enumerate(ordered_groups, start=1):
        nonempty = [list(items) for items in sequences if items]
        if not nonempty:
            raise ForecastingError(f"group {group_id} has no training observations")
        key = nonempty[0][0].group
        targets: dict[str, Any] = {}
        for field in TARGET_FIELDS:
            by_horizon: dict[str, Any] = {}
            for horizon in range(1, 9):
                model, metrics = _fit_direct_model(nonempty, field, horizon, ridge=ridge)
                by_horizon[str(horizon)] = {"model": model, "metrics": metrics}
                metric_rows.append(metrics["wape_p50"])
            targets[field] = by_horizon
        groups[group_id] = {"key": _group_dict(key), "targets": targets}
        if progress_callback is not None:
            progress_callback(group_index, len(ordered_groups), group_id)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model_version": model_version,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "synthetic": True,
        "algorithm": "calendar-ridge-direct-multihorizon",
        "feature_names": list(FEATURE_NAMES),
        "horizon_minutes": list(range(10, 81, 10)),
        "calibration": {
            "method": "split-conformal-aci",
            "target_alpha": 0.10,
            "adaptive_rate": 0.01,
        },
        "split": {"train": 0.70, "calibration": 0.15, "test": 0.15, "ordered": True},
        "source": source or {},
        "summary_metrics": {"mean_test_wape_p50": statistics.fmean(metric_rows)},
        "groups": groups,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["bundle_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def write_forecast_bundle(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)


@dataclass(slots=True)
class TrainedForecastBundle:
    payload: dict[str, Any]
    _alpha_by_group: dict[str, float] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.payload.get("schema_version") != SCHEMA_VERSION:
            raise ForecastingError("unsupported trained forecast bundle schema")
        canonical = dict(self.payload)
        expected = canonical.pop("bundle_sha256", None)
        actual = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if expected != actual:
            raise ForecastingError("trained forecast bundle checksum mismatch")
        self._alpha_by_group = {
            group_id: float(self.payload["calibration"]["target_alpha"])
            for group_id in self.payload["groups"]
        }

    @classmethod
    def load(cls, path: str | Path) -> "TrainedForecastBundle":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def model_version(self) -> str:
        return str(self.payload["model_version"])

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            key: self.payload[key]
            for key in (
                "schema_version", "model_version", "created_at", "synthetic", "algorithm",
                "feature_names", "horizon_minutes", "calibration", "split", "source",
                "summary_metrics", "bundle_sha256",
            )
        }

    def predict(
        self,
        history: Iterable[DemandObservation],
        *,
        issued_at: datetime,
        target_window: TimeWindow,
        horizon_steps: int = 1,
    ) -> Forecast:
        observations = sorted(history, key=lambda item: item.window.end)
        if not observations:
            raise ForecastingError("forecast history is empty")
        if horizon_steps not in range(1, 9):
            raise ForecastingError("bundle horizon must be 1 through 8")
        issued_at = parse_utc(issued_at)
        if observations[-1].window.end > issued_at:
            raise ForecastingError("forecast history leaks future observations")
        group_id = observations[0].group.selection_id
        if any(item.group.selection_id != group_id for item in observations):
            raise ForecastingError("history must contain one selection group")
        try:
            group_model = self.payload["groups"][group_id]
        except KeyError as error:
            raise ForecastingError(f"bundle has no model for group {group_id}") from error
        origin = len(observations) - 1
        alpha = self._alpha_by_group[group_id]

        def predict_field(field: str) -> Quantiles:
            entry = group_model["targets"][field][str(horizon_steps)]["model"]
            features = np.asarray(
                _features(observations, field, origin, target_window.start), dtype=float
            )
            p50 = max(0.0, float(features @ np.asarray(entry["coefficients"])) + float(entry["median_bias"]))
            return Quantiles(
                p50=p50,
                p90=p50 + _interpolate_width(entry, 1.0 - alpha),
                p95=p50 + _interpolate_width(entry, 0.95),
            )

        residual = observations[-1].existing_load_by_upf
        existing = [
            ExistingLoad(
                upf_id,
                Quantiles(value.surviving_sessions, value.surviving_sessions, value.surviving_sessions),
                Quantiles(value.ul_mbps, value.ul_mbps, value.ul_mbps),
                Quantiles(value.dl_mbps, value.dl_mbps, value.dl_mbps),
            )
            for upf_id, value in sorted(residual.items())
        ]
        source_end = observations[-1].window.end
        identity = f"{self.model_version}|{group_id}|{iso_utc(issued_at)}|{horizon_steps}"
        return Forecast(
            forecast_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
            issued_at=issued_at,
            source_window_end=source_end,
            target_window=target_window,
            horizon_steps=horizon_steps,
            group=GroupKey.from_dict(group_model["key"]),
            new_session_count=predict_field("new_session_count"),
            new_load_ul_mbps=predict_field("new_ul_mbps"),
            new_load_dl_mbps=predict_field("new_dl_mbps"),
            existing_load_by_upf=existing,
            model_version=self.model_version,
            quality_flags=["synthetic_training", "adaptive_conformal"],
        )

    def observe_coverage(self, group_id: str, *, covered: bool) -> float:
        """Update ACI miscoverage level after a realized p90 outcome."""
        target = float(self.payload["calibration"]["target_alpha"])
        rate = float(self.payload["calibration"]["adaptive_rate"])
        alpha = self._alpha_by_group[group_id]
        error = 0.0 if covered else 1.0
        alpha = min(0.49, max(0.01, alpha + rate * (target - error)))
        self._alpha_by_group[group_id] = alpha
        return alpha
