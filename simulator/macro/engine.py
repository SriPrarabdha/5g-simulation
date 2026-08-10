from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from schemas import Capacity, GroupKey, SelectionAudit, TimeWindow, UPFState
from forecasting import DemandObservation, ResidualObservation
from steering import rendezvous_select

from .config import GroupProfile, ScenarioConfig, UPFProfile
from .controllers import ControlContext, Controller, StaticCapacityController
from .model import DirectionResult, GroupUPFBucketResult, StepResult, UPFStepResult


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
    selection_audit_stride: int = 1
    steps: list[StepResult] = field(default_factory=list)
    selection_audits: list[SelectionAudit] = field(default_factory=list)
    _summary_cache: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _summary_cache_steps: int = field(default=-1, init=False, repr=False)

    @property
    def summary(self) -> dict[str, Any]:
        if self._summary_cache is not None and self._summary_cache_steps == len(self.steps):
            return self._summary_cache
        offered = {"ul": 0.0, "dl": 0.0}
        carried = {"ul": 0.0, "dl": 0.0}
        dropped = {"ul": 0.0, "dl": 0.0}
        rejected = {"ul": 0.0, "dl": 0.0}
        new_session_offered = {"ul": 0.0, "dl": 0.0}
        overload_duration = {"ul": 0.0, "dl": 0.0}
        overload_area = {"ul": 0.0, "dl": 0.0}
        residual_overload_area = {"ul": 0.0, "dl": 0.0}
        incremental_new_session_overload_area = {"ul": 0.0, "dl": 0.0}
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
                    # Rejections apply to newly arriving sessions. Removing
                    # admitted new-session bytes leaves the load that was
                    # already attached to this UPF at the start of the tick.
                    admitted_new_bytes = max(
                        0.0, item.new_session_offered_bytes - item.rejected_bytes
                    )
                    residual_mbps = max(
                        0.0,
                        admitted_mbps
                        - admitted_new_bytes * 8 / self.step_seconds / 1_000_000,
                    )
                    if item.safe_capacity_mbps > 0:
                        excess = max(0.0, admitted_mbps / item.safe_capacity_mbps - 1.0)
                        residual_excess = max(
                            0.0, residual_mbps / item.safe_capacity_mbps - 1.0
                        )
                        incremental_excess = excess - residual_excess
                    else:
                        excess = math.inf if admitted_mbps > 0 else 0.0
                        residual_excess = math.inf if residual_mbps > 0 else 0.0
                        incremental_excess = (
                            0.0 if residual_mbps > 0 else excess
                        )
                    if excess > 0:
                        overload_duration[direction] += self.step_seconds
                        overload_area[direction] += excess * self.step_seconds
                        residual_overload_area[direction] += residual_excess * self.step_seconds
                        incremental_new_session_overload_area[direction] += (
                            incremental_excess
                        ) * self.step_seconds
                failures += upf.establishment_failures
            rejected["ul"] += step.unplaced_rejected_ul_bytes
            rejected["dl"] += step.unplaced_rejected_dl_bytes
            offered["ul"] += step.unplaced_rejected_ul_bytes
            offered["dl"] += step.unplaced_rejected_dl_bytes
            new_session_offered["ul"] += step.unplaced_rejected_ul_bytes
            new_session_offered["dl"] += step.unplaced_rejected_dl_bytes
            failures += sum(step.group_rejections.values()) - sum(upf.establishment_failures for upf in step.upfs)
        summary = {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "controller": self.controller,
            "control_scope": "new_session_placement_only",
            "session_migration_supported": False,
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
            "residual_overload_area_seconds": residual_overload_area,
            "incremental_new_session_overload_area_seconds": incremental_new_session_overload_area,
            "establishment_failures": failures,
            "selection_audit_stride": self.selection_audit_stride,
            "selection_audits_retained": len(self.selection_audits),
        }
        self._summary_cache = summary
        self._summary_cache_steps = len(self.steps)
        return summary

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
                    {"record_type": "simulation_step", "schema_version": "simulation-step/0.2", **step.to_dict()},
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
        group_upf_bucket = pa.struct([
            ("group_id", pa.string()),
            ("zone", pa.string()),
            ("dnn", pa.string()),
            ("snssai", pa.string()),
            ("five_qi", pa.int16()),
            ("upf_id", pa.string()),
            ("bucket_seconds", pa.int32()),
            ("active_sessions", pa.int64()),
            ("admitted_sessions", pa.int64()),
            ("establishment_failures", pa.int64()),
            ("offered_ul_mbps", pa.float64()),
            ("offered_dl_mbps", pa.float64()),
        ])
        schema = pa.schema([
            ("scenario_id", pa.string()),
            ("seed", pa.int64()),
            ("step", pa.int32()),
            ("window_start", pa.timestamp("us", tz="UTC")),
            ("window_end", pa.timestamp("us", tz="UTC")),
            ("policy_id", pa.string()),
            ("group_arrivals", pa.list_(group_count)),
            ("group_rejections", pa.list_(group_count)),
            ("group_upf_buckets", pa.list_(group_upf_bucket)),
            ("unplaced_rejected_ul_bytes", pa.float64()),
            ("unplaced_rejected_dl_bytes", pa.float64()),
            ("upfs", pa.list_(upf)),
        ], metadata={
            b"schema_version": b"simulation-step/1.1",
            b"controller": self.controller.encode(),
            b"summary": json.dumps(self.summary, sort_keys=True, separators=(",", ":")).encode(),
        })
        with pq.ParquetWriter(
            destination,
            schema,
            compression="zstd",
            version="2.6",
            write_statistics=True,
        ) as writer:
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
                if len(rows) == 4096:
                    writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                    rows.clear()
            if rows:
                writer.write_table(pa.Table.from_pylist(rows, schema=schema))

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
        with pq.ParquetWriter(destination, schema, compression="zstd") as writer:
            rows: list[dict[str, Any]] = []
            for audit in self.selection_audits:
                rows.append({
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
                })
                if len(rows) == 50_000:
                    writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                    rows.clear()
            if rows:
                writer.write_table(pa.Table.from_pylist(rows, schema=schema))


class Simulator:
    def __init__(self, config: ScenarioConfig, controller: Controller | None = None) -> None:
        self.config = config
        self.controller = controller or StaticCapacityController()
        self._upfs = {profile.upf_id: _UPFRuntime(profile) for profile in config.upfs}
        self._active_sessions: Counter[str] = Counter()
        self._active_ul_mbps: Counter[str] = Counter()
        self._active_dl_mbps: Counter[str] = Counter()
        self._active_sessions_by_group_upf: Counter[tuple[str, str]] = Counter()
        self._interval_admitted_by_group_upf: Counter[tuple[str, str]] = Counter()
        self._interval_rejected_by_group_upf: Counter[tuple[str, str]] = Counter()
        self._new_window_ul_mbps: Counter[str] = Counter()
        self._new_window_dl_mbps: Counter[str] = Counter()
        self._departures_by_step: dict[int, Counter[tuple[str, str, float, float]]] = defaultdict(Counter)
        self._new_window_departures_by_step: dict[int, Counter[tuple[str, str, float, float]]] = defaultdict(Counter)
        self._session_sequence: Counter[str] = Counter()
        self._history_by_group: dict[str, list[DemandObservation]] = defaultdict(list)
        self._interval_arrivals: Counter[str] = Counter()
        self._arrival_factors = {group.key.selection_id: 1.0 for group in config.groups}
        self._scheduled_forecast_multipliers = {
            group.key.selection_id: 1.0 for group in config.groups
        }
        self._path_latency_overrides: dict[tuple[str, str], float] = {}
        self._eligible_group_ids_by_upf: dict[str, list[str]] = {
            upf_id: [
                group.key.selection_id
                for group in config.groups
                if upf_id in group.eligible_upfs
            ]
            for upf_id in self._upfs
        }
        self._audit_groups = {
            group.key.selection_id: GroupKey(group.key.zone, group.key.dnn, group.key.snssai)
            for group in config.groups
        }
        self._group_profiles = {
            group.key.selection_id: group
            for group in config.groups
        }
        self._events_by_step: dict[int, list[Any]] = defaultdict(list)
        for event in config.events:
            self._events_by_step[event.step].append(event)
        self._streams = {
            f"arrivals:{group.key.selection_id}": self._random_stream(f"arrivals:{group.key.selection_id}")
            for group in config.groups
        }
        self._streams.update({
            f"lifetimes:{group.key.selection_id}": self._random_stream(f"lifetimes:{group.key.selection_id}")
            for group in config.groups
        })
        self._step_index = 0
        self._policy = None
        self._policy_version = 0
        self._history_closed_at_step: int | None = None
        self._events_applied_at_step: int | None = None
        self._planned_at_step: int | None = None
        self.result = SimulationResult(
            scenario_id=self.config.scenario_id,
            seed=self.config.seed,
            step_seconds=self.config.step_seconds,
            controller=self.controller.name,
            primary_overload_metric=self.config.primary_overload_metric,
            selection_audit_stride=self.config.selection_audit_stride,
        )

    @property
    def current_step(self) -> int:
        return self._step_index

    @property
    def current_policy(self):
        return self._policy

    @property
    def decision_due(self) -> bool:
        return (
            self._step_index < self.config.steps
            and self._step_index % self.config.decision_interval_steps == 0
            and self._planned_at_step != self._step_index
        )

    def inject_event(self, event: Any) -> None:
        """Add an event at or after the next unprocessed tick without replaying history."""
        if event.step < self._step_index:
            raise ValueError("cannot inject an event into already-realized simulation time")
        self._events_by_step[event.step].append(event)
        if event.step == self._step_index:
            self._events_applied_at_step = None
            self._planned_at_step = None

    def replace_controller(self, controller: Controller) -> None:
        self.controller = controller
        self.result.controller = controller.name
        self.result._summary_cache = None
        self._planned_at_step = None

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
        for event in self._events_by_step.get(step, ()):
            if event.event_type == "arrival_factor":
                self._arrival_factors[event.group_id or ""] = event.arrival_factor or 0.0
                if event.forecast_hint_multiplier is not None:
                    self._scheduled_forecast_multipliers[event.group_id or ""] = (
                        event.forecast_hint_multiplier
                    )
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
                eligible_groups=self._eligible_group_ids_by_upf[upf_id],
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
        return Counter(self._active_sessions)

    def _residual_by_upf(self) -> dict[str, ResidualObservation]:
        return {
            upf_id: ResidualObservation(
                self._active_sessions[upf_id],
                max(0.0, self._active_ul_mbps[upf_id]),
                max(0.0, self._active_dl_mbps[upf_id]),
            )
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

    def _control_context(
        self,
        *,
        include_history: bool = True,
        include_oracle: bool = True,
    ) -> ControlContext:
        return ControlContext(
            history_by_group={
                group_id: tuple(items)
                for group_id, items in self._history_by_group.items()
            } if include_history else {},
            residual_by_upf=self._residual_by_upf(),
            oracle_new_by_group=self._peek_interval_arrivals() if include_oracle else {},
            scheduled_multiplier_by_group=dict(self._scheduled_forecast_multipliers),
            scheduled_multiplier_by_group_horizon=self._known_demand_horizon(),
            active_cohorts=self._active_cohort_state(),
        )

    def _known_demand_horizon(self) -> dict[str, tuple[float, ...]]:
        """Return only schedule multipliers available at the current decision time."""

        horizon = int(getattr(getattr(self.controller, "mpc_config", None), "horizon_windows", 12))
        bucket_steps = self.config.decision_interval_steps
        result: dict[str, tuple[float, ...]] = {}
        for group in self.config.groups:
            group_id = group.key.selection_id
            known_events = sorted(
                (
                    event for event in self.config.events
                    if event.event_type == "arrival_factor"
                    and event.group_id == group_id
                    and event.step >= self._step_index
                    and event.known_at_step is not None
                    and event.known_at_step <= self._step_index
                ),
                key=lambda item: item.step,
            )
            values: list[float] = []
            factor = self._arrival_factors[group_id]
            for window in range(horizon):
                start = self._step_index + window * bucket_steps
                end = start + bucket_steps
                samples: list[float] = []
                event_index = 0
                while event_index < len(known_events) and known_events[event_index].step < start:
                    factor = known_events[event_index].forecast_hint_multiplier or known_events[event_index].arrival_factor or 1.0
                    event_index += 1
                window_factor = factor
                for step in range(start, end):
                    while event_index < len(known_events) and known_events[event_index].step == step:
                        window_factor = known_events[event_index].forecast_hint_multiplier or known_events[event_index].arrival_factor or 1.0
                        event_index += 1
                    samples.append(window_factor)
                factor = window_factor
                values.append(sum(samples) / len(samples) if samples else factor)
            result[group_id] = tuple(values)
        return result

    def _active_cohort_state(self):
        """Return exact anchored cohorts without exposing future random arrivals."""
        from optimization import ActiveCohort

        cohorts = []
        for departure_step, departures in sorted(self._departures_by_step.items()):
            remaining_steps = departure_step - self._step_index + 1
            if remaining_steps < 1:
                continue
            for (group_id, upf_id, ul_mbps, dl_mbps), count in sorted(departures.items()):
                if count:
                    cohorts.append(ActiveCohort(
                        group_id=group_id,
                        upf_id=upf_id,
                        sessions=float(count),
                        remaining_steps=remaining_steps,
                        ul_mbps_per_session=ul_mbps,
                        dl_mbps_per_session=dl_mbps,
                    ))
        return tuple(cohorts)

    def _context_for_controller(self) -> ControlContext | None:
        name = self.controller.name
        if name == "static-capacity-v1":
            return None
        if name == "reactive-threshold-v1":
            return self._control_context(include_history=False, include_oracle=False)
        if name == "oracle-highs-v1":
            return self._control_context(include_history=False, include_oracle=True)
        if name in {"forecast-capacity-v1", "predictive-highs-v1", "cohort-mpc-v1"}:
            return self._control_context(include_history=True, include_oracle=False)
        return self._control_context()

    def control_context(self) -> ControlContext:
        return self._control_context()

    def current_states(self) -> list[UPFState]:
        at = self.config.start_time + timedelta(seconds=self._step_index * self.config.step_seconds)
        return self._states(at)

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

    def _prepare_current_step(self) -> tuple[Any, Any]:
        if self._step_index >= self.config.steps:
            raise StopIteration("simulation is complete")
        step = self._step_index
        window_start = self.config.start_time + timedelta(seconds=step * self.config.step_seconds)
        window_end = window_start + timedelta(seconds=self.config.step_seconds)
        if (
            step > 0
            and step % self.config.decision_interval_steps == 0
            and self._history_closed_at_step != step
        ):
            self._close_history_window(window_start)
            self._new_window_ul_mbps.clear()
            self._new_window_dl_mbps.clear()
            self._new_window_departures_by_step.clear()
            self._history_closed_at_step = step
        if self._events_applied_at_step != step:
            self._apply_events(step)
            self._events_applied_at_step = step
        return window_start, window_end

    def replan(self):
        """Close the prior bucket and choose the policy for the next tick."""
        window_start, _ = self._prepare_current_step()
        states = self._states(window_start)
        self._policy_version += 1
        try:
            self._policy = self.controller.build_policy(
                self.config,
                self.config.groups,
                states,
                window_start,
                self._policy_version,
                self._context_for_controller(),
            )
        except RuntimeError:
            self._policy = None
        self._planned_at_step = self._step_index
        return self._policy

    def advance(self) -> StepResult:
        """Advance exactly one 30-second tick using the policy selected beforehand."""
        window_start, window_end = self._prepare_current_step()
        step = self._step_index
        if self.decision_due:
            self.replan()
        policy = self._policy
        states = self._states(window_start)
        state_by_id = {state.upf_id: state for state in states}
        policy_weights_by_group = (
            {item.key.selection_id: item.weights for item in policy.groups}
            if policy is not None else {}
        )
        result = self.result

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
            requested = policy_weights_by_group.get(group_id, {})
            allowed = {
                upf_id: weight
                for upf_id, weight in requested.items()
                if upf_id in group.eligible_upfs
                and state_by_id[upf_id].health in {"healthy", "degraded"}
            }
            total_weight = sum(allowed.values())
            weights = (
                {upf_id: weight / total_weight for upf_id, weight in allowed.items()}
                if total_weight > 0 else {}
            )
            retained_eligible = sorted(weights)
            for _ in range(arrival_count):
                sequence = self._session_sequence[group_id]
                self._session_sequence[group_id] += 1
                retain_audit = sequence % self.config.selection_audit_stride == 0
                if not weights:
                    rejections[group_id] += 1
                    unplaced_rejections[group_id] += 1
                    if retain_audit:
                        session_key = f"{self.config.scenario_id}:{self.config.seed}:{group_id}:{sequence}"
                        result.selection_audits.append(SelectionAudit(
                            timestamp=window_start,
                            session_id_hash=hashlib.sha256(session_key.encode()).hexdigest(),
                            session_hash_value=hashlib.sha256(f"{session_key}\x1fno-policy".encode()).hexdigest(),
                            group=self._audit_groups[group_id],
                            eligible_upfs=[
                                upf_id for upf_id in group.eligible_upfs
                                if state_by_id[upf_id].health in {"healthy", "degraded"}
                            ],
                            requested_weights={}, selected_upf=None,
                            policy_id=policy.policy_id if policy is not None else None,
                            reason="no_eligible_upf",
                        ))
                    continue
                session_key = f"{self.config.scenario_id}:{self.config.seed}:{group_id}:{sequence}"
                # Keep the rendezvous hash namespace stable across controller
                # policies. Weight changes should change placement through the
                # weighted score, not by independently re-salting every key;
                # this also gives paired controller experiments common random
                # numbers for the selection mechanism.
                selection_namespace = (
                    f"{self.config.scenario_id}:weighted-rendezvous-v1"
                )
                selected, hash_value = rendezvous_select(
                    session_key, selection_namespace, weights
                )
                reason = "fallback_static" if policy.fallback.used else "optimizer_weighted"
                if policy.fallback.source_policy_id:
                    reason = "fallback_last_safe"
                if retain_audit:
                    result.selection_audits.append(SelectionAudit(
                        timestamp=window_start,
                        session_id_hash=hashlib.sha256(session_key.encode()).hexdigest(),
                        session_hash_value=hash_value,
                        group=self._audit_groups[group_id],
                        eligible_upfs=retained_eligible,
                        requested_weights=weights, selected_upf=selected,
                        policy_id=policy.policy_id, reason=reason,
                    ))
                runtime = self._upfs[selected]
                if active_before[selected] + admitted[selected] >= runtime.profile.session_capacity:
                    rejections[group_id] += 1
                    rejected_by_upf[selected] += 1
                    rejected_by_upf_group[(selected, group_id)] += 1
                    self._interval_rejected_by_group_upf[(group_id, selected)] += 1
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
            ul_load = count * ul_mbps
            dl_load = count * dl_mbps
            self._active_sessions[upf_id] += count
            self._active_ul_mbps[upf_id] += ul_load
            self._active_dl_mbps[upf_id] += dl_load
            self._active_sessions_by_group_upf[(group_id, upf_id)] += count
            self._interval_admitted_by_group_upf[(group_id, upf_id)] += count
            self._new_window_ul_mbps[upf_id] += ul_load
            self._new_window_dl_mbps[upf_id] += dl_load
            departure_step = step + lifetime - 1
            departure_key = (group_id, upf_id, ul_mbps, dl_mbps)
            self._departures_by_step[departure_step][departure_key] += count
            self._new_window_departures_by_step[departure_step][departure_key] += count

        group_upf_admissions: dict[str, dict[str, int]] = {}
        for (group_id, upf_id, _, _, _), count in sorted(new_cohorts.items()):
            by_upf = group_upf_admissions.setdefault(group_id, {})
            by_upf[upf_id] = by_upf.get(upf_id, 0) + count

        offered_ul = self._active_ul_mbps
        offered_dl = self._active_dl_mbps
        new_offered_ul = self._new_window_ul_mbps
        new_offered_dl = self._new_window_dl_mbps
        departures = self._departures_by_step.get(step, Counter())
        departing: Counter[str] = Counter()
        for (_, upf_id, _, _), count in departures.items():
            departing[upf_id] += count

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
        group_upf_buckets: list[GroupUPFBucketResult] = []
        closes_bucket = (
            (step + 1) % self.config.decision_interval_steps == 0
            or step + 1 == self.config.steps
        )
        if closes_bucket:
            bucket_steps = step % self.config.decision_interval_steps + 1
            keys = (
                set(self._active_sessions_by_group_upf)
                | set(self._interval_admitted_by_group_upf)
                | set(self._interval_rejected_by_group_upf)
            )
            for group_id, upf_id in sorted(keys):
                group = self._group_profiles[group_id]
                active = self._active_sessions_by_group_upf[(group_id, upf_id)]
                group_upf_buckets.append(GroupUPFBucketResult(
                    group_id=group_id,
                    zone=group.key.zone,
                    dnn=group.key.dnn,
                    snssai=group.key.snssai,
                    five_qi=group.key.five_qi,
                    upf_id=upf_id,
                    bucket_seconds=bucket_steps * self.config.step_seconds,
                    active_sessions=active,
                    admitted_sessions=self._interval_admitted_by_group_upf[(group_id, upf_id)],
                    establishment_failures=self._interval_rejected_by_group_upf[(group_id, upf_id)],
                    offered_ul_mbps=active * group.offered_ul_mbps_per_session,
                    offered_dl_mbps=active * group.offered_dl_mbps_per_session,
                ))
            self._interval_admitted_by_group_upf.clear()
            self._interval_rejected_by_group_upf.clear()

        step_result = StepResult(
            scenario_id=self.config.scenario_id, seed=self.config.seed, step=step,
            window_start=window_start, window_end=window_end,
            policy_id=policy.policy_id if policy is not None else "none",
            group_arrivals=arrivals, group_rejections=dict(rejections), upfs=upf_results,
            group_upf_admissions=group_upf_admissions,
            group_upf_buckets=group_upf_buckets,
            unplaced_rejected_ul_bytes=unplaced_ul_mbps * 1_000_000 / 8 * self.config.step_seconds,
            unplaced_rejected_dl_bytes=unplaced_dl_mbps * 1_000_000 / 8 * self.config.step_seconds,
        )
        result.steps.append(step_result)
        for (group_id, upf_id, ul_mbps, dl_mbps), count in self._departures_by_step.pop(step, {}).items():
            self._active_sessions[upf_id] -= count
            self._active_ul_mbps[upf_id] -= count * ul_mbps
            self._active_dl_mbps[upf_id] -= count * dl_mbps
            self._active_sessions_by_group_upf[(group_id, upf_id)] -= count
            if self._active_sessions[upf_id] == 0:
                del self._active_sessions[upf_id]
            if abs(self._active_ul_mbps[upf_id]) < 1e-7:
                del self._active_ul_mbps[upf_id]
            if abs(self._active_dl_mbps[upf_id]) < 1e-7:
                del self._active_dl_mbps[upf_id]
            if self._active_sessions_by_group_upf[(group_id, upf_id)] == 0:
                del self._active_sessions_by_group_upf[(group_id, upf_id)]
        for (_, upf_id, ul_mbps, dl_mbps), count in self._new_window_departures_by_step.pop(step, {}).items():
            self._new_window_ul_mbps[upf_id] -= count * ul_mbps
            self._new_window_dl_mbps[upf_id] -= count * dl_mbps
            if abs(self._new_window_ul_mbps[upf_id]) < 1e-7:
                del self._new_window_ul_mbps[upf_id]
            if abs(self._new_window_dl_mbps[upf_id]) < 1e-7:
                del self._new_window_dl_mbps[upf_id]
        self._step_index += 1
        return step_result

    def run(
        self,
        *,
        progress_interval_steps: int | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> SimulationResult:
        if (progress_interval_steps is None) != (progress_callback is None):
            raise ValueError("progress interval and callback must be provided together")
        if progress_interval_steps is not None and progress_interval_steps <= 0:
            raise ValueError("progress_interval_steps must be positive")
        while self._step_index < self.config.steps:
            self.advance()
            if (
                progress_callback is not None
                and progress_interval_steps is not None
                and (
                    self._step_index % progress_interval_steps == 0
                    or self._step_index == self.config.steps
                )
            ):
                progress_callback(self._step_index, self.config.steps)
        return self.result

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
