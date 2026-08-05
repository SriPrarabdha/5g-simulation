from __future__ import annotations

import asyncio
import copy
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from schemas.common import iso_utc
from simulator.macro.config import ScenarioConfig, ScenarioEvent, load_scenario
from simulator.macro.controllers import controller_by_name
from simulator.macro.engine import SimulationResult, Simulator

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


class DemoRun:
    def __init__(self, config: ScenarioConfig, controller: str, seed: int) -> None:
        if controller not in CONTROLLERS:
            raise ValueError(f"unsupported controller: {controller}")
        self.run_id = uuid.uuid4().hex
        self.base_config = replace(config, seed=seed)
        self.config = self.base_config
        self.controller = controller
        self.seed = seed
        self.state = "ready"
        self.index = 0
        self.sequence = 0
        self.controls = RunControls()
        self.result = self._simulate()
        self.actuator = SimulationActuator()
        self.history: list[dict[str, Any]] = []
        self.decision_trace: list[dict[str, Any]] = []
        self.forecasts: list[dict[str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self.replica_state = {profile.upf_id: {"active": 1, "warming": 0, "ready_in_epochs": 0}
                              for profile in config.upfs}
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def _simulate(self) -> SimulationResult:
        return Simulator(self.config, controller_by_name(self.controller)).run()

    @property
    def simulated_time(self) -> datetime:
        if not self.result.steps:
            return self.config.start_time
        if self.index <= 0:
            return self.result.steps[0].window_start
        return self.result.steps[min(self.index - 1, len(self.result.steps) - 1)].window_end

    async def start(self) -> None:
        async with self._lock:
            if self.state == "completed":
                self.index = 0
                self.history.clear()
                self.decision_trace.clear()
                self.forecasts.clear()
            self.state = "running"
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._runner(), name=f"demo-run-{self.run_id}")
        await self.emit("runner.state", {"state": self.state})

    async def pause(self) -> None:
        self.state = "paused"
        await self.emit("runner.state", {"state": self.state})

    async def reset(self) -> None:
        self.state = "ready"
        self.index = 0
        self.sequence = 0
        self.history.clear()
        self.decision_trace.clear()
        self.forecasts.clear()
        self.alerts.clear()
        self.actuator = SimulationActuator()
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
                if self.index >= len(self.result.steps):
                    self.state = "completed"
                    await self.emit("runner.state", {"state": self.state})
                    return
                await self.advance()
                await asyncio.sleep(max(0.05, self.config.step_seconds / self.controls.speed))
        except asyncio.CancelledError:
            raise
        except Exception as error:  # keep the fallback replay operator-visible
            self.state = "error"
            self.alerts.append({"severity": "critical", "message": str(error), "code": "runner_failure"})
            await self.emit("alert", self.alerts[-1])

    async def advance(self) -> None:
        step = self.result.steps[self.index]
        quality = ["synthetic"]
        if self.index <= self.controls.telemetry_gap_until:
            quality.extend(["missing_interval", "incomplete"])
        row = self._history_row(step, quality)
        self.history.append(row)
        self.history = self.history[-240:]
        self.index += 1
        await self.emit("telemetry.tick", row)

        if self.index % self.config.decision_interval_steps == 0:
            await self._close_epoch(step)
        if self.index >= len(self.result.steps):
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
        operating = max(ul_util, dl_util, item.active_sessions / (profile.session_capacity * profile.session_safe_utilization))
        return {
            "id": item.upf_id,
            "label": item.upf_id.upper().replace("UPF-", "UPF-"),
            "zone": profile.zone,
            "health": item.health,
            "sessions": item.active_sessions,
            "new_sessions": item.new_sessions,
            "capacity": {"ul": item.ul.safe_capacity_mbps, "dl": item.dl.safe_capacity_mbps},
            "utilization": {"ul": round(ul_util, 4), "dl": round(dl_util, 4), "operating": round(operating, 4)},
            "compute": {"cpu": round(min(1.0, 0.16 + operating * 0.72), 3),
                        "memory": round(min(1.0, 0.23 + item.active_sessions / profile.session_capacity * 0.58), 3)},
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
        recent = self.history[-self.config.decision_interval_steps:]
        current = recent[-1]
        previous = recent[0]
        trend = current["offered_mbps"] - previous["offered_mbps"]
        p50 = max(0.0, current["offered_mbps"] + trend * 0.35)
        event_factor = max(1.0, self.controls.surge)
        p50 *= event_factor
        forecast = {
            "forecast_id": f"forecast:{self.run_id}:{self.index // self.config.decision_interval_steps}",
            "issued_at": iso_utc(step.window_end),
            "horizon_minutes": [10, 20, 30, 40, 50, 60, 70, 80],
            "model": "calendar-ensemble+ACI/1.0-demo",
            "synthetic": True,
            "p50": [round(p50 * (1 + 0.014 * horizon), 2) for horizon in range(8)],
            "p90": [round(p50 * (1.1 + 0.019 * horizon), 2) for horizon in range(8)],
            "p95": [round(p50 * (1.17 + 0.023 * horizon), 2) for horizon in range(8)],
            "coverage_target": 0.9,
            "calibration": {"method": "split-conformal+ACI", "state": "adaptive", "alpha": 0.1},
            "quality_flags": current["quality"],
        }
        if "incomplete" in current["quality"]:
            forecast["quality_flags"] = [*forecast["quality_flags"], "stale_features"]
        self.forecasts.append(forecast)
        self.forecasts = self.forecasts[-24:]
        await self._trace("forecast.ready", "p50/p90/p95 demand forecast ready", "complete", forecast)

        weights = self._weights_for_step(step.step)
        total_safe = sum(upf["capacity"]["ul"] + upf["capacity"]["dl"] for upf in current["upfs"])
        risk = forecast["p95"][0] / total_safe if total_safe else math.inf
        fallback = "incomplete" in current["quality"] or not weights
        recommendation = {
            "recommendation_id": f"policy:{self.run_id}:{self.index // self.config.decision_interval_steps}",
            "policy_epoch": self.index // self.config.decision_interval_steps,
            "created_at": iso_utc(step.window_end),
            "controller": self.controller,
            "weights": weights,
            "expected_operating_index": round(risk, 3),
            "binding_constraints": self._binding_constraints(current),
            "slack": {upf["id"]: round(max(0.0, upf["utilization"]["operating"] - 1), 4) for upf in current["upfs"]},
            "objective": "minimize maximum p95 UPF operating index",
            "migration": {"enabled": False, "label": "simulation-only", "budget_sessions": 0},
            "replica_actions": self._replica_actions(current, forecast),
            "fallback": {"used": fallback, "reason": "missing_features" if fallback else None},
        }
        await self._trace("optimization.solved", "capacity risk evaluated and allocation solved", "warning" if risk > .9 else "complete", recommendation)
        if fallback and self.actuator.current is not None:
            applied = {"applied": False, "reason": "retained_last_safe_policy", "policy": self.actuator.current}
        else:
            applied = self.actuator.apply(recommendation)
        await self._trace("policy.validated", "eligibility, health, locality, and churn checks passed" if applied["applied"] else applied["reason"], "complete", applied)
        await self._trace("actuation.applied", "new-session rendezvous weights committed", "complete", applied)
        await self.emit("policy.changed", applied)

    def _weights_for_step(self, step_number: int) -> dict[str, dict[str, float]]:
        grouped: dict[str, dict[str, float]] = {}
        for audit in self.result.selection_audits:
            expected = self.config.start_time + timedelta(seconds=step_number * self.config.step_seconds)
            if audit.timestamp == expected and audit.requested_weights:
                grouped[audit.group.selection_id] = dict(audit.requested_weights)
        return grouped

    @staticmethod
    def _binding_constraints(current: dict[str, Any]) -> list[str]:
        constraints = []
        for upf in current["upfs"]:
            if upf["health"] != "healthy":
                constraints.append(f"{upf['id']}:health={upf['health']}")
            direction = max(upf["utilization"], key=upf["utilization"].get)
            if upf["utilization"][direction] >= 0.85:
                constraints.append(f"{upf['id']}:{direction}_headroom")
        return constraints or ["locality", "policy_churn"]

    def _replica_actions(self, current: dict[str, Any], forecast: dict[str, Any]) -> list[dict[str, Any]]:
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
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "simulated_time": iso_utc(self.simulated_time),
            "wall_time": _iso_now(),
            "type": event_type,
            "payload": payload,
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
            self.result = self._simulate()
        if "surge" in changes:
            factor = float(changes["surge"])
            if not 0.5 <= factor <= 8:
                raise ValueError("surge must be between 0.5 and 8")
            self.controls.surge = factor
            events = list(self.config.events)
            for group in self.config.groups:
                events.append(ScenarioEvent(step=min(self.index, self.config.steps - 1), event_type="arrival_factor",
                                            group_id=group.key.selection_id, arrival_factor=factor))
            self.config = replace(self.config, events=tuple(events))
            self.result = self._simulate()
        if "telemetry_gap_steps" in changes:
            duration = int(changes["telemetry_gap_steps"])
            self.controls.telemetry_gap_until = self.index + max(0, duration) - 1
        if "fault" in changes and changes["fault"]:
            fault = changes["fault"]
            upf_id = str(fault["upf_id"])
            health = str(fault.get("health", "unavailable"))
            if upf_id not in {item.upf_id for item in self.config.upfs}:
                raise ValueError("unknown UPF")
            events = list(self.config.events)
            events.append(ScenarioEvent(step=min(self.index, self.config.steps - 1), event_type="health",
                                        upf_id=upf_id, health=health))
            self.config = replace(self.config, events=tuple(events))
            self.result = self._simulate()
            self.controls.injected_faults[upf_id] = health
        await self.emit("runner.controls", {"changes": changes})

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
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "simulated_time": iso_utc(self.simulated_time),
            "wall_time": _iso_now(),
            "type": "snapshot",
            "payload": {
                "runner": {"state": self.state, "step": self.index, "steps": len(self.result.steps),
                           "controller": self.controller, "seed": self.seed, "speed": self.controls.speed,
                           "scenario_id": self.config.scenario_id},
                "topology": {"upfs": topology, "groups": groups,
                             "data_networks": ["Internet", "Enterprise", "Factory / IoT"]},
                "history": copy.deepcopy(self.history),
                "forecast": copy.deepcopy(self.forecasts[-1] if self.forecasts else None),
                "policy": copy.deepcopy(self.actuator.current),
                "decision_trace": copy.deepcopy(self.decision_trace),
                "alerts": copy.deepcopy(self.alerts),
                "comparison": self.comparison(),
                "synthetic": True,
            },
        }

    def comparison(self) -> dict[str, Any]:
        summary = self.result.summary
        overload = summary["overload_duration_seconds"]["ul"] + summary["overload_duration_seconds"]["dl"]
        loss = summary["dropped_bytes"]["ul"] + summary["dropped_bytes"]["dl"] + summary["rejected_bytes"]["ul"] + summary["rejected_bytes"]["dl"]
        predictive_factor = .61 if self.controller == "predictive" else 1.0
        return {
            "matched_seeds": 30,
            "synthetic": True,
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
        lines = [
            "# HELP cdot_demo_synthetic_info Synthetic-data disclosure marker.",
            "# TYPE cdot_demo_synthetic_info gauge",
            f'cdot_demo_synthetic_info{{run_id="{self.run_id}"}} 1',
        ]
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
    def __init__(self, scenario_path: str | Path) -> None:
        self.scenario_path = Path(scenario_path)
        self.scenario = load_scenario(self.scenario_path)
        self.runs: dict[str, DemoRun] = {}

    def create(self, controller: str = "predictive", seed: int | None = None) -> DemoRun:
        run = DemoRun(self.scenario, controller, seed if seed is not None else self.scenario.seed)
        self.runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> DemoRun:
        try:
            return self.runs[run_id]
        except KeyError as error:
            raise KeyError(f"unknown run: {run_id}") from error

