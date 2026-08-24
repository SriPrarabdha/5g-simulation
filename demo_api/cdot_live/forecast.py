from __future__ import annotations

import json
import math
import os
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .adapter import quantile
from .config import LiveConfig, ROOT


def _median_iqr(values: list[float]) -> tuple[float, float]:
    median = statistics.median(values)
    spread = quantile(values, 0.75) - quantile(values, 0.25)
    return median, max(spread, max(abs(median) * 0.05, 1e-9))


def _features(values: list[float], target: datetime) -> list[float]:
    median, spread = _median_iqr(values)
    standardized = [(value - median) / spread for value in values]
    recent = standardized[-6:]
    seconds = target.hour * 3600 + target.minute * 60 + target.second
    daily = 2 * math.pi * seconds / 86400
    weekly = 2 * math.pi * target.weekday() / 7
    return [
        1.0,
        standardized[-1],
        statistics.fmean(recent),
        standardized[-1] - standardized[-2] if len(standardized) > 1 else 0.0,
        standardized[-144] if len(standardized) >= 144 else statistics.fmean(recent),
        math.sin(daily), math.cos(daily), math.sin(weekly), math.cos(weekly),
    ]


class GuardedTransferForecaster:
    """Guarded transfer challenger blended only against causal live baselines.

    Donor absolute scale and donor conformal widths are intentionally discarded.
    Behavior is combined across every matching-DNN donor and transformed through
    the live tuple's rolling median/IQR. Prediction widths come only from causal
    residuals on the live series.
    """

    def __init__(self, config: LiveConfig, bundle_path: str | Path | None = None) -> None:
        self.config = config
        default = ROOT / "output" / "stage1" / "models" / "extreme-forecaster-7d-s20260817.json"
        selected = Path(bundle_path or os.environ.get("CDOT_LIVE_SYNTHETIC_BUNDLE", default))
        self.bundle_path = selected
        self.bundle: dict[str, Any] | None = None
        self.load_error: str | None = None
        try:
            payload = json.loads(selected.read_text(encoding="utf-8"))
            if not payload.get("synthetic") or not payload.get("groups"):
                raise ValueError("bundle is not a synthetic trained bundle")
            self.bundle = payload
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.load_error = str(error)

    def _donor_models(self, dnn: str, direction: str, horizon: int) -> list[dict[str, Any]]:
        if self.bundle is None:
            return []
        donor_dnn = self.config.synthetic_dnn.get(dnn)
        field = f"new_{direction}_mbps"
        result = []
        for donor in self.bundle.get("groups", {}).values():
            if donor.get("key", {}).get("dnn") != donor_dnn:
                continue
            try:
                result.append(donor["targets"][field][str(horizon)]["model"])
            except KeyError:
                continue
        return result

    def _transfer(self, values: list[float], dnn: str, direction: str, horizon: int, target: datetime) -> float | None:
        models = self._donor_models(dnn, direction, horizon)
        if not models or len(values) < 3:
            return None
        features = _features(values, target)
        donor_points = []
        for model in models:
            coefficients = [float(value) for value in model.get("coefficients", [])]
            if len(coefficients) != len(features):
                continue
            point = sum(left * right for left, right in zip(coefficients, features)) + float(model.get("median_bias", 0))
            if math.isfinite(point):
                donor_points.append(point)
        if not donor_points:
            return None
        donor_median, donor_iqr = _median_iqr(donor_points)
        # Equal contribution from every matching donor group; no synthetic
        # zone is selected as a privileged analogue for a live TAC.
        behavior = statistics.fmean((value - donor_median) / donor_iqr for value in donor_points)
        live_median, live_iqr = _median_iqr(values[-144:])
        candidate = live_median + behavior * live_iqr
        return candidate if math.isfinite(candidate) and candidate >= 0 else None

    @staticmethod
    def _baseline(values: list[float], method: str, horizon: int) -> float:
        if method == "seasonal-naive/3":
            return values[-3 + ((horizon - 1) % 3)] if len(values) >= 3 else values[-1]
        window = values[-3:]
        return statistics.fmean(window)

    def _backtest(self, values: list[float], dnn: str, direction: str) -> dict[str, Any]:
        methods = ("seasonal-naive/3", "moving-average/3")
        errors = {method: [] for method in methods}
        denominators = {method: 0.0 for method in methods}
        transfer_errors: list[float] = []
        transfer_denominator = 0.0
        points: list[tuple[float, float | None, dict[str, float]]] = []
        for target_index in range(6, len(values)):
            history = values[:target_index]
            actual = values[target_index]
            baselines = {method: self._baseline(history, method, 1) for method in methods}
            for method, point in baselines.items():
                errors[method].append(abs(actual - point))
                denominators[method] += abs(actual)
            target = datetime.now(timezone.utc)
            transfer = self._transfer(history, dnn, direction, 1, target)
            if transfer is not None:
                transfer_errors.append(abs(actual - transfer))
                transfer_denominator += abs(actual)
            points.append((actual, transfer, baselines))
        wape = {
            method: sum(errors[method]) / denominators[method] if denominators[method] else math.inf
            for method in methods
        }
        best = min(methods, key=lambda method: wape[method])
        transfer_wape = sum(transfer_errors) / transfer_denominator if transfer_denominator else math.inf
        contribution = 0.5 if math.isfinite(transfer_wape) and transfer_wape <= wape[best] else 0.0
        fallback = None
        if self.bundle is None:
            fallback = "synthetic_bundle_unavailable"
        elif not transfer_errors:
            fallback = "insufficient_live_history_or_matching_donors"
        elif not math.isfinite(transfer_wape):
            fallback = "synthetic_candidate_non_finite"
        elif transfer_wape > wape[best]:
            fallback = "synthetic_candidate_worse_than_live_baseline"
        residuals = []
        for actual, transfer, baselines in points:
            point = baselines[best]
            if contribution and transfer is not None:
                point = (1 - contribution) * point + contribution * transfer
            residuals.append(abs(actual - point))
        return {
            "baseline": best,
            "baseline_wape": None if not math.isfinite(wape[best]) else wape[best],
            "synthetic_transfer_wape": None if not math.isfinite(transfer_wape) else transfer_wape,
            "synthetic_contribution": contribution,
            "fallback_reason": fallback,
            "residuals": residuals,
            "candidate_rejected": contribution == 0,
        }

    def forecast(self, buckets: list[dict[str, Any]]) -> dict[str, Any]:
        complete = [bucket for bucket in buckets if bucket.get("complete")]
        if not complete:
            raise ValueError("no complete closed telemetry bucket")
        series: dict[str, dict[str, Any]] = {}
        for bucket in complete:
            for item in bucket.get("tuples", []):
                row = series.setdefault(item["tuple_id"], {
                    "tac": item["tac"], "dnn": item["dnn"], "dscp": item["dscp"], "upf": item["upf"],
                    "ul": [], "dl": [],
                })
                row["ul"].append(float(item["ul_rate"]))
                row["dl"].append(float(item["dl_rate"]))
        source_end = datetime.fromisoformat(complete[-1]["end"].replace("Z", "+00:00"))
        issued = datetime.now(timezone.utc)
        rows = []
        all_models = []
        for tuple_id, item in sorted(series.items()):
            directional: dict[str, Any] = {}
            tuple_models = {}
            valid = True
            for direction in ("ul", "dl"):
                values = item[direction]
                model = self._backtest(values, item["dnn"], direction)
                tuple_models[direction] = {key: value for key, value in model.items() if key != "residuals"}
                p90_width = quantile(model["residuals"], 0.90)
                p95_width = quantile(model["residuals"], 0.95)
                points = []
                for horizon in range(1, 9):
                    target = source_end.astimezone(timezone.utc) + timedelta(minutes=10 * horizon)
                    baseline = self._baseline(values, model["baseline"], horizon)
                    transfer = self._transfer(values, item["dnn"], direction, horizon, target)
                    contribution = model["synthetic_contribution"] if transfer is not None else 0.0
                    point = (1 - contribution) * baseline + contribution * (transfer or 0.0)
                    if not math.isfinite(point) or point < 0:
                        valid = False
                        break
                    points.append({
                        "horizon_minutes": horizon * 10,
                        "p50": round(point, 6),
                        "p90": round(point + p90_width, 6),
                        "p95": round(point + p95_width, 6),
                    })
                directional[direction] = points
                all_models.append(model)
            if valid:
                rows.append({
                    "tuple_id": tuple_id, "tac": item["tac"], "dnn": item["dnn"],
                    "dscp": item["dscp"], "source_upf": item["upf"],
                    "unit": self.config.units, "horizons": directional,
                    "model": tuple_models,
                })
        contributions = [item["synthetic_contribution"] for item in all_models]
        baselines = [item["baseline_wape"] for item in all_models if item["baseline_wape"] is not None]
        return {
            "forecast_id": f"cdot-live-{uuid.uuid4().hex}",
            "issued_at": issued.isoformat().replace("+00:00", "Z"),
            "source_window_end": complete[-1]["end"],
            "target": "carried_traffic_rate",
            "unit": self.config.units,
            "assumption": "next-window p95 carried rate is a cold-start steady-state allocation proxy",
            "session_arrivals": "unavailable",
            "cohort_features": "unavailable",
            "cohort_mpc_run": False,
            "rows": rows,
            "model_summary": {
                "synthetic_transfer_contribution": statistics.fmean(contributions) if contributions else 0.0,
                "live_baseline_wape": statistics.fmean(baselines) if baselines else None,
                "fallback_reasons": sorted({item["fallback_reason"] for item in all_models if item["fallback_reason"]}),
                "synthetic_cap": 0.5,
                "bands": "live causal absolute-residual conformal",
                "donor_mapping": dict(self.config.synthetic_dnn),
                "donor_absolute_scale_used": False,
                "donor_band_width_used": False,
            },
        }
