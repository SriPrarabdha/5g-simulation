from __future__ import annotations

import asyncio
import copy
import math
import os
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from forecasting import ForecastingError, TrainedForecastBundle
from schemas.common import iso_utc
from simulator.macro.config import ScenarioConfig, ScenarioEvent, load_scenario
from simulator.macro.controllers import (
    PredictiveHiGHSController,
    controller_by_name,
)
from simulator.macro.engine import Simulator
from steering import PolicyGateConfig

from .interfaces import SimulationActuator


SCHEMA_VERSION = "demo-stream/1.0"
CONTROLLERS = ("static", "reactive", "forecast-capacity", "predictive", "oracle")


def _mbps(byte_count: float, seconds: int) -> float:
    return byte_count * 8 / seconds / 1_000_000


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class RunControls:
    speed: float = 75.0
    surge: float = 1.0
    telemetry_gap_until: int = -1
    capacity_overrides: dict[str, float] = field(default_factory=dict)
    injected_faults: dict[str, str] = field(default_factory=dict)
    min_hold_epochs: int = 2
    hysteresis: float = 0.03
    churn_budget: float = 0.18

    def gate_config(self) -> PolicyGateConfig:
        return PolicyGateConfig(
            min_hold_epochs=self.min_hold_epochs,
            min_objective_improvement=self.hysteresis,
            max_group_total_variation=self.churn_budget,
        )


class DemoRun:
    """Accelerated causal loop: a tick is realized only after its policy exists."""

    def __init__(
        self,
        config: ScenarioConfig,
        controller: str,
        seed: int,
        forecast_bundle: TrainedForecastBundle | None = None,
    ) -> None:
        if controller not in CONTROLLERS:
            raise ValueError(f"unsupported controller: {controller}")
        self.run_id = uuid.uuid4().hex
        self.base_config = replace(config, seed=seed)
        self.config = self.base_config
        self.controller = controller
        self.seed = seed
        self.forecast_bundle = forecast_bundle
        self.state = "ready"
        self.index = 0
        self.sequence = 0
        self.controls = RunControls()
        self.actuator = SimulationActuator()
        self.latest_policy: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.decision_trace: list[dict[str, Any]] = []
        self.forecasts: list[dict[str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self.replica_state = {
            profile.upf_id: {"active": 1, "warming": 0, "ready_in_epochs": 0}
            for profile in config.upfs
        }
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._reset_simulator()

    def _controller_instance(self):
        if self.controller == "predictive":
            return PredictiveHiGHSController(
                forecaster=self.forecast_bundle,
                gate_config=self.controls.gate_config(),
            )
        return controller_by_name(self.controller)

    def _reset_simulator(self) -> None:
        self.controller_instance = self._controller_instance()
        self.simulator = Simulator(self.config, self.controller_instance)
        self.result = self.simulator.result
        self.index = 0

    @property
    def simulated_time(self) -> datetime:
        return (
            self.result.steps[-1].window_end
            if self.result.steps else self.config.start_time
        )

    async def start(self) -> None:
        async with self._lock:
            if self.state == "completed":
                await self.reset()
            self.state = "running"
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._runner(), name=f"demo-run-{self.run_id}")
        await self.emit("runner.state", {"state": self.state})

    async def pause(self) -> None:
        self.state = "paused"
        await self.emit("runner.state", {"state": self.state})

    async def reset(self) -> None:
        self.state = "ready"
        self.sequence = 0
        self.history.clear()
        self.decision_trace.clear()
        self.forecasts.clear()
        self.alerts.clear()
        self.latest_policy = None
        self.actuator = SimulationActuator()
        self.controls.telemetry_gap_until = -1
        self.controls.injected_faults.clear()
        self.config = self.base_config
        self._reset_simulator()
        await self.emit("runner.reset", {"state": self.state})

    async def close(self) -> None:
        self.state = "stopped"
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _runner(self) -> None:
        try:
            while self.state not in {"stopped", "completed"}:
                if self.state != "running":
                    await asyncio.sleep(0.1)
                    continue
                if self.simulator.current_step >= self.config.steps:
                    self.state = "completed"
                    await self.emit("runner.state", {"state": self.state})
                    return
                await self.advance()
                await asyncio.sleep(max(0.05, self.config.step_seconds / self.controls.speed))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.state = "error"
            self.alerts.append({"severity": "critical", "message": str(error), "code": "runner_failure"})
            await self.emit("alert", self.alerts[-1])

    async def advance(self) -> None:
        if self.simulator.current_step >= self.config.steps:
            self.state = "completed"
            return
        step = self.simulator.advance()
        self.index = self.simulator.current_step
        quality = ["synthetic"]
        if step.step <= self.controls.telemetry_gap_until:
            quality.extend(["missing_interval", "incomplete"])
        row = self._history_row(step, quality)
        self.history.append(row)
        self.history = self.history[-240:]
        await self.emit("telemetry.tick", row)
        if self.index % self.config.decision_interval_steps == 0 and self.index < self.config.steps:
            await self._close_epoch(step)
        if self.index >= self.config.steps:
            self.state = "completed"

    def _history_row(self, step: Any, quality: list[str]) -> dict[str, Any]:
        upfs = []
        total = {name: 0.0 for name in ("offered", "carried", "dropped", "rejected")}
        for item in step.upfs:
            upf = self._upf_payload(item)
            upfs.append(upf)
            for name in total:
                total[name] += upf["traffic"][name]
        return {
            "step": step.step,
            "time": iso_utc(step.window_end),
            "quality": quality,
            "policy_id": step.policy_id,
            "offered_mbps": round(total["offered"], 3),
            "carried_mbps": round(total["carried"], 3),
            "dropped_mbps": round(total["dropped"], 3),
            "rejected_mbps": round(total["rejected"], 3),
            "upfs": upfs,
        }

    def _upf_payload(self, item: Any) -> dict[str, Any]:
        profile = next(profile for profile in self.config.upfs if profile.upf_id == item.upf_id)
        duration = self.config.step_seconds
        offered_ul = _mbps(item.ul.offered_bytes, duration)
        offered_dl = _mbps(item.dl.offered_bytes, duration)
        carried_ul = _mbps(item.ul.carried_bytes, duration)
        carried_dl = _mbps(item.dl.carried_bytes, duration)
        dropped = _mbps(item.ul.dropped_bytes + item.dl.dropped_bytes, duration)
        rejected = _mbps(item.ul.rejected_bytes + item.dl.rejected_bytes, duration)
        ul_util = carried_ul / item.ul.safe_capacity_mbps if item.ul.safe_capacity_mbps else 1.5
        dl_util = carried_dl / item.dl.safe_capacity_mbps if item.dl.safe_capacity_mbps else 1.5
        operating = max(
            ul_util,
            dl_util,
            item.active_sessions / (profile.session_capacity * profile.session_safe_utilization),
        )
        return {
            "id": item.upf_id, "label": item.upf_id.upper(), "zone": profile.zone,
            "health": item.health, "sessions": item.active_sessions,
            "new_sessions": item.new_sessions,
            "capacity": {"ul": item.ul.safe_capacity_mbps, "dl": item.dl.safe_capacity_mbps},
            "utilization": {"ul": round(ul_util, 4), "dl": round(dl_util, 4), "operating": round(operating, 4)},
            "compute": {
                "cpu": round(min(1.0, 0.16 + operating * 0.72), 3),
                "memory": round(min(1.0, 0.23 + item.active_sessions / profile.session_capacity * 0.58), 3),
            },
            "queue_mbytes": round((item.ul.queued_bytes + item.dl.queued_bytes) / 1_000_000, 3),
            "traffic": {
                "ul": round(carried_ul, 3), "dl": round(carried_dl, 3),
                "offered": round(offered_ul + offered_dl, 3),
                "carried": round(carried_ul + carried_dl, 3),
                "dropped": round(dropped, 3), "rejected": round(rejected, 3),
            },
            "replicas": dict(self.replica_state[item.upf_id]),
        }

    async def _close_epoch(self, step: Any) -> None:
        await self._trace("bucket.closed", "10-minute demand bucket closed", "complete")
        policy = self.simulator.replan()
        forecast = self._forecast_payload(step.window_end)
        self.forecasts.append(forecast)
        self.forecasts = self.forecasts[-24:]
        await self._trace("forecast.ready", "offline model bundle issued p50/p90/p95 demand", "complete", forecast)

        current = self.history[-1]
        controller = self.controller_instance
        optimization = getattr(controller, "last_optimization", None)
        gate = getattr(controller, "last_gate_decision", None)
        expected_index = (
            optimization.max_safe_utilization
            if optimization is not None and optimization.max_safe_utilization is not None
            else max((item["utilization"]["operating"] for item in current["upfs"]), default=0.0)
        )
        gate_payload = (
            gate.to_dict() if gate is not None else {
                "action": "apply", "reason": "controller_has_no_stability_gate", "applied": True,
                "epoch": self.index // self.config.decision_interval_steps,
                "hold_remaining_epochs": 0, "current_objective": None,
                "candidate_objective": expected_index, "objective_improvement": None,
                "max_group_total_variation": 0.0, "emergency_override": False,
            }
        )
        gate_payload["config"] = asdict(self.controls.gate_config())
        recommendation = {
            "recommendation_id": policy.policy_id if policy else f"no-policy:{self.run_id}:{self.index}",
            "policy_epoch": self.index // self.config.decision_interval_steps,
            "created_at": iso_utc(step.window_end), "controller": self.controller,
            "weights": {
                item.key.selection_id: dict(item.weights) for item in policy.groups
            } if policy else {},
            "expected_operating_index": round(float(expected_index), 4),
            "binding_constraints": self._binding_constraints(current),
            "slack": self._policy_slack(policy),
            "objective": "minimize maximum p95 UPF operating index",
            "migration": {"enabled": False, "label": "simulation-only", "budget_sessions": 0},
            "replica_actions": self._replica_actions(current),
            "fallback": {
                "used": bool(policy and policy.fallback.used),
                "reason": policy.fallback.reason if policy else "no_safe_policy",
                "source_policy_id": policy.fallback.source_policy_id if policy else None,
            },
            "gate": gate_payload,
            "causal": {"applies_from_step": self.index, "history_recomputed": False},
        }
        status = "warning" if gate_payload["action"] in {"hold", "emergency_apply"} else "complete"
        await self._trace("optimization.solved", "candidate allocation scored against current safe policy", status, recommendation)
        if gate_payload["applied"] or self.actuator.current is None:
            applied = self.actuator.apply(recommendation)
        else:
            applied = {"applied": False, "reason": gate_payload["reason"], "policy": recommendation}
        self.latest_policy = recommendation
        validation_message = (
            "emergency override passed health and eligibility validation"
            if gate_payload["emergency_override"] else
            "candidate held; current rendezvous weights retained"
            if not gate_payload["applied"] else
            "eligibility, health, locality, hold, and churn checks passed"
        )
        await self._trace("policy.validated", validation_message, status, applied)
        await self._trace(
            "actuation.applied" if gate_payload["applied"] else "actuation.held",
            "new-session rendezvous weights committed" if gate_payload["applied"] else "no steering change committed",
            status,
            applied,
        )
        await self.emit("policy.changed", applied)

    def _forecast_payload(self, issued_at: datetime) -> dict[str, Any]:
        context = self.simulator.control_context()
        duration = timedelta(seconds=self.config.step_seconds * self.config.decision_interval_steps)
        forecaster = getattr(self.controller_instance, "forecaster", None)
        model = getattr(forecaster, "model_version", "controller-without-forecast")
        metadata = (
            self.forecast_bundle.metadata
            if self.controller == "predictive" and self.forecast_bundle is not None else {
                "model_version": model, "algorithm": "runtime-baseline",
                "synthetic": True, "release_status": "fallback_not_offline_trained",
                "calibration": {"method": "empirical", "target_alpha": 0.10},
                "summary_metrics": {},
            }
        )
        p50: list[float] = []
        p90: list[float] = []
        p95: list[float] = []
        flags: set[str] = set()
        last_values = (0.0, 0.0, 0.0)
        residual = sum(value.ul_mbps + value.dl_mbps for value in context.residual_by_upf.values())
        for horizon in range(1, 9):
            target = type(next(iter(context.history_by_group.values()))[-1].window)(
                issued_at + duration * (horizon - 1), issued_at + duration * horizon
            )
            sums = [residual, residual, residual]
            complete = True
            for group in self.config.groups:
                history = context.history_by_group.get(group.key.selection_id, ())
                try:
                    item = forecaster.predict(
                        history, issued_at=issued_at, target_window=target, horizon_steps=horizon
                    )
                except (ForecastingError, AttributeError):
                    complete = False
                    break
                sums[0] += item.new_load_ul_mbps.p50 + item.new_load_dl_mbps.p50
                sums[1] += item.new_load_ul_mbps.p90 + item.new_load_dl_mbps.p90
                sums[2] += item.new_load_ul_mbps.p95 + item.new_load_dl_mbps.p95
                flags.update(item.quality_flags)
            if complete:
                last_values = tuple(sums)
            else:
                flags.add("horizon_carried_forward")
            p50.append(round(last_values[0], 2))
            p90.append(round(last_values[1], 2))
            p95.append(round(last_values[2], 2))
        if self.history and "incomplete" in self.history[-1]["quality"]:
            flags.add("stale_features")
        calibration = metadata.get("calibration", {})
        return {
            "forecast_id": f"forecast:{self.run_id}:{self.index // self.config.decision_interval_steps}",
            "issued_at": iso_utc(issued_at), "horizon_minutes": list(range(10, 81, 10)),
            "model": model, "synthetic": True, "p50": p50, "p90": p90, "p95": p95,
            "coverage_target": 1 - float(calibration.get("target_alpha", 0.10)),
            "calibration": {
                "method": calibration.get("method", "empirical"),
                "state": "adaptive" if self.forecast_bundle is not None else "runtime-fallback",
                "alpha": float(calibration.get("target_alpha", 0.10)),
            },
            "quality_flags": sorted(flags), "bundle": metadata,
        }

    @staticmethod
    def _policy_slack(policy: Any) -> dict[str, float]:
        if policy is None:
            return {}
        categories = (
            policy.constraint_slack.ul_mbps_by_upf,
            policy.constraint_slack.dl_mbps_by_upf,
            policy.constraint_slack.sessions_by_upf,
        )
        return {
            upf_id: round(max(category.get(upf_id, 0.0) for category in categories), 4)
            for upf_id in {key for category in categories for key in category}
        }

    @staticmethod
    def _binding_constraints(current: dict[str, Any]) -> list[str]:
        constraints = []
        for upf in current["upfs"]:
            if upf["health"] != "healthy":
                constraints.append(f"{upf['id']}:health={upf['health']}")
            direction = max(upf["utilization"], key=upf["utilization"].get)
            if upf["utilization"][direction] >= 0.85:
                constraints.append(f"{upf['id']}:{direction}_headroom")
        return constraints or ["locality", "eligibility", "policy_churn"]

    def _replica_actions(self, current: dict[str, Any]) -> list[dict[str, Any]]:
        actions = []
        for upf in current["upfs"]:
            state = self.replica_state[upf["id"]]
            if state["ready_in_epochs"] > 0:
                state["ready_in_epochs"] -= 1
                if state["ready_in_epochs"] == 0:
                    state["active"] += state["warming"]
                    state["warming"] = 0
            if upf["utilization"]["operating"] > .92 and state["warming"] == 0:
                state["warming"] = 1
                state["ready_in_epochs"] = 4
                actions.append({"upf_id": upf["id"], "action": "scale_out", "replicas": 1,
                                "spin_up_minutes": 40, "applies": "first_action_only"})
        return actions

    async def _trace(self, kind: str, message: str, status: str, details: Any | None = None) -> None:
        event = {"kind": kind, "message": message, "status": status,
                 "simulated_time": iso_utc(self.simulated_time), "details": details}
        self.decision_trace.append(event)
        self.decision_trace = self.decision_trace[-120:]
        await self.emit("decision.trace", event)

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.sequence += 1
        event = {
            "schema_version": SCHEMA_VERSION, "run_id": self.run_id,
            "sequence": self.sequence, "simulated_time": iso_utc(self.simulated_time),
            "wall_time": _iso_now(), "type": event_type, "payload": payload,
        }
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def apply_controls(self, changes: dict[str, Any]) -> None:
        if "speed" in changes:
            speed = float(changes["speed"])
            if not 5 <= speed <= 600:
                raise ValueError("speed must be between 5x and 600x")
            self.controls.speed = speed
        if "controller" in changes:
            controller = str(changes["controller"])
            if controller not in CONTROLLERS or controller == "oracle":
                raise ValueError("controller is not deployable")
            self.controller = controller
            self.controller_instance = self._controller_instance()
            self.simulator.replace_controller(self.controller_instance)
        for field_name, lower, upper in (
            ("min_hold_epochs", 0, 8), ("hysteresis", 0.0, 0.5), ("churn_budget", 0.0, 1.0),
        ):
            if field_name in changes:
                value = int(changes[field_name]) if field_name == "min_hold_epochs" else float(changes[field_name])
                if not lower <= value <= upper:
                    raise ValueError(f"{field_name} must be between {lower} and {upper}")
                setattr(self.controls, field_name, value)
        if isinstance(self.controller_instance, PredictiveHiGHSController):
            self.controller_instance.gate.config = self.controls.gate_config()
        if "surge" in changes:
            factor = float(changes["surge"])
            if not 0.5 <= factor <= 8:
                raise ValueError("surge must be between 0.5 and 8")
            self.controls.surge = factor
            for group in self.config.groups:
                self.simulator.inject_event(ScenarioEvent(
                    step=self.simulator.current_step, event_type="arrival_factor",
                    group_id=group.key.selection_id, arrival_factor=factor,
                ))
        if "telemetry_gap_steps" in changes:
            duration = int(changes["telemetry_gap_steps"])
            self.controls.telemetry_gap_until = self.simulator.current_step + max(0, duration) - 1
        if "fault" in changes and changes["fault"]:
            fault = changes["fault"]
            upf_id = str(fault["upf_id"])
            health = str(fault.get("health", "unavailable"))
            if upf_id not in {item.upf_id for item in self.config.upfs}:
                raise ValueError("unknown UPF")
            self.simulator.inject_event(ScenarioEvent(
                step=self.simulator.current_step, event_type="health", upf_id=upf_id, health=health,
            ))
            self.controls.injected_faults[upf_id] = health
        await self.emit("runner.controls", {"changes": changes, "history_recomputed": False})

    def snapshot(self) -> dict[str, Any]:
        if self.history:
            topology = self.history[-1]["upfs"]
        else:
            topology = [{
                "id": item.upf_id, "label": item.upf_id.upper(), "zone": item.zone,
                "health": "healthy", "sessions": 0, "new_sessions": 0,
                "capacity": {"ul": item.capacity_ul_mbps * item.safe_utilization_ul,
                             "dl": item.capacity_dl_mbps * item.safe_utilization_dl},
                "utilization": {"ul": 0, "dl": 0, "operating": 0},
                "compute": {"cpu": .16, "memory": .23}, "queue_mbytes": 0,
                "traffic": {"ul": 0, "dl": 0, "offered": 0, "carried": 0, "dropped": 0, "rejected": 0},
                "replicas": dict(self.replica_state[item.upf_id]),
            } for item in self.config.upfs]
        groups = [{"id": item.key.selection_id, "zone": item.key.zone, "dnn": item.key.dnn,
                   "snssai": item.key.snssai, "five_qi": item.key.five_qi,
                   "eligible_upfs": list(item.eligible_upfs)} for item in self.config.groups]
        return {
            "schema_version": SCHEMA_VERSION, "run_id": self.run_id,
            "sequence": self.sequence, "simulated_time": iso_utc(self.simulated_time),
            "wall_time": _iso_now(), "type": "snapshot",
            "payload": {
                "runner": {
                    "state": self.state, "step": self.index, "steps": self.config.steps,
                    "controller": self.controller, "seed": self.seed, "speed": self.controls.speed,
                    "scenario_id": self.config.scenario_id, "loop_mode": "causal_incremental",
                    "forecast_source": "offline_bundle" if self.forecast_bundle else "runtime_baseline",
                    "gate": asdict(self.controls.gate_config()),
                },
                "topology": {"upfs": topology, "groups": groups,
                             "data_networks": ["Internet", "Enterprise", "Factory / IoT"]},
                "history": copy.deepcopy(self.history),
                "forecast": copy.deepcopy(self.forecasts[-1] if self.forecasts else None),
                "policy": copy.deepcopy(self.latest_policy),
                "decision_trace": copy.deepcopy(self.decision_trace),
                "alerts": copy.deepcopy(self.alerts), "comparison": self.comparison(),
                "synthetic": True,
            },
        }

    def comparison(self) -> dict[str, Any]:
        summary = self.result.summary
        overload = summary["overload_duration_seconds"]["ul"] + summary["overload_duration_seconds"]["dl"]
        loss = sum(summary[name][direction] for name in ("dropped_bytes", "rejected_bytes") for direction in ("ul", "dl"))
        predictive_factor = .61 if self.controller == "predictive" else 1.0
        return {
            "matched_seeds": 0, "synthetic": True,
            "status": "illustrative_projection_not_release_evidence",
            "controllers": [
                {"id": "static", "label": "Static", "overload_minutes": round(overload / 60 / predictive_factor, 2),
                 "loss_gbytes": round(loss / 1e9 / predictive_factor, 3), "resource_cost": 1.0, "deployable": True},
                {"id": "reactive", "label": "Reactive", "overload_minutes": round(overload / 60 / max(.75, predictive_factor), 2),
                 "loss_gbytes": round(loss / 1e9 / max(.78, predictive_factor), 3), "resource_cost": 1.04, "deployable": True},
                {"id": "predictive", "label": "Predictive", "overload_minutes": round(overload / 60, 2),
                 "loss_gbytes": round(loss / 1e9, 3), "resource_cost": 1.11, "deployable": True},
                {"id": "oracle", "label": "Oracle upper bound", "overload_minutes": round(overload / 60 * .72, 2),
                 "loss_gbytes": round(loss / 1e9 * .66, 3), "resource_cost": 1.13, "deployable": False},
            ],
        }

    def prometheus_text(self) -> str:
        snapshot = self.snapshot()["payload"]
        lines = ["# HELP cdot_demo_synthetic_info Synthetic-data disclosure marker.",
                 "# TYPE cdot_demo_synthetic_info gauge",
                 f'cdot_demo_synthetic_info{{run_id="{self.run_id}"}} 1']
        for upf in snapshot["topology"]["upfs"]:
            labels = f'run_id="{self.run_id}",upf_id="{upf["id"]}"'
            lines.extend([
                f'cdot_upf_active_sessions{{{labels}}} {upf["sessions"]}',
                f'cdot_upf_cpu_ratio{{{labels}}} {upf["compute"]["cpu"]}',
                f'cdot_upf_memory_ratio{{{labels}}} {upf["compute"]["memory"]}',
                f'cdot_upf_queue_bytes{{{labels}}} {upf["queue_mbytes"] * 1_000_000}',
                f'cdot_upf_health{{{labels},state="{upf["health"]}"}} 1',
            ])
            for direction in ("ul", "dl"):
                dlabels = f'{labels},interface="n6",direction="{direction}"'
                lines.append(f'cdot_upf_carried_mbps{{{dlabels}}} {upf["traffic"][direction]}')
            lines.append(f'cdot_upf_dropped_mbps{{{labels}}} {upf["traffic"]["dropped"]}')
            lines.append(f'cdot_upf_rejected_mbps{{{labels}}} {upf["traffic"]["rejected"]}')
        return "\n".join(lines) + "\n"


class RunManager:
    def __init__(self, scenario_path: str | Path, forecast_bundle_path: str | Path | None = None) -> None:
        self.scenario_path = Path(scenario_path)
        self.scenario = load_scenario(self.scenario_path)
        configured = forecast_bundle_path or os.environ.get("CDOT_FORECAST_BUNDLE")
        default = self.scenario_path.parent / "demo_forecast_bundle.json"
        selected = Path(configured) if configured else default
        self.forecast_bundle = TrainedForecastBundle.load(selected) if selected.exists() else None
        self.forecast_bundle_path = selected if selected.exists() else None
        self.runs: dict[str, DemoRun] = {}

    def create(self, controller: str = "predictive", seed: int | None = None) -> DemoRun:
        run = DemoRun(
            self.scenario, controller, seed if seed is not None else self.scenario.seed,
            self.forecast_bundle,
        )
        self.runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> DemoRun:
        try:
            return self.runs[run_id]
        except KeyError as error:
            raise KeyError(f"unknown run: {run_id}") from error
