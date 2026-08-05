from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from schemas import Capacity, GroupKey, SelectionAudit, TimeWindow, UPFState
from forecasting import DemandObservation, ResidualObservation
from steering import rendezvous_select

from .config import GroupProfile, ScenarioConfig, UPFProfile
from .controllers import ControlContext, Controller, StaticCapacityController, normalized_healthy_weights
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
    primary_overload_metric: str
    steps: list[StepResult] = field(default_factory=list)
    selection_audits: list[SelectionAudit] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, Any]:
        offered = {"ul": 0.0, "dl": 0.0}
        carried = {"ul": 0.0, "dl": 0.0}
        dropped = {"ul": 0.0, "dl": 0.0}
        rejected = {"ul": 0.0, "dl": 0.0}
        new_session_offered = {"ul": 0.0, "dl": 0.0}
        overload_duration = {"ul": 0.0, "dl": 0.0}
        overload_area = {"ul": 0.0, "dl": 0.0}
        failures = 0
        for step in self.steps:
            for upf in step.upfs:
                for direction in ("ul", "dl"):
                    item = getattr(upf, direction)
                    offered[direction] += item.offered_bytes
                    carried[direction] += item.carried_bytes
                    dropped[direction] += item.dropped_bytes
                    rejected[direction] += item.rejected_bytes
                    new_session_offered[direction] += item.new_session_offered_bytes
                    admitted_mbps = (
                        (item.offered_bytes - item.rejected_bytes) * 8
                        / self.step_seconds / 1_000_000
                    )
                    if item.safe_capacity_mbps > 0:
                        excess = max(0.0, admitted_mbps / item.safe_capacity_mbps - 1.0)
                    else:
                        excess = math.inf if admitted_mbps > 0 else 0.0
                    if excess > 0:
                        overload_duration[direction] += self.step_seconds
                        overload_area[direction] += excess * self.step_seconds
                failures += upf.establishment_failures
            rejected["ul"] += step.unplaced_rejected_ul_bytes
            rejected["dl"] += step.unplaced_rejected_dl_bytes
            offered["ul"] += step.unplaced_rejected_ul_bytes
            offered["dl"] += step.unplaced_rejected_dl_bytes
            new_session_offered["ul"] += step.unplaced_rejected_ul_bytes
            new_session_offered["dl"] += step.unplaced_rejected_dl_bytes
            failures += sum(step.group_rejections.values()) - sum(upf.establishment_failures for upf in step.upfs)
        return {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "controller": self.controller,
            "primary_overload_metric": self.primary_overload_metric,
            "steps": len(self.steps),
            "offered_bytes": offered,
            "carried_bytes": carried,
            "dropped_bytes": dropped,
            "rejected_bytes": rejected,
            "new_session_offered_bytes": new_session_offered,
            "controllable_load_fraction": {
                direction: new_session_offered[direction] / offered[direction] if offered[direction] else 0.0
                for direction in ("ul", "dl")
            },
            "overload_duration_seconds": overload_duration,
            "overload_area_seconds": overload_area,
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
            for audit in self.selection_audits:
                stream.write(json.dumps(
                    {"record_type": "selection_audit", **audit.to_dict()},
                    sort_keys=True, separators=(",", ":"),
                ) + "\n")

    def write_parquet(self, path: str | Path) -> None:
        """Write the canonical, typed offline result artifact."""
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as error:
            raise RuntimeError("PyArrow is required for canonical Parquet output") from error

        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        direction = pa.struct([
            ("offered_bytes", pa.float64()),
            ("new_session_offered_bytes", pa.float64()),
            ("carried_bytes", pa.float64()),
            ("queued_bytes", pa.float64()),
            ("dropped_bytes", pa.float64()),
            ("rejected_bytes", pa.float64()),
            ("capacity_mbps", pa.float64()),
            ("safe_capacity_mbps", pa.float64()),
        ])
        upf = pa.struct([
            ("upf_id", pa.string()),
            ("health", pa.string()),
            ("active_sessions", pa.int64()),
            ("new_sessions", pa.int64()),
            ("departed_sessions", pa.int64()),
            ("establishment_failures", pa.int64()),
            ("ul", direction),
            ("dl", direction),
        ])
        group_count = pa.struct([("group_id", pa.string()), ("count", pa.int64())])
        schema = pa.schema([
            ("scenario_id", pa.string()),
            ("seed", pa.int64()),
            ("step", pa.int32()),
            ("window_start", pa.timestamp("us", tz="UTC")),
            ("window_end", pa.timestamp("us", tz="UTC")),
            ("policy_id", pa.string()),
            ("group_arrivals", pa.list_(group_count)),
            ("group_rejections", pa.list_(group_count)),
            ("unplaced_rejected_ul_bytes", pa.float64()),
            ("unplaced_rejected_dl_bytes", pa.float64()),
            ("upfs", pa.list_(upf)),
        ], metadata={
            b"schema_version": b"simulation-step/1.0",
            b"controller": self.controller.encode(),
            b"summary": json.dumps(self.summary, sort_keys=True, separators=(",", ":")).encode(),
        })
        rows: list[dict[str, Any]] = []
        for step in self.steps:
            row = step.to_dict()
            row["window_start"] = step.window_start
            row["window_end"] = step.window_end
            row["group_arrivals"] = [
                {"group_id": key, "count": value}
                for key, value in sorted(step.group_arrivals.items())
            ]
            row["group_rejections"] = [
                {"group_id": key, "count": value}
                for key, value in sorted(step.group_rejections.items())
            ]
            rows.append(row)
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(
            table,
            destination,
            compression="zstd",
            version="2.6",
            write_statistics=True,
        )

    def write_selection_audits_parquet(self, path: str | Path) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as error:
            raise RuntimeError("PyArrow is required for selection-audit Parquet output") from error
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        schema = pa.schema([
            ("schema_version", pa.string()),
            ("timestamp", pa.timestamp("us", tz="UTC")),
            ("session_id_hash", pa.string()),
            ("session_hash_value", pa.string()),
            ("zone", pa.string()),
            ("dnn", pa.string()),
            ("snssai", pa.string()),
            ("eligible_upfs", pa.list_(pa.string())),
            ("requested_weights", pa.map_(pa.string(), pa.float64())),
            ("selected_upf", pa.string()),
            ("policy_id", pa.string()),
            ("reason", pa.string()),
        ], metadata={b"schema_version": b"selection-audit/1.0"})
        rows = [{
            "schema_version": audit.schema_version,
            "timestamp": audit.timestamp,
            "session_id_hash": audit.session_id_hash,
            "session_hash_value": audit.session_hash_value,
            "zone": audit.group.zone,
            "dnn": audit.group.dnn,
            "snssai": audit.group.snssai,
            "eligible_upfs": audit.eligible_upfs,
            "requested_weights": audit.requested_weights,
            "selected_upf": audit.selected_upf,
            "policy_id": audit.policy_id,
            "reason": audit.reason,
        } for audit in self.selection_audits]
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), destination, compression="zstd")


class Simulator:
    def __init__(self, config: ScenarioConfig, controller: Controller | None = None) -> None:
        self.config = config
        self.controller = controller or StaticCapacityController()
        self._upfs = {profile.upf_id: _UPFRuntime(profile) for profile in config.upfs}
        self._cohorts: list[Cohort] = []
        self._session_sequence: Counter[str] = Counter()
        self._history_by_group: dict[str, list[DemandObservation]] = defaultdict(list)
        self._interval_arrivals: Counter[str] = Counter()
        self._arrival_factors = {group.key.selection_id: 1.0 for group in config.groups}
        self._path_latency_overrides: dict[tuple[str, str], float] = {}
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
            if event.event_type == "arrival_factor":
                self._arrival_factors[event.group_id or ""] = event.arrival_factor or 0.0
                continue
            runtime = self._upfs[event.upf_id or ""]
            if event.event_type == "health":
                runtime.health = event.health or "unknown"
            elif event.event_type == "capacity_factor":
                if event.ul_factor is not None:
                    runtime.ul_factor = event.ul_factor
                if event.dl_factor is not None:
                    runtime.dl_factor = event.dl_factor
            else:
                self._path_latency_overrides[(event.upf_id or "", event.zone or "")] = event.latency_ms or 0.0

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
                path_latency_ms_by_zone={
                    zone: self._path_latency_overrides.get((upf_id, zone), latency)
                    for zone, latency in runtime.profile.path_latency_ms_by_zone.items()
                },
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

    def _residual_by_upf(self) -> dict[str, ResidualObservation]:
        sessions: Counter[str] = Counter()
        ul: Counter[str] = Counter()
        dl: Counter[str] = Counter()
        for cohort in self._cohorts:
            sessions[cohort.upf_id] += cohort.count
            ul[cohort.upf_id] += cohort.count * cohort.ul_mbps_per_session
            dl[cohort.upf_id] += cohort.count * cohort.dl_mbps_per_session
        return {
            upf_id: ResidualObservation(sessions[upf_id], ul[upf_id], dl[upf_id])
            for upf_id in self._upfs
        }

    def _close_history_window(self, window_end) -> None:
        duration = timedelta(seconds=self.config.step_seconds * self.config.decision_interval_steps)
        window = TimeWindow(window_end - duration, window_end)
        residual = self._residual_by_upf()
        for group in self.config.groups:
            arrivals = self._interval_arrivals[group.key.selection_id]
            self._history_by_group[group.key.selection_id].append(DemandObservation(
                window=window,
                group=group.key,
                new_session_count=float(arrivals),
                new_ul_mbps=arrivals * group.offered_ul_mbps_per_session,
                new_dl_mbps=arrivals * group.offered_dl_mbps_per_session,
                existing_load_by_upf=residual,
            ))
        self._interval_arrivals.clear()

    def _control_context(self) -> ControlContext:
        return ControlContext(
            history_by_group={
                group_id: tuple(items)
                for group_id, items in self._history_by_group.items()
            },
            residual_by_upf=self._residual_by_upf(),
            oracle_new_by_group=self._peek_interval_arrivals(),
        )

    def _peek_interval_arrivals(self) -> dict[str, ResidualObservation]:
        result: dict[str, ResidualObservation] = {}
        for group in self.config.groups:
            group_id = group.key.selection_id
            clone = random.Random()
            clone.setstate(self._streams[f"arrivals:{group_id}"].getstate())
            count = sum(
                self._poisson(group.arrivals_per_step * self._arrival_factors[group_id], clone)
                for _ in range(self.config.decision_interval_steps)
            )
            result[group_id] = ResidualObservation(
                count,
                count * group.offered_ul_mbps_per_session,
                count * group.offered_dl_mbps_per_session,
            )
        return result

    def run(self) -> SimulationResult:
        result = SimulationResult(
            scenario_id=self.config.scenario_id,
            seed=self.config.seed,
            step_seconds=self.config.step_seconds,
            controller=self.controller.name,
            primary_overload_metric=self.config.primary_overload_metric,
        )
        policy = None
        version = 0
        for step in range(self.config.steps):
            window_start = self.config.start_time + timedelta(seconds=step * self.config.step_seconds)
            window_end = window_start + timedelta(seconds=self.config.step_seconds)
            if step > 0 and step % self.config.decision_interval_steps == 0:
                self._close_history_window(window_start)
            self._apply_events(step)
            states = self._states(window_start)
            state_by_id = {state.upf_id: state for state in states}
            if policy is None or step % self.config.decision_interval_steps == 0:
                version += 1
                try:
                    policy = self.controller.build_policy(
                        self.config, self.config.groups, states, window_start, version,
                        self._control_context(),
                    )
                except RuntimeError:
                    policy = None

            active_before = self._active_counts()
            arrivals: dict[str, int] = {}
            rejections: Counter[str] = Counter()
            admitted: Counter[str] = Counter()
            rejected_by_upf: Counter[str] = Counter()
            rejected_by_upf_group: Counter[tuple[str, str]] = Counter()
            unplaced_rejections: Counter[str] = Counter()
            new_cohorts: Counter[tuple[str, str, int, float, float]] = Counter()

            for group in self.config.groups:
                group_id = group.key.selection_id
                arrival_count = self._poisson(
                    group.arrivals_per_step * self._arrival_factors[group_id],
                    self._streams[f"arrivals:{group_id}"],
                )
                arrivals[group_id] = arrival_count
                self._interval_arrivals[group_id] += arrival_count
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
                        unplaced_rejections[group_id] += 1
                        result.selection_audits.append(SelectionAudit(
                            timestamp=window_start,
                            session_id_hash=hashlib.sha256(session_key.encode()).hexdigest(),
                            session_hash_value=hashlib.sha256(f"{session_key}\x1fno-policy".encode()).hexdigest(),
                            group=GroupKey(group.key.zone, group.key.dnn, group.key.snssai),
                            eligible_upfs=[
                                upf_id for upf_id in group.eligible_upfs
                                if state_by_id[upf_id].health in {"healthy", "degraded"}
                            ],
                            requested_weights={}, selected_upf=None,
                            policy_id=policy.policy_id if policy is not None else None,
                            reason="no_eligible_upf",
                        ))
                        continue
                    selected, hash_value = rendezvous_select(session_key, policy.policy_id, weights)
                    reason = "fallback_static" if policy.fallback.used else "optimizer_weighted"
                    if policy.fallback.source_policy_id:
                        reason = "fallback_last_safe"
                    result.selection_audits.append(SelectionAudit(
                        timestamp=window_start,
                        session_id_hash=hashlib.sha256(session_key.encode()).hexdigest(),
                        session_hash_value=hash_value,
                        group=GroupKey(group.key.zone, group.key.dnn, group.key.snssai),
                        eligible_upfs=sorted(weights),
                        requested_weights=dict(weights), selected_upf=selected,
                        policy_id=policy.policy_id, reason=reason,
                    ))
                    runtime = self._upfs[selected]
                    if active_before[selected] + admitted[selected] >= runtime.profile.session_capacity:
                        rejections[group_id] += 1
                        rejected_by_upf[selected] += 1
                        rejected_by_upf_group[(selected, group_id)] += 1
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
                    arrival_step=step,
                ))

            offered_ul: Counter[str] = Counter()
            offered_dl: Counter[str] = Counter()
            new_offered_ul: Counter[str] = Counter()
            new_offered_dl: Counter[str] = Counter()
            decision_window_start = step - (step % self.config.decision_interval_steps)
            for cohort in self._cohorts:
                offered_ul[cohort.upf_id] += cohort.count * cohort.ul_mbps_per_session
                offered_dl[cohort.upf_id] += cohort.count * cohort.dl_mbps_per_session
                if cohort.arrival_step >= decision_window_start:
                    new_offered_ul[cohort.upf_id] += cohort.count * cohort.ul_mbps_per_session
                    new_offered_dl[cohort.upf_id] += cohort.count * cohort.dl_mbps_per_session

            departing: Counter[str] = Counter()
            for cohort in self._cohorts:
                if cohort.remaining_steps == 1:
                    departing[cohort.upf_id] += cohort.count

            upf_results: list[UPFStepResult] = []
            for upf_id, runtime in self._upfs.items():
                rejected_ul_mbps = 0.0
                rejected_dl_mbps = 0.0
                if rejected_by_upf[upf_id]:
                    for group in self.config.groups:
                        count = rejected_by_upf_group[(upf_id, group.key.selection_id)]
                        rejected_ul_mbps += count * group.offered_ul_mbps_per_session
                        rejected_dl_mbps += count * group.offered_dl_mbps_per_session
                ul_result, runtime.ul_queue_bytes = self._serve_direction(
                    offered_ul[upf_id], new_offered_ul[upf_id], rejected_ul_mbps, runtime.ul_queue_bytes,
                    runtime.ul_capacity_mbps, runtime.profile.safe_utilization_ul,
                    runtime.profile.queue_limit_seconds,
                )
                dl_result, runtime.dl_queue_bytes = self._serve_direction(
                    offered_dl[upf_id], new_offered_dl[upf_id], rejected_dl_mbps, runtime.dl_queue_bytes,
                    runtime.dl_capacity_mbps, runtime.profile.safe_utilization_dl,
                    runtime.profile.queue_limit_seconds,
                )
                upf_results.append(UPFStepResult(
                    upf_id=upf_id, health=runtime.health,
                    active_sessions=active_before[upf_id] + admitted[upf_id],
                    new_sessions=admitted[upf_id], departed_sessions=departing[upf_id],
                    establishment_failures=rejected_by_upf[upf_id], ul=ul_result, dl=dl_result,
                ))

            unplaced_ul_mbps = sum(
                unplaced_rejections[group.key.selection_id] * group.offered_ul_mbps_per_session
                for group in self.config.groups
            )
            unplaced_dl_mbps = sum(
                unplaced_rejections[group.key.selection_id] * group.offered_dl_mbps_per_session
                for group in self.config.groups
            )
            result.steps.append(StepResult(
                scenario_id=self.config.scenario_id, seed=self.config.seed, step=step,
                window_start=window_start, window_end=window_end,
                policy_id=policy.policy_id if policy is not None else "none",
                group_arrivals=arrivals, group_rejections=dict(rejections), upfs=upf_results,
                unplaced_rejected_ul_bytes=unplaced_ul_mbps * 1_000_000 / 8 * self.config.step_seconds,
                unplaced_rejected_dl_bytes=unplaced_dl_mbps * 1_000_000 / 8 * self.config.step_seconds,
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
        new_session_offered_mbps: float,
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
            new_session_offered_bytes=(new_session_offered_mbps + rejected_mbps) * 1_000_000 / 8 * interval,
            carried_bytes=carried,
            queued_bytes=next_queue,
            dropped_bytes=dropped,
            rejected_bytes=rejected_bytes,
            capacity_mbps=capacity_mbps,
            safe_capacity_mbps=capacity_mbps * safe_utilization,
        ), next_queue
