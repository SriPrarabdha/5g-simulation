from __future__ import annotations

import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import LiveConfig
from .smf import extract_tuples, extract_weights, integer_weights, reduced_ratio, tuple_key, with_weights


DEFAULT_TAC_ALLOWLIST = {
    1: ("upf-1", "upf-4"),
    2: ("upf-1", "upf-2"),
    3: ("upf-1", "upf-2", "upf-3"),
    4: ("upf-1", "upf-3", "upf-4"),
}


def _project(values: dict[str, float], lower: dict[str, float], upper: dict[str, float]) -> dict[str, float]:
    result = {key: min(upper[key], max(lower[key], float(values.get(key, 0)))) for key in lower}
    for _ in range(100):
        error = 1.0 - sum(result.values())
        if abs(error) <= 1e-10:
            break
        available = [key for key in result if (result[key] < upper[key] - 1e-12 if error > 0 else result[key] > lower[key] + 1e-12)]
        if not available:
            raise ValueError("weight bounds cannot sum to one")
        share = error / len(available)
        for key in available:
            result[key] = min(upper[key], max(lower[key], result[key] + share))
    return result


def bounded_weights(target: dict[str, float], current: dict[str, float]) -> dict[str, float]:
    keys = sorted(target)
    if not keys:
        raise ValueError("no eligible UPFs")
    target_total = sum(max(0.0, target[key]) for key in keys)
    target = {key: max(0.0, target[key]) / target_total for key in keys}
    current_total = sum(max(0.0, current.get(key, 0)) for key in keys)
    current = ({key: max(0.0, current.get(key, 0)) / current_total for key in keys}
               if current_total else {key: 1 / len(keys) for key in keys})
    conditional_lower = 0.05 if len(keys) > 1 else 1.0
    conditional_upper = 0.75 if len(keys) > 1 else 1.0
    first = _project(target, {key: conditional_lower for key in keys}, {key: conditional_upper for key in keys})
    lower = {key: max(conditional_lower, current[key] - 0.10) for key in keys}
    upper = {key: min(conditional_upper, current[key] + 0.10) for key in keys}
    return _project(first, lower, upper)


def bounded_integer_weights(target: dict[str, int], current: dict[str, int]) -> dict[str, int]:
    """Keep rounding from turning a continuous 10pp bound into an 11pp write."""
    if not current:
        return dict(target)
    keys = sorted(target)
    lower = {key: max(5 if len(keys) > 1 else 100, current.get(key, 0) - 10) for key in keys}
    upper = {key: min(75 if len(keys) > 1 else 100, current.get(key, 0) + 10) for key in keys}
    result = {key: min(upper[key], max(lower[key], target[key])) for key in keys}
    while sum(result.values()) != 100:
        increase = sum(result.values()) < 100
        candidates = [key for key in keys if result[key] < upper[key]] if increase else [key for key in keys if result[key] > lower[key]]
        if not candidates:
            raise ValueError("integer weight bounds cannot sum to 100")
        candidates.sort(key=lambda key: ((target[key] - result[key]) if increase else (result[key] - target[key]), key), reverse=True)
        result[candidates[0]] += 1 if increase else -1
    return result


class LiveOptimizer:
    def __init__(self, config: LiveConfig) -> None:
        self.config = config

    def _selection_key(self, item: dict[str, Any]) -> str:
        dnn = item.get("dnn", item.get("dnnid"))
        dnn = self.config.dnn.get(str(dnn), str(dnn))
        tac = item.get("tac", item.get("loc", item.get("tacid")))
        try:
            tac = int(tac)
        except (TypeError, ValueError):
            pass
        raw_dscp = str(item.get("dscp", 0)).removeprefix("dscp")
        try:
            dscp: int | str = int(raw_dscp)
        except ValueError:
            dscp = raw_dscp
        return f"tac-{tac}|{dnn}|dscp-{dscp}"

    def _current(self, state: Any) -> dict[str, dict[str, Any]]:
        return {self._selection_key(item): item for item in extract_tuples(state)}

    def solve(
        self,
        forecast: dict[str, Any],
        operational: dict[str, dict[str, Any]],
        smf_state: Any,
        smf_hash: str | None,
        *,
        telemetry_fresh: bool,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        current_by_key = self._current(smf_state)
        smf_to_metric = {mapping.smf: metric for metric, mapping in self.config.mappings.items()}
        grouped: dict[str, dict[str, Any]] = {}
        for row in forecast.get("rows", []):
            key = f"tac-{row['tac']}|{row['dnn']}|dscp-{row['dscp']}"
            group = grouped.setdefault(key, {
                "selection": {"tac": row["tac"], "dnn": row["dnn"], "dscp": row["dscp"]},
                "ul_p95": 0.0, "dl_p95": 0.0,
            })
            if row["horizons"]["ul"] and row["horizons"]["dl"]:
                group["ul_p95"] += float(row["horizons"]["ul"][0]["p95"])
                group["dl_p95"] += float(row["horizons"]["dl"][0]["p95"])

        proposal_rows = []
        solver_statuses = []
        max_slack = 0.0
        for selection_id, demand in sorted(grouped.items()):
            current_item = current_by_key.get(selection_id)
            current_smf = extract_weights(current_item or {})
            if current_item is not None:
                allowed = [smf_to_metric[key] for key in current_smf if key in smf_to_metric]
            else:
                allowed = list(DEFAULT_TAC_ALLOWLIST.get(int(demand["selection"]["tac"]), ()))
            healthy = [upf for upf in allowed if operational.get(upf, {}).get("health") == "healthy"]
            supported = current_item is not None and bool(current_smf) and len(healthy) == len(allowed)
            status, message, raw, projected, slack = self._solve_highs(demand, healthy)
            solver_statuses.append(status)
            max_slack = max(max_slack, slack)
            current_metric = {smf_to_metric[key]: value for key, value in current_smf.items() if key in smf_to_metric}
            current_total = sum(current_metric.values())
            current_norm = ({key: value / current_total for key, value in current_metric.items()}
                            if current_total else {key: 1 / len(healthy) for key in healthy} if healthy else {})
            bounded: dict[str, float] = {}
            if status == "optimal" and raw:
                try:
                    bounded = bounded_weights(raw, current_norm)
                except ValueError as error:
                    status, message = "infeasible", str(error)
            smf_normalized = {self.config.mappings[key].smf: value for key, value in bounded.items()}
            current_100 = integer_weights(current_smf) if current_smf else {}
            proposed_100 = integer_weights(smf_normalized) if smf_normalized else {}
            if proposed_100 and current_100:
                proposed_100 = bounded_integer_weights(proposed_100, current_100)
            deltas = {
                key: proposed_100.get(key, 0) - current_100.get(key, 0)
                for key in sorted(set(proposed_100) | set(current_100))
            }
            outgoing_base = current_item or {**demand["selection"], "weights": {}}
            outgoing = with_weights(outgoing_base, proposed_100) if proposed_100 else None
            row_ready = supported and status == "optimal" and slack <= 1e-7
            proposal_rows.append({
                "selection_id": selection_id,
                "selection": demand["selection"],
                "display_only": not supported,
                "display_only_reason": None if supported else "tuple absent from current SMF state or an allowlisted UPF is unhealthy",
                "eligible_upfs": [self.config.mappings[key].smf for key in allowed],
                "healthy_eligible_upfs": [self.config.mappings[key].smf for key in healthy],
                "current_weights": current_100,
                "proposed_weights": proposed_100,
                "reduced_ratio": reduced_ratio(proposed_100),
                "delta_percentage_points": deltas,
                "projected_utilization": projected,
                "slack": slack,
                "solver_status": status,
                "solver_message": message,
                "actuation_ready": row_ready,
                "outgoing_json": outgoing,
            })
        optimal = bool(proposal_rows) and all(row["solver_status"] == "optimal" for row in proposal_rows)
        gates = {
            "fresh_telemetry": telemetry_fresh,
            "complete_bucket": bool(forecast.get("rows")),
            "successful_forecast": bool(forecast.get("rows")),
            "optimal_solver": optimal,
            "zero_slack": max_slack <= 1e-7,
            "healthy_eligible_upfs": all(
                row["display_only"] or len(row["healthy_eligible_upfs"]) == len(row["eligible_upfs"])
                for row in proposal_rows
            ),
            "conditional_weight_bounds_5_to_75_percent": all(
                all(5 <= value <= 75 for value in row["proposed_weights"].values())
                for row in proposal_rows if len(row["proposed_weights"]) > 1
            ),
            "maximum_change_10_percentage_points": all(
                all(abs(value) <= 10 for value in row["delta_percentage_points"].values())
                for row in proposal_rows if row["current_weights"]
            ),
        }
        actuatable = [row for row in proposal_rows if not row["display_only"]]
        return {
            "proposal_id": f"proposal-{uuid.uuid4().hex}",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "forecast_id": forecast.get("forecast_id"),
            "base_smf_state_hash": smf_hash,
            "unit": self.config.units,
            "calibration": self.config.calibration_status,
            "assumption": "next-window +10 minute p95 carried rate is a cold-start steady-state allocation proxy",
            "optimizer": "scipy.optimize.linprog(method=highs)",
            "solver_status": "optimal" if optimal else next((value for value in solver_statuses if value != "optimal"), "skipped"),
            "solver_runtime_ms": round((time.perf_counter() - started) * 1000),
            "max_slack": max_slack,
            "gates": gates,
            "actuation_ready": bool(actuatable) and all(gates.values()) and all(row["actuation_ready"] for row in actuatable),
            "rows": proposal_rows,
            "warnings": [
                "Uncalibrated pps-proxy limits cannot support production safety claims.",
                "This optimizes carried traffic, not session arrivals; cohort MPC is disabled.",
                "Only tuples returned by GET /upf-admin may be actuated.",
            ],
        }

    def _solve_highs(self, demand: dict[str, Any], upfs: list[str]) -> tuple[str, str, dict[str, float], dict[str, Any], float]:
        if not upfs:
            return "infeasible", "no healthy eligible UPF", {}, {}, 0.0
        try:
            from optimization.highs import OptimizationConfig, solve_allocation
            from schemas import Capacity, Forecast, GroupKey, Quantiles, TimeWindow, UPFState
        except ImportError as error:
            return "error", f"HiGHS dependencies unavailable: {error}", {}, {}, 0.0
        now = datetime.now(timezone.utc)
        selection = demand["selection"]
        group = GroupKey(f"tac-{selection['tac']}", selection["dnn"], f"dscp-{selection['dscp']}")
        target = TimeWindow(now, now + timedelta(minutes=10))
        zero = Quantiles(0, 0, 0)
        forecast = Forecast(
            forecast_id=f"live-opt-{uuid.uuid4().hex}", issued_at=now, source_window_end=now,
            target_window=target, horizon_steps=1, group=group,
            new_session_count=zero,
            new_load_ul_mbps=Quantiles(demand["ul_p95"], demand["ul_p95"], demand["ul_p95"]),
            new_load_dl_mbps=Quantiles(demand["dl_p95"], demand["dl_p95"], demand["dl_p95"]),
            existing_load_by_upf=[], model_version="guarded-synthetic-transfer/live-baselines",
            quality_flags=["pps_proxy", "carried_traffic_cold_start_proxy"],
        )
        states = []
        for upf in upfs:
            mapping = self.config.mappings[upf]
            states.append(UPFState(
                measurement_time=now, upf_id=upf,
                capacity_mbps=Capacity(mapping.ul_limit, mapping.dl_limit),
                safe_utilization=Capacity(1, 1), session_capacity=1, session_safe_utilization=1,
                health="healthy", zone="cdot-live", eligible_groups=[group.selection_id],
                path_latency_ms_by_zone={group.zone: 0}, state_ttl_seconds=self.config.stale_seconds,
                calibration_version="v02-p99-uncalibrated-proxy",
            ))
        result = solve_allocation(
            [forecast], states, created_at=now, policy_version=1,
            config=OptimizationConfig(
                planning_quantile="p95",
                max_group_upf_weight=0.75 if len(upfs) > 1 else 1.0,
            ),
        )
        if result.policy is None:
            return result.status, result.message, {}, {}, math.inf if result.status == "feasible_with_slack" else 0.0
        raw = result.policy.groups[0].weights
        slack_values = list(result.policy.constraint_slack.ul_mbps_by_upf.values()) + list(result.policy.constraint_slack.dl_mbps_by_upf.values())
        slack = max(slack_values, default=0.0)
        projected = {
            upf: {
                "ul": result.projected_ul_mbps_by_upf.get(upf, 0) / self.config.mappings[upf].ul_limit,
                "dl": result.projected_dl_mbps_by_upf.get(upf, 0) / self.config.mappings[upf].dl_limit,
            }
            for upf in upfs
        }
        return result.status, result.message, raw, projected, slack
