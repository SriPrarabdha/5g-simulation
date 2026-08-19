"""Leakage-safe forecasting baselines and evaluation metrics."""

from .baselines import (
    DemandObservation,
    ForecastingError,
    LightGBMQuantileForecaster,
    MovingAverageForecaster,
    ResidualObservation,
    SeasonalNaiveForecaster,
)
from .metrics import QuantileMetrics, evaluate_quantiles
from .bundle import (
    SCHEMA_VERSION as BUNDLE_SCHEMA_VERSION,
    TrainedForecastBundle,
    train_forecast_bundle,
    write_forecast_bundle,
)
from .candidates import (
    CAUSAL_FEATURE_NAMES,
    CAUSAL_LAGS,
    CalendarRidgeV2Forecaster,
    CausalRegimeEnsemble,
    HistGradientBoostingQuantileForecaster,
    causal_features,
)
from .evaluation import (
    ForecastEvaluationRecord,
    evaluate_forecast_records,
    forecast_promotion_gates,
)

__all__ = [
    "DemandObservation",
    "ForecastingError",
    "LightGBMQuantileForecaster",
    "MovingAverageForecaster",
    "QuantileMetrics",
    "ResidualObservation",
    "SeasonalNaiveForecaster",
    "evaluate_quantiles",
    "BUNDLE_SCHEMA_VERSION",
    "TrainedForecastBundle",
    "train_forecast_bundle",
    "write_forecast_bundle",
    "CAUSAL_FEATURE_NAMES",
    "CAUSAL_LAGS",
    "CalendarRidgeV2Forecaster",
    "CausalRegimeEnsemble",
    "HistGradientBoostingQuantileForecaster",
    "causal_features",
    "ForecastEvaluationRecord",
    "evaluate_forecast_records",
    "forecast_promotion_gates",
]
