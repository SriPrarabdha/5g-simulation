from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from schemas import Capacity, UPFState
from steering import rendezvous_select

from .config import GroupProfile, ScenarioConfig, UPFProfile
from .controllers import StaticCapacityController, normalized_healthy_weights
from .model import Cohort, DirectionResult, StepResult, UPFStepResult


@dataclass(slots=True)
class _UPFRuntime:
    profile: UPFProfile
    health: str = "healthy"
    ul_factor: float = 1.0
    dl_factor: float = 1.0
    ul_queue_bytes: float = 0.0
    dl_queue_bytes: float = 0.0

    @property
    def ul_capacity_mbps(self) -> float:
        return self.profile.capacity_ul_mbps * self.ul_factor if self.health != "unavailable" else 0.0

    @property
    def dl_capacity_mbps(self) -> float:
        return self.profile.capacity_dl_mbps * self.dl_factor if self.health != "unavailable" else 0.0


@dataclass(slots=True)
class SimulationResult:
    scenario_id: str
    seed: int
    step_seconds: int
    controller: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, Any]:
        offered = {"ul": 0.0, "dl": 0.0}
        carried = {"ul": 0.0, "dl": 0.0}
        dropped = {"ul": 0.0, "dl": 0.0}
        failures = 0
        for step in self.steps:
            for upf in step.upfs:
                for direction in ("ul", "dl"):
                    item = getattr(upf, direction)
                    offered[direction] += item.offered_bytes
                    carried[direction] += item.carried_bytes
                    dropped[direction] += item.dropped_bytes
                failures += upf.establishment_failures
            failures += sum(step.group_rejections.values()) - sum(upf.establishment_failures for upf in step.upfs)
        return {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "controller": self.controller,
            "steps": len(self.steps),
            "offered_bytes": offered,
            "carried_bytes": carried,
            "dropped_bytes": dropped,
            "establishment_failures": failures,
        }

    def write_jsonl(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="\n") as stream:
            metadata = {
                "record_type": "simulation_metadata",
                "schema_version": "simulation-metadata/0.1",
                **self.summary,
            }
            stream.write(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
            for step in self.steps:
                stream.write(json.dumps(
                    {"record_type": "simulation_step", "schema_version": "simulation-step/0.1", **step.to_dict()},
                    sort_keys=True, separators=(",", ":"),
                ) + "\n")


class Simulator:
    def __init__(self, config: ScenarioConfig, controller: StaticCapacityController | None = None) -> None:
        self.config = config
        self.controller = controller or StaticCapacityController()
        self._upfs = {profile.upf_id: _UPFRuntime(profile) for profile in config.upfs}
        self._cohorts: list[Cohort] = []
        self._session_sequence: Counter[str] = Counter()
        self._streams = {
            f"arrivals:{group.key.selection_id}": self._random_stream(f"arrivals:{group.key.selection_id}")
            for group in config.groups
        }
        self._streams.update({
            f"lifetimes:{group.key.selection_id}": self._random_stream(f"lifetimes:{group.key.selection_id}")
            for group in config.groups
        })

    def _random_stream(self, name: str) -> random.Random:
        digest = hashlib.sha256(f"{self.config.seed}:{name}".encode()).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    @staticmethod
    def _poisson(mean: float, stream: random.Random) -> int:
        if mean <= 0:
            return 0
        # A sum of independent Poisson variables is Poisson. Chunking keeps the
        # exact Knuth sampler numerically stable for showcase-sized bursts.
        chunks = max(1, math.ceil(mean / 30.0))
        chunk_mean = mean / chunks
        total = 0
        threshold = math.exp(-chunk_mean)
        for _ in range(chunks):
            product = 1.0
            count = 0
            while product > threshold:
                count += 1
                product *= stream.random()
            total += count - 1
        return total

    def _apply_events(self, step: int) -> None:
        for event in self.config.events:
            if event.step != step:
                continue
            runtime = self._upfs[event.upf_id]
            if event.event_type == "health":
                runtime.health = event.health or "unknown"
            else:
                if event.ul_factor is not None:
                    runtime.ul_factor = event.ul_factor
                if event.dl_factor is not None:
                    runtime.dl_factor = event.dl_factor

    def _states(self, measurement_time) -> list[UPFState]:
        eligible_by_upf: dict[str, list[str]] = {upf_id: [] for upf_id in self._upfs}
        for group in self.config.groups:
            for upf_id in group.eligible_upfs:
                eligible_by_upf[upf_id].append(group.key.selection_id)
        return [
            UPFState(
                measurement_time=measurement_time,
                upf_id=upf_id,
                capacity_mbps=Capacity(ul=runtime.profile.capacity_ul_mbps * runtime.ul_factor, dl=runtime.profile.capacity_dl_mbps * runtime.dl_factor),
                safe_utilization=Capacity(
                    ul=runtime.profile.safe_utilization_ul,
                    dl=runtime.profile.safe_utilization_dl,
                ),
                session_capacity=runtime.profile.session_capacity,
                session_safe_utilization=runtime.profile.session_safe_utilization,
                health=runtime.health,
                zone=runtime.profile.zone,
                eligible_groups=eligible_by_upf[upf_id],
                path_latency_ms_by_zone=runtime.profile.path_latency_ms_by_zone,
                state_ttl_seconds=self.config.step_seconds * self.config.decision_interval_steps,
                calibration_version="scenario-config/0.1",
            )
            for upf_id, runtime in self._upfs.items()
        ]

    def _active_counts(self) -> Counter[str]:
        counts: Counter[str] = Counter()
        for cohort in self._cohorts:
            counts[cohort.upf_id] += cohort.count
        return counts

    def run(self) -> SimulationResult:
        result = SimulationResult(
            scenario_id=self.config.scenario_id,
            seed=self.config.seed,
            step_seconds=self.config.step_seconds,
            controller=self.controller.name,
        )
        policy = None
        version = 0
        for step in range(self.config.steps):
            window_start = self.config.start_time + timedelta(seconds=step * self.config.step_seconds)
            window_end = window_start + timedelta(seconds=self.config.step_seconds)
            self._apply_events(step)
            states = self._states(window_start)
            state_by_id = {state.upf_id: state for state in states}
            if policy is None or step % self.config.decision_interval_steps == 0:
                version += 1
                try:
                    policy = self.controller.build_policy(
                        self.config, self.config.groups, states, window_start, version
                    )
                except RuntimeError:
                    policy = None

            active_before = self._active_counts()
            arrivals: dict[str, int] = {}
            rejections: Counter[str] = Counter()
            admitted: Counter[str] = Counter()
            rejected_by_upf: Counter[str] = Counter()
            new_cohorts: Counter[tuple[str, str, int, float, float]] = Counter()

            for group in self.config.groups:
                group_id = group.key.selection_id
                arrival_count = self._poisson(
                    group.arrivals_per_step, self._streams[f"arrivals:{group_id}"]
                )
                arrivals[group_id] = arrival_count
                weights = (
                    normalized_healthy_weights(policy, group.key, group.eligible_upfs, state_by_id)
                    if policy is not None else {}
                )
                for _ in range(arrival_count):
                    sequence = self._session_sequence[group_id]
                    self._session_sequence[group_id] += 1
                    session_key = f"{self.config.scenario_id}:{self.config.seed}:{group_id}:{sequence}"
                    if not weights:
                        rejections[group_id] += 1
                        continue
                    selected, _ = rendezvous_select(session_key, policy.policy_id, weights)
                    runtime = self._upfs[selected]
                    if active_before[selected] + admitted[selected] >= runtime.profile.session_capacity:
                        rejections[group_id] += 1
                        rejected_by_upf[selected] += 1
                        continue
                    lifetime = self._streams[f"lifetimes:{group_id}"].randint(
                        group.lifetime_steps_min, group.lifetime_steps_max
                    )
                    admitted[selected] += 1
                    new_cohorts[(
                        group_id, selected, lifetime,
                        group.offered_ul_mbps_per_session, group.offered_dl_mbps_per_session,
                    )] += 1

            for key, count in new_cohorts.items():
                group_id, upf_id, lifetime, ul_mbps, dl_mbps = key
                self._cohorts.append(Cohort(
                    group_id=group_id,
                    upf_id=upf_id,
                    remaining_steps=lifetime,
                    count=count,
                    ul_mbps_per_session=ul_mbps,
                    dl_mbps_per_session=dl_mbps,
                ))

            offered_ul: Counter[str] = Counter()
            offered_dl: Counter[str] = Counter()
            for cohort in self._cohorts:
                offered_ul[cohort.upf_id] += cohort.count * cohort.ul_mbps_per_session
                offered_dl[cohort.upf_id] += cohort.count * cohort.dl_mbps_per_session

            departing: Counter[str] = Counter()
            for cohort in self._cohorts:
                if cohort.remaining_steps == 1:
                    departing[cohort.upf_id] += cohort.count

            upf_results: list[UPFStepResult] = []
            for upf_id, runtime in self._upfs.items():
                rejected_ul_mbps = 0.0
                rejected_dl_mbps = 0.0
                if rejected_by_upf[upf_id]:
                    # Rejected attempts are attributed using their group profile.
                    for group in self.config.groups:
                        if upf_id in group.eligible_upfs:
                            # Exact group attribution is retained in group_rejections;
                            # this is a conservative aggregate diagnostic.
                            rejected_ul_mbps += rejected_by_upf[upf_id] * group.offered_ul_mbps_per_session
                            rejected_dl_mbps += rejected_by_upf[upf_id] * group.offered_dl_mbps_per_session
                            break
                ul_result, runtime.ul_queue_bytes = self._serve_direction(
                    offered_ul[upf_id], rejected_ul_mbps, runtime.ul_queue_bytes,
                    runtime.ul_capacity_mbps, runtime.profile.safe_utilization_ul,
                    runtime.profile.queue_limit_seconds,
                )
                dl_result, runtime.dl_queue_bytes = self._serve_direction(
                    offered_dl[upf_id], rejected_dl_mbps, runtime.dl_queue_bytes,
                    runtime.dl_capacity_mbps, runtime.profile.safe_utilization_dl,
                    runtime.profile.queue_limit_seconds,
                )
                upf_results.append(UPFStepResult(
                    upf_id=upf_id, health=runtime.health,
                    active_sessions=active_before[upf_id] + admitted[upf_id],
                    new_sessions=admitted[upf_id], departed_sessions=departing[upf_id],
                    establishment_failures=rejected_by_upf[upf_id], ul=ul_result, dl=dl_result,
                ))

            result.steps.append(StepResult(
                scenario_id=self.config.scenario_id, seed=self.config.seed, step=step,
                window_start=window_start, window_end=window_end,
                policy_id=policy.policy_id if policy is not None else "none",
                group_arrivals=arrivals, group_rejections=dict(rejections), upfs=upf_results,
            ))
            survivors: list[Cohort] = []
            for cohort in self._cohorts:
                cohort.remaining_steps -= 1
                if cohort.remaining_steps > 0:
                    survivors.append(cohort)
            self._cohorts = survivors
        return result

    def _serve_direction(
        self,
        admitted_offered_mbps: float,
        rejected_mbps: float,
        queued_bytes: float,
        capacity_mbps: float,
        safe_utilization: float,
        queue_limit_seconds: float,
    ) -> tuple[DirectionResult, float]:
        interval = self.config.step_seconds
        offered_bytes = admitted_offered_mbps * 1_000_000 / 8 * interval
        rejected_bytes = rejected_mbps * 1_000_000 / 8 * interval
        capacity_bytes = capacity_mbps * 1_000_000 / 8 * interval
        available = queued_bytes + offered_bytes
        carried = min(available, capacity_bytes)
        remaining = max(0.0, available - carried)
        queue_limit_bytes = capacity_mbps * 1_000_000 / 8 * queue_limit_seconds
        next_queue = min(remaining, queue_limit_bytes)
        dropped = max(0.0, remaining - next_queue)
        return DirectionResult(
            offered_bytes=offered_bytes + rejected_bytes,
            carried_bytes=carried,
            queued_bytes=next_queue,
            dropped_bytes=dropped,
            rejected_bytes=rejected_bytes,
            capacity_mbps=capacity_mbps,
            safe_capacity_mbps=capacity_mbps * safe_utilization,
        ), next_queue

