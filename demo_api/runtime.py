from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from forecasting import ForecastingError, MovingAverageForecaster, TrainedForecastBundle
from optimization import CohortMPCConfig
from schemas.common import iso_utc
from simulator.macro.config import ScenarioConfig, ScenarioEvent, load_scenario
from simulator.macro.controllers import (
    CohortMPCController,
    ForecastAdjustmentConfig,
    PredictiveHiGHSController,
    controller_by_name,
)
from simulator.macro.engine import Simulator
from steering import PolicyGateConfig

from .interfaces import SimulationActuator
from .story import STORY_CHECKPOINTS, build_story_playlist


SCHEMA_VERSION = "demo-stream/1.1"
CONTROLLERS = ("static", "reactive", "forecast-capacity", "predictive", "mpc", "oracle")


def _mbps(byte_count: float, seconds: int) -> float:
    return byte_count * 8 / seconds / 1_000_000


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class RunControls:
    speed: float = field(default_factory=lambda: float(os.environ.get("CDOT_STORY_SPEED", "8")))
    surge: float = 1.0
    telemetry_gap_until: int = -1
    capacity_overrides: dict[str, float] = field(default_factory=dict)
    injected_faults: dict[str, str] = field(default_factory=dict)
    min_hold_epochs: int = 2
    hysteresis: float = 0.03
    churn_budget: float = 0.18
    pause_at_step: int | None = None

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
        mpc_config: CohortMPCConfig | None = None,
        mpc_profile: dict[str, Any] | None = None,
        campaign_evidence: dict[str, Any] | None = None,
        story_mode: bool = False,
    ) -> None:
        if controller not in CONTROLLERS:
            raise ValueError(f"unsupported controller: {controller}")
        self.run_id = uuid.uuid4().hex
        seeded_config = replace(config, seed=seed)
        self.story_mode = story_mode
        if story_mode:
            seeded_config, self.story_episodes = build_story_playlist(seeded_config, seed)
        else:
            self.story_episodes = []
        self.base_config = seeded_config
        self.config = self.base_config
        self.controller = controller
        self.seed = seed
        self.forecast_bundle = forecast_bundle
        self.mpc_config = mpc_config or CohortMPCConfig()
        self.mpc_profile = copy.deepcopy(mpc_profile)
        self.campaign_evidence = copy.deepcopy(campaign_evidence)
        self.state = "ready"
        self.index = 0
        self.sequence = 0
        self.controls = RunControls()
        self.actuator = SimulationActuator()
        self.latest_policy: dict[str, Any] | None = None
        self.routing_presentation: dict[str, Any] | None = None
        self.paused_at_step: int | None = None
        self.history: list[dict[str, Any]] = []
        self.decision_trace: list[dict[str, Any]] = []
        self.forecasts: list[dict[str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self.decision_cycles: list[dict[str, Any]] = []
        self.replica_state = {
            profile.upf_id: {"active": 1, "warming": 0, "ready_in_epochs": 0}
            for profile in config.upfs
        }
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._story_checkpoints: dict[str, dict[str, Any]] = {}
        self._reset_simulator()
        if self.story_mode:
            self._capture_checkpoint()

    def _controller_instance(self):
        if self.controller == "mpc":
            history_windows = int(
                (self.mpc_profile or {}).get("forecaster", {}).get("history_windows", 6)
            )
            return CohortMPCController(
                forecaster=MovingAverageForecaster(history_windows),
                mpc_config=self.mpc_config,
                forecast_adjustment=ForecastAdjustmentConfig(
                    scheduled_event_hints_enabled=self.story_mode,
                    anomaly_fallback_enabled=self.story_mode,
                    anomaly_history_windows=6,
                    anomaly_ratio_threshold=1.45,
                    anomaly_multiplier_cap=2.4,
                ),
            )
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
                self._reset_locked()
            self.state = "running"
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._runner(), name=f"demo-run-{self.run_id}")
        await self.emit("runner.state", {"state": self.state})

    async def pause(self) -> None:
        async with self._lock:
            self.state = "paused"
        await self.emit("runner.state", {"state": self.state})

    async def reset(self) -> None:
        async with self._lock:
            self._reset_locked()
        await self.emit("runner.reset", {"state": self.state})

    def _reset_locked(self) -> None:
        self.state = "ready"
        self.history.clear()
        self.decision_trace.clear()
        self.forecasts.clear()
        self.alerts.clear()
        self.decision_cycles.clear()
        self.latest_policy = None
        self.routing_presentation = None
        self.paused_at_step = None
        self.actuator = SimulationActuator()
        self.controls.telemetry_gap_until = -1
        self.controls.pause_at_step = None
        self.controls.injected_faults.clear()
        self.config = self.base_config
        self._reset_simulator()
        self._story_checkpoints.clear()
        if self.story_mode:
            self._capture_checkpoint()

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
        async with self._lock:
            await self._advance_locked()

    async def _advance_locked(self) -> None:
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
        if self.story_mode and self.index % self.config.decision_interval_steps == 0:
            self._resolve_story_cycle(self.index)
        if self.index % self.config.decision_interval_steps == 0 and self.index < self.config.steps:
            await self._close_epoch(step)
        if self.story_mode and self.index in {20, 40, 60}:
            self._capture_checkpoint()
        if self.index >= self.config.steps:
            self.state = "completed"
            self.paused_at_step = self.index
            self.controls.pause_at_step = None
            if self.story_mode:
                self._capture_checkpoint()
            await self.emit("story.checkpoint", {"step": self.index, "state": self.state})
        elif self.controls.pause_at_step is not None and self.index >= self.controls.pause_at_step:
            self.state = "paused"
            self.paused_at_step = self.index
            self.controls.pause_at_step = None
            await self.emit("guided.checkpoint", {"step": self.index, "state": self.state})

    def _history_row(self, step: Any, quality: list[str]) -> dict[str, Any]:
        upfs = []
        total = {name: 0.0 for name in ("offered", "carried", "dropped", "rejected")}
        for item in step.upfs:
            upf = self._upf_payload(item)
            upfs.append(upf)
            for name in total:
                total[name] += upf["traffic"][name]
        group_rates = {
            group.key.selection_id: (
                group.offered_ul_mbps_per_session + group.offered_dl_mbps_per_session
            )
            for group in self.config.groups
        }
        return {
            "step": step.step,
            "time": iso_utc(step.window_end),
            "quality": quality,
            "policy_id": step.policy_id,
            "offered_mbps": round(total["offered"], 3),
            "carried_mbps": round(total["carried"], 3),
            "dropped_mbps": round(total["dropped"], 3),
            "rejected_mbps": round(total["rejected"], 3),
            "class_arrivals": copy.deepcopy(step.group_arrivals),
            "class_rejections": {
                group_id: step.group_rejections.get(group_id, 0)
                for group_id in group_rates
            },
            "class_arrival_mbps": {
                group_id: round(count * group_rates[group_id], 3)
                for group_id, count in step.group_arrivals.items()
            },
            "new_session_routing": copy.deepcopy(step.group_upf_admissions),
            "new_session_routing_mbps": {
                group_id: {
                    upf_id: round(count * group_rates[group_id], 3)
                    for upf_id, count in by_upf.items()
                }
                for group_id, by_upf in step.group_upf_admissions.items()
            },
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
                "source": "derived_synthetic_proxy",
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
        previous_active_weights = copy.deepcopy(
            (self.actuator.current or {}).get("weights", {})
        )
        await self._trace("bucket.closed", "10-minute demand bucket closed", "complete")
        policy = self.simulator.replan()
        forecast = self._forecast_payload(step.window_end)
        self.forecasts.append(forecast)
        self.forecasts = self.forecasts[-24:]
        forecast_message = (
            "causal MA6 forecast issued for the cohort-state horizon"
            if self.controller == "mpc"
            else "offline model bundle issued p50/p90/p95 demand"
        )
        await self._trace("forecast.ready", forecast_message, "complete", forecast)

        current = self.history[-1]
        controller = self.controller_instance
        optimization = getattr(controller, "last_optimization", None)
        gate = getattr(controller, "last_gate_decision", None)
        mpc_result = getattr(controller, "last_result", None)
        certificate = mpc_result.certificate if mpc_result is not None else None
        static_weights = copy.deepcopy(
            mpc_result.static_first_allocation if mpc_result is not None else {}
        )
        candidate_weights = copy.deepcopy(
            mpc_result.first_allocation if mpc_result is not None else {}
        )
        if not previous_active_weights:
            previous_active_weights = copy.deepcopy(static_weights)
        expected_index = (
            optimization.max_safe_utilization
            if optimization is not None and optimization.max_safe_utilization is not None
            else certificate.candidate.terminal_max_safe_utilization
            if certificate is not None
            else max((item["utilization"]["operating"] for item in current["upfs"]), default=0.0)
        )
        if self.controller == "mpc":
            accepted = bool(policy and not policy.fallback.used and certificate and certificate.accepted)
            gate_payload = {
                "action": "apply" if accepted else "hold",
                "reason": certificate.reason if certificate is not None else policy.fallback.reason,
                "applied": accepted,
                "epoch": self.index // self.config.decision_interval_steps,
                "hold_remaining_epochs": 0,
                "current_objective": certificate.static.score if certificate is not None else None,
                "candidate_objective": certificate.candidate.score if certificate is not None else expected_index,
                "objective_improvement": certificate.relative_improvement if certificate is not None else None,
                "max_group_total_variation": 0.0,
                "emergency_override": False,
            }
        else:
            gate_payload = (
            gate.to_dict() if gate is not None else {
                "action": "apply", "reason": "controller_has_no_stability_gate", "applied": True,
                "epoch": self.index // self.config.decision_interval_steps,
                "hold_remaining_epochs": 0, "current_objective": None,
                "candidate_objective": expected_index, "objective_improvement": None,
                "max_group_total_variation": 0.0, "emergency_override": False,
            }
            )
        story_episode = self._episode_for_window(self.index, self.index + self.config.decision_interval_steps)
        if self.story_mode and story_episode and story_episode["surprise"]:
            # The surprise is intentionally unavailable to the forecast at this
            # boundary. Retaining the last safe policy is the honest headline.
            gate_payload.update({
                "action": "hold",
                "reason": "unannounced_surprise_no_causal_signal",
                "applied": False,
            })
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
            "objective": (
                "minimize horizon overload and drops with terminal cohort exposure"
                if self.controller == "mpc"
                else "minimize maximum p95 UPF operating index"
            ),
            "migration": {"enabled": False, "label": "simulation-only", "budget_sessions": 0},
            "replica_actions": self._replica_actions(current),
            "fallback": {
                "used": bool(policy and policy.fallback.used),
                "reason": policy.fallback.reason if policy else "no_safe_policy",
                "source_policy_id": policy.fallback.source_policy_id if policy else None,
            },
            "gate": gate_payload,
            "certificate": {
                "accepted": certificate.accepted,
                "reason": certificate.reason,
                "relative_improvement": certificate.relative_improvement,
                "ul_overload_relative_improvement": certificate.ul_overload_relative_improvement,
                "known_future_events": mpc_result.known_future_events,
            } if certificate is not None and mpc_result is not None else None,
            "causal": {"applies_from_step": self.index, "history_recomputed": False},
        }
        self.routing_presentation = {
            "previous_active_weights": previous_active_weights,
            "static_first_allocation": static_weights,
            "certified_candidate_weights": candidate_weights or copy.deepcopy(recommendation["weights"]),
            "deltas": self._weight_deltas(
                previous_active_weights,
                candidate_weights or recommendation["weights"],
            ),
            "certificate": self._certificate_payload(certificate),
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
        if self.story_mode and story_episode is not None:
            self._create_story_cycle(
                story_episode,
                forecast=self._story_forecast(story_episode),
                recommendation=recommendation,
                previous_weights=previous_active_weights,
                applied_policy=applied,
            )
        await self.emit("policy.changed", applied)

    def _episode_for_window(self, start_step: int, end_step: int) -> dict[str, Any] | None:
        return next((
            episode for episode in self.story_episodes
            if episode["target_window_start_step"] == start_step
            and episode["target_window_end_step"] == end_step
        ), None)

    def _story_forecast(self, episode: dict[str, Any]) -> dict[str, Any]:
        group_id = episode["group_id"]
        history = self.simulator.control_context().history_by_group.get(group_id, ())
        values = [item.new_ul_mbps + item.new_dl_mbps for item in history[-6:]]
        baseline = sum(values) / len(values) if values else 0.0
        flags = ["causal_ma6", "new_session_demand"]
        if episode["scheduled"]:
            active_fraction = (episode["end_step"] - episode["start_step"]) / self.config.decision_interval_steps
            multiplier = 1.0 + (episode["magnitude"] - 1.0) * active_fraction
            p50 = baseline * multiplier
            flags.append("scheduled_event_knowledge")
        else:
            p50 = baseline
            flags.extend(("no_advance_signal", "surprise_risk_unseen"))
        if len(values) >= 2:
            prior = values[:-1]
            prior_mean = sum(prior) / len(prior) if prior else 0.0
            if values[-1] / max(1.0, prior_mean) >= 1.45:
                p50 = max(p50, values[-1] * .72)
                flags.append("surprise_anomaly_adaptation")
        if any(
            cycle["episode"]["surprise"]
            and cycle["status"] == "resolved"
            and cycle["target_window"]["end_step"] == self.index
            for cycle in self.decision_cycles
        ):
            flags.append("surprise_anomaly_adaptation")
        return {
            "p50_mbps": round(p50, 3),
            "p90_mbps": round(p50 * (1.22 if episode["scheduled"] else 1.15), 3),
            "p95_mbps": round(p50 * (1.32 if episode["scheduled"] else 1.22), 3),
            "quality_flags": flags,
            "source_window_end_step": self.index,
            "causal": True,
        }

    def _create_story_cycle(
        self,
        episode: dict[str, Any],
        *,
        forecast: dict[str, Any],
        recommendation: dict[str, Any],
        previous_weights: dict[str, dict[str, float]],
        applied_policy: dict[str, Any],
    ) -> None:
        group_id = episode["group_id"]
        planned_weights = copy.deepcopy(
            ((self.actuator.current or {}).get("weights", {})).get(group_id, {})
        )
        candidate_weights = copy.deepcopy(recommendation["weights"].get(group_id, {}))
        previous = copy.deepcopy(previous_weights.get(group_id, {}))
        certificate = self.routing_presentation.get("certificate") if self.routing_presentation else None
        gate = recommendation["gate"]
        group = next(item for item in self.config.groups if item.key.selection_id == group_id)
        latest_upfs = {
            item["id"]: item for item in (self.history[-1]["upfs"] if self.history else [])
        }
        upf_context: dict[str, dict[str, Any]] = {}
        for profile in self.config.upfs:
            upf_id = profile.upf_id
            eligible = upf_id in group.eligible_upfs
            delta = candidate_weights.get(upf_id, 0.0) - previous.get(upf_id, 0.0)
            observed = latest_upfs.get(upf_id, {})
            if not eligible:
                explanation = "Not eligible for this traffic class"
            elif episode.get("constrained_upf") == upf_id:
                explanation = "Known capacity reduction is included in the horizon"
            elif not gate.get("applied"):
                explanation = "Candidate not committed; last safe weight stays active"
            elif delta > 1e-6:
                explanation = "Receives a larger share of future sessions"
            elif delta < -1e-6:
                explanation = "Receives a smaller share of future sessions"
            else:
                explanation = "Future-session share is unchanged"
            operating = observed.get("utilization", {}).get("operating")
            upf_context[upf_id] = {
                "eligible": eligible,
                "observed_operating_index": operating,
                "observed_headroom_fraction": (
                    round(max(0.0, 1.0 - operating), 4) if operating is not None else None
                ),
                "scheduled_capacity_reduction": episode.get("constrained_upf") == upf_id,
                "explanation": explanation,
            }
        cycle = {
            "id": f"cycle-{episode['order']}",
            "order": episode["order"],
            "status": "active",
            "episode": copy.deepcopy(episode),
            "target_window": {
                "start_step": episode["target_window_start_step"],
                "end_step": episode["target_window_end_step"],
                "start_time": iso_utc(self.config.start_time + timedelta(seconds=episode["target_window_start_step"] * self.config.step_seconds)),
                "end_time": iso_utc(self.config.start_time + timedelta(seconds=episode["target_window_end_step"] * self.config.step_seconds)),
            },
            "forecast": forecast,
            "optimization": {
                "static_score": certificate["static"]["score"] if certificate else None,
                "optimized_score": certificate["mpc"]["score"] if certificate else None,
                "candidate_accepted": bool(certificate and certificate["accepted"]),
                "relative_improvement": certificate["relative_improvement"] if certificate else None,
            },
            "decision": {
                "action": gate["action"],
                "applied": bool(applied_policy.get("applied")),
                "reason": gate["reason"],
                "policy_id": recommendation["recommendation_id"],
                "scope": "new_session_placement_only",
                "previous_weights": previous,
                "candidate_weights": candidate_weights,
                "weight_deltas": {
                    upf_id: round(candidate_weights.get(upf_id, 0.0) - previous.get(upf_id, 0.0), 8)
                    for upf_id in sorted(set(previous) | set(candidate_weights))
                },
                "eligible_upfs": list(group.eligible_upfs),
                "upf_context": upf_context,
            },
            "planned_admitted_share_by_upf": planned_weights,
            "outcome": None,
        }
        self.decision_cycles.append(cycle)
        self.routing_presentation["active_group_id"] = group_id
        self.routing_presentation["active_episode_id"] = episode["id"]
        self.routing_presentation["realized_admitted_share_by_upf"] = None

    def _resolve_story_cycle(self, end_step: int) -> None:
        cycle = next((
            item for item in reversed(self.decision_cycles)
            if item["status"] == "active" and item["target_window"]["end_step"] == end_step
        ), None)
        if cycle is None:
            return
        start_step = cycle["target_window"]["start_step"]
        group_id = cycle["episode"]["group_id"]
        group = next(item for item in self.config.groups if item.key.selection_id == group_id)
        steps = self.result.steps[start_step:end_step]
        arrivals = sum(item.group_arrivals.get(group_id, 0) for item in steps)
        actual = arrivals * (group.offered_ul_mbps_per_session + group.offered_dl_mbps_per_session)
        canonical = next((item for item in reversed(steps) if item.group_upf_buckets), None)
        admitted = {
            upf_id: 0 for upf_id in sorted(profile.upf_id for profile in self.config.upfs)
        }
        rejected_by_upf = dict(admitted)
        for item in (canonical.group_upf_buckets if canonical else ()):
            if item.group_id != group_id:
                continue
            admitted[item.upf_id] += item.admitted_sessions
            rejected_by_upf[item.upf_id] += item.establishment_failures
        admitted_total = sum(admitted.values())
        realized_share = {
            upf_id: round(count / admitted_total, 6) if admitted_total else 0.0
            for upf_id, count in sorted(admitted.items())
        }
        forecast = cycle["forecast"]
        covered_p90 = actual <= forecast["p90_mbps"]
        covered_p95 = actual <= forecast["p95_mbps"]
        above_p50 = (actual - forecast["p50_mbps"]) / max(forecast["p50_mbps"], 1e-9)
        rows = self.history[start_step:end_step]
        realized_utilization = {
            upf_id: round(max((
                next(upf for upf in row["upfs"] if upf["id"] == upf_id)["utilization"]["operating"]
                for row in rows
            ), default=0.0), 4)
            for upf_id in sorted(self.replica_state)
        }
        cycle["status"] = "resolved"
        cycle["outcome"] = {
            "realized_new_session_demand_mbps": round(actual, 3),
            "forecast_error_mbps": round(actual - forecast["p50_mbps"], 3),
            "forecast_error_fraction": round(above_p50, 6),
            "covered_p90": covered_p90,
            "covered_p95": covered_p95,
            "accuracy_statement": (
                f"Actual landed inside p90; {abs(above_p50) * 100:.1f}% {'above' if above_p50 >= 0 else 'below'} p50"
                if covered_p90 else
                f"Actual missed p90; {above_p50 * 100:.1f}% above p50"
            ),
            "realized_admitted_share_by_upf": realized_share,
            "realized_admitted_sessions_by_upf": admitted,
            "realized_new_session_mbps_by_upf": {
                upf_id: round(count * (
                    group.offered_ul_mbps_per_session + group.offered_dl_mbps_per_session
                ), 3)
                for upf_id, count in admitted.items()
            },
            "realized_rejected_sessions_by_upf": rejected_by_upf,
            "realized_utilization_by_upf": realized_utilization,
            "dropped_mbps": round(sum(row["dropped_mbps"] for row in rows), 3),
            "rejected_mbps": round(sum(row["rejected_mbps"] for row in rows), 3),
            "rejected_sessions": sum(item.group_rejections.get(group_id, 0) for item in steps),
            "measurement_window_seconds": len(steps) * self.config.step_seconds,
            "source": "canonical_group_upf_buckets",
        }
        if self.routing_presentation and self.routing_presentation.get("active_episode_id") == cycle["episode"]["id"]:
            self.routing_presentation["realized_admitted_share_by_upf"] = realized_share

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
        horizon_count = self.mpc_config.horizon_windows if self.controller == "mpc" else 8
        for horizon in range(1, horizon_count + 1):
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
            "issued_at": iso_utc(issued_at),
            "horizon_minutes": list(range(10, horizon_count * 10 + 1, 10)),
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
        async with self._lock:
            await self._apply_controls_locked(changes)

    async def _apply_controls_locked(self, changes: dict[str, Any]) -> None:
        if "speed" in changes:
            speed = float(changes["speed"])
            if not 1 <= speed <= 600:
                raise ValueError("speed must be between 1x and 600x")
            self.controls.speed = speed
        if "controller" in changes:
            controller = str(changes["controller"])
            if controller not in CONTROLLERS or controller == "oracle":
                raise ValueError("controller is not deployable")
            self.controller = controller
            self.controller_instance = self._controller_instance()
            self.simulator.replace_controller(self.controller_instance)
        if "pause_at_step" in changes:
            pause_at_step = int(changes["pause_at_step"])
            if not self.index < pause_at_step <= self.config.steps:
                raise ValueError("pause_at_step must be after the current step and within the run")
            self.controls.pause_at_step = pause_at_step
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

    def _checkpoint_bundle(self) -> dict[str, Any]:
        simulator = copy.deepcopy(self.simulator)
        return {
            "step": self.index,
            "simulator": simulator,
            "controls": copy.deepcopy(self.controls),
            "actuator": copy.deepcopy(self.actuator),
            "latest_policy": copy.deepcopy(self.latest_policy),
            "routing_presentation": copy.deepcopy(self.routing_presentation),
            "paused_at_step": self.paused_at_step,
            "history": copy.deepcopy(self.history),
            "decision_trace": copy.deepcopy(self.decision_trace),
            "forecasts": copy.deepcopy(self.forecasts),
            "decision_cycles": copy.deepcopy(self.decision_cycles),
            "alerts": copy.deepcopy(self.alerts),
            "replica_state": copy.deepcopy(self.replica_state),
        }

    def _capture_checkpoint(self) -> None:
        checkpoint = next((item for item in STORY_CHECKPOINTS if item[2] == self.index), None)
        if checkpoint is not None:
            self._story_checkpoints[checkpoint[0]] = self._checkpoint_bundle()

    async def rewind(self, checkpoint_id: str, *, autoplay: bool = True) -> None:
        async with self._lock:
            if checkpoint_id not in {item[0] for item in STORY_CHECKPOINTS}:
                raise ValueError("unknown story checkpoint")
            if checkpoint_id not in self._story_checkpoints:
                raise ValueError("story checkpoint has not been reached")
            bundle = copy.deepcopy(self._story_checkpoints[checkpoint_id])
            preserved_sequence = self.sequence
            self.simulator = bundle["simulator"]
            self.result = self.simulator.result
            self.controller_instance = self.simulator.controller
            self.index = bundle["step"]
            self.controls = bundle["controls"]
            self.actuator = bundle["actuator"]
            self.latest_policy = bundle["latest_policy"]
            self.routing_presentation = bundle["routing_presentation"]
            self.paused_at_step = bundle["paused_at_step"]
            self.history = bundle["history"]
            self.decision_trace = bundle["decision_trace"]
            self.forecasts = bundle["forecasts"]
            self.decision_cycles = bundle["decision_cycles"]
            self.alerts = bundle["alerts"]
            self.replica_state = bundle["replica_state"]
            self.sequence = preserved_sequence
            target_step = self.index
            self._story_checkpoints = {
                item_id: stored
                for item_id, stored in self._story_checkpoints.items()
                if stored["step"] <= target_step
            }
            self.state = "running" if autoplay else "paused"
            self.controls.pause_at_step = None
            self.paused_at_step = None if autoplay else self.index
            if autoplay and (self._task is None or self._task.done()):
                self._task = asyncio.create_task(self._runner(), name=f"demo-run-{self.run_id}")
            await self.emit("story.rewound", {
                "checkpoint_id": checkpoint_id,
                "step": self.index,
                "autoplay": autoplay,
                "state": self.state,
            })

    @staticmethod
    def _weight_deltas(
        previous: dict[str, dict[str, float]],
        candidate: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, float]]:
        deltas: dict[str, dict[str, float]] = {}
        for group_id in sorted(set(previous) | set(candidate)):
            group_delta = {
                upf_id: round(
                    candidate.get(group_id, {}).get(upf_id, 0.0)
                    - previous.get(group_id, {}).get(upf_id, 0.0),
                    8,
                )
                for upf_id in sorted(
                    set(previous.get(group_id, {})) | set(candidate.get(group_id, {}))
                )
            }
            deltas[group_id] = group_delta
        return deltas

    @staticmethod
    def _certificate_payload(certificate: Any | None) -> dict[str, Any] | None:
        if certificate is None:
            return None

        def metrics(item: Any) -> dict[str, Any]:
            return {
                "ul_overload_area_seconds": item.overload_area_seconds["ul"],
                "dl_overload_area_seconds": item.overload_area_seconds["dl"],
                "ul_dropped_bytes": item.dropped_bytes["ul"],
                "dl_dropped_bytes": item.dropped_bytes["dl"],
                "terminal_max_safe_utilization": item.terminal_max_safe_utilization,
                "score": item.score,
            }

        return {
            "static": metrics(certificate.static),
            "mpc": metrics(certificate.candidate),
            "relative_improvement": certificate.relative_improvement,
            "ul_overload_relative_improvement": certificate.ul_overload_relative_improvement,
            "reason": certificate.reason,
            "accepted": certificate.accepted,
        }

    def _guided_story(self) -> dict[str, Any]:
        checkpoints = [{
            "id": checkpoint_id,
            "number": number,
            "step": step,
            "title": title,
            "reached": checkpoint_id in self._story_checkpoints,
            "current": step == max(item[2] for item in STORY_CHECKPOINTS if item[2] <= self.index),
            "rewind_available": checkpoint_id in self._story_checkpoints and step < self.index,
        } for number, (checkpoint_id, title, step) in enumerate(STORY_CHECKPOINTS, 1)]
        current_index = max(
            index for index, checkpoint in enumerate(checkpoints)
            if self.index >= checkpoint["step"]
        )
        current = dict(checkpoints[current_index])
        current["paused"] = self.state == "paused" and self.paused_at_step == self.index
        return {
            "current_chapter": current,
            "current_checkpoint": current if current["reached"] else None,
            "next_checkpoint": (
                dict(checkpoints[current_index + 1])
                if current_index + 1 < len(checkpoints) else None
            ),
            "checkpoints": checkpoints,
            "presenter_paced": False,
            "autoplay": True,
        }

    def _story_payload(self) -> dict[str, Any]:
        guided = self._guided_story()
        active = next((item for item in reversed(self.decision_cycles) if item["status"] == "active"), None)
        if active is None and self.decision_cycles:
            active = self.decision_cycles[-1]
        next_decision_step = (
            min(self.config.steps, ((self.index // self.config.decision_interval_steps) + 1) * self.config.decision_interval_steps)
            if self.index < self.config.steps else self.config.steps
        )
        return {
            "episodes": copy.deepcopy(self.story_episodes),
            "checkpoints": copy.deepcopy(guided["checkpoints"]),
            "current_checkpoint": copy.deepcopy(guided["current_chapter"]),
            "active_cycle_id": active["id"] if active else None,
            "next_decision_step": next_decision_step,
            "elapsed_simulated_seconds": self.index * self.config.step_seconds,
            "duration_simulated_seconds": self.config.steps * self.config.step_seconds,
            "default_speed": 8,
        }

    def _audience_states(self) -> list[dict[str, str]]:
        labels = {
            "forecast.ready": "Forecast ready",
            "optimization.solved": "Static comparison passed",
            "actuation.applied": "New-session policy applied",
            "actuation.held": "Safe policy held",
        }
        return [
            {"kind": item["kind"], "label": labels[item["kind"]], "status": item["status"]}
            for item in self.decision_trace
            if item["kind"] in labels
        ][-8:]

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
                   "eligible_upfs": list(item.eligible_upfs),
                   "base_arrivals_per_step": item.arrivals_per_step,
                   "offered_mbps_per_session": {
                       "ul": item.offered_ul_mbps_per_session,
                       "dl": item.offered_dl_mbps_per_session,
                   },
                   "lifetime_steps": {
                       "min": item.lifetime_steps_min,
                       "max": item.lifetime_steps_max,
                   }} for item in self.config.groups]
        return {
            "schema_version": SCHEMA_VERSION, "run_id": self.run_id,
            "sequence": self.sequence, "simulated_time": iso_utc(self.simulated_time),
            "wall_time": _iso_now(), "type": "snapshot",
            "payload": {
                "runner": {
                    "state": self.state, "step": self.index, "steps": self.config.steps,
                    "step_seconds": self.config.step_seconds,
                    "decision_interval_steps": self.config.decision_interval_steps,
                    "controller": self.controller, "seed": self.seed, "speed": self.controls.speed,
                    "scenario_id": self.config.scenario_id, "loop_mode": "causal_incremental",
                    "forecast_source": (
                        "causal_moving_average_6"
                        if self.controller == "mpc"
                        else "offline_bundle" if self.forecast_bundle else "runtime_baseline"
                    ),
                    "controller_profile": (
                        (self.mpc_profile or {}).get("profile_id")
                        if self.controller == "mpc" else None
                    ),
                    "gate": asdict(self.controls.gate_config()),
                    "pause_at_step": self.controls.pause_at_step,
                    "paused_at_step": self.paused_at_step,
                },
                "topology": {"upfs": topology, "groups": groups,
                             "data_networks": ["Internet", "Enterprise", "Factory / IoT"]},
                "scenario": {
                    "name": "Seeded four-event network simulation",
                    "summary": (
                        "A deterministic 50-minute synthetic replay with three scheduled "
                        "traffic episodes and one unannounced surprise."
                    ),
                    "duration_minutes": self.config.steps * self.config.step_seconds / 60,
                    "events": [asdict(event) for event in self.config.events],
                },
                "history": copy.deepcopy(self.history),
                "forecast": copy.deepcopy(self.forecasts[-1] if self.forecasts else None),
                "policy": copy.deepcopy(self.latest_policy),
                "routing": copy.deepcopy(self.routing_presentation),
                "guided_story": self._guided_story(),
                "story": self._story_payload(),
                "decision_cycles": copy.deepcopy(self.decision_cycles),
                "active_cycle": copy.deepcopy(next((
                    item for item in reversed(self.decision_cycles) if item["status"] == "active"
                ), self.decision_cycles[-1] if self.decision_cycles else None)),
                "audience_states": self._audience_states(),
                "control_scope": "new_session_placement_only",
                "session_migration_supported": False,
                "decision_trace": copy.deepcopy(self.decision_trace),
                "alerts": copy.deepcopy(self.alerts), "comparison": self.comparison(),
                "synthetic": True,
            },
        }

    def comparison(self) -> dict[str, Any]:
        if self.campaign_evidence is not None:
            evidence = self.campaign_evidence
            totals = evidence["totals"]
            controllers = []
            for controller, label in (("static", "Static"), ("mpc", "Cohort MPC")):
                controller_totals = totals[controller]
                controllers.append({
                    "id": controller,
                    "label": label,
                    "overload_minutes": round(
                        controller_totals["overload_area_seconds"]["ul"] / 60, 2
                    ),
                    "loss_gbytes": round(
                        sum(controller_totals["dropped_bytes"].values()) / 1e9, 3
                    ),
                    "resource_cost": 1.0,
                    "deployable": True,
                })
            return {
                "campaign_id": evidence["campaign_id"],
                "created_at": evidence["created_at"],
                "simulated_days_per_pair": evidence["simulated_days_per_pair"],
                "matched_seeds": evidence["paired_runs"],
                "synthetic": True,
                "status": evidence["release_status"],
                "primary_metric": evidence["primary_metric"],
                "mean_pair_relative_reduction": evidence["mean_pair_relative_reduction"],
                "bootstrap_95_interval": evidence["bootstrap_95_interval"],
                "weighted_total_relative_reduction": evidence["weighted_total_relative_reduction"],
                "worst_pair_relative_reduction": evidence["worst_pair_relative_reduction"],
                "aggregate_guardrails": copy.deepcopy(evidence["aggregate_guardrails"]),
                "by_scenario": copy.deepcopy(evidence["by_scenario"]),
                "artifact_sha256": evidence["source_artifact"]["sha256"],
                "controllers": controllers,
            }
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
    def __init__(
        self,
        scenario_path: str | Path,
        forecast_bundle_path: str | Path | None = None,
        mpc_profile_path: str | Path | None = None,
        campaign_evidence_path: str | Path | None = None,
    ) -> None:
        self.scenario_path = Path(scenario_path)
        self.scenario = load_scenario(self.scenario_path)
        configured = forecast_bundle_path or os.environ.get("CDOT_FORECAST_BUNDLE")
        default = self.scenario_path.parent / "demo_forecast_bundle.json"
        selected = Path(configured) if configured else default
        self.forecast_bundle = TrainedForecastBundle.load(selected) if selected.exists() else None
        if self.forecast_bundle is not None:
            self.forecast_bundle.validate_groups(group.key for group in self.scenario.groups)
        self.forecast_bundle_path = selected if selected.exists() else None
        profile_path = Path(mpc_profile_path) if mpc_profile_path else self.scenario_path.parent / "cohort_mpc_pilot_10pct_v2.json"
        self.mpc_profile = json.loads(profile_path.read_text(encoding="utf-8"))
        if self.mpc_profile.get("schema_version") != "cohort-mpc-profile/1.0":
            raise ValueError("unsupported cohort MPC demo profile")
        if self.mpc_profile.get("forecaster", {}).get("type") != "moving_average":
            raise ValueError("the demo MPC profile must use a moving-average forecaster")
        self.mpc_config = CohortMPCConfig(**self.mpc_profile.get("mpc", {}))
        self.mpc_profile_path = profile_path
        evidence_path = (
            Path(campaign_evidence_path)
            if campaign_evidence_path
            else Path(__file__).resolve().parent / "data" / "cohort_mpc_full_campaign_evidence_v1.json"
        )
        self.campaign_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if self.campaign_evidence.get("schema_version") != "demo-campaign-evidence/1.0":
            raise ValueError("unsupported demo campaign evidence")
        self.campaign_evidence_path = evidence_path
        self.runs: dict[str, DemoRun] = {}

    def create(self, controller: str = "mpc", seed: int | None = None) -> DemoRun:
        run = DemoRun(
            self.scenario, controller, seed if seed is not None else self.scenario.seed,
            self.forecast_bundle,
            self.mpc_config,
            self.mpc_profile,
            self.campaign_evidence,
            story_mode=True,
        )
        self.runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> DemoRun:
        try:
            return self.runs[run_id]
        except KeyError as error:
            raise KeyError(f"unknown run: {run_id}") from error
