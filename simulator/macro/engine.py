from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from schemas import Capacity, GroupKey, Policy, SelectionAudit, TimeWindow, UPFState
from schemas.common import iso_utc, parse_utc
from forecasting import DemandObservation, ResidualObservation
from steering import rendezvous_select

from .config import GroupProfile, ScenarioConfig, ScenarioEvent, UPFProfile
from .controllers import (
    ControlContext,
    Controller,
    StaticCapacityController,
    restore_controller,
    snapshot_controller,
)
from .model import DirectionResult, GroupUPFBucketResult, StepResult, UPFStepResult
from .realism import TelemetryObservationV2, TrafficRealismRuntimeV2
from .sinks import ArtifactDescriptor, BoundedMemorySink, CompositeSink, SummarySink


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
class _DetachedArtifactWriter:
    """Private legacy-format helper; never constructed or retained by Simulator."""
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
        group_generated_load = pa.struct([
            ("group_id", pa.string()), ("ul_mbps", pa.float64()),
            ("dl_mbps", pa.float64()),
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
            ("group_generated_load_mbps", pa.list_(group_generated_load)),
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
                row["group_generated_load_mbps"] = [
                    {"group_id": key, "ul_mbps": value["ul"], "dl_mbps": value["dl"]}
                    for key, value in sorted(step.group_generated_load_mbps.items())
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


@dataclass(frozen=True, slots=True)
class RunOutcome:
    summary: dict[str, Any]
    step_count: int
    audit_count: int
    artifacts: tuple[ArtifactDescriptor, ...]
    timings: dict[str, float]
    completed: bool
    completion_status: str
    checkpoint: str | None = None


class Simulator:
    def __init__(self, config: ScenarioConfig, controller: Controller | None = None) -> None:
        self.config = config
        self.controller = controller or StaticCapacityController()
        self._upfs = {profile.upf_id: _UPFRuntime(profile) for profile in config.upfs}
        self._active_sessions: Counter[str] = Counter()
        self._active_ul_mbps: Counter[str] = Counter()
        self._active_dl_mbps: Counter[str] = Counter()
        self._active_sessions_by_group_upf: Counter[tuple[str, str]] = Counter()
        self._active_ul_by_group_upf: Counter[tuple[str, str]] = Counter()
        self._active_dl_by_group_upf: Counter[tuple[str, str]] = Counter()
        self._interval_admitted_by_group_upf: Counter[tuple[str, str]] = Counter()
        self._interval_rejected_by_group_upf: Counter[tuple[str, str]] = Counter()
        self._new_window_ul_mbps: Counter[str] = Counter()
        self._new_window_dl_mbps: Counter[str] = Counter()
        self._departures_by_step: dict[int, Counter[tuple[str, str, float, float]]] = defaultdict(Counter)
        self._new_window_departures_by_step: dict[int, Counter[tuple[str, str, float, float]]] = defaultdict(Counter)
        self._session_sequence: Counter[str] = Counter()
        required_history = int(getattr(self.controller, "required_history_windows", 0))
        self._required_history_windows = required_history
        self._history_by_group: dict[str, deque[DemandObservation]] = defaultdict(
            lambda: deque(maxlen=required_history or 1)
        )
        self._interval_arrivals: Counter[str] = Counter()
        self._interval_new_ul_by_group: Counter[str] = Counter()
        self._interval_new_dl_by_group: Counter[str] = Counter()
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
        self._realism_v2: TrafficRealismRuntimeV2 | None = None
        self._latest_telemetry_v2: tuple[TelemetryObservationV2, ...] = ()
        if config.traffic_model is not None:
            for group in config.groups:
                group_id = group.key.selection_id
                for purpose in ("demand", "burst", "rates", "holding"):
                    name = f"v2:{purpose}:{group_id}"
                    self._streams[name] = self._random_stream(name)
            for upf in config.upfs:
                name = f"v2:telemetry:{upf.upf_id}"
                self._streams[name] = self._random_stream(name)
            self._realism_v2 = TrafficRealismRuntimeV2(
                config.traffic_model, config.groups, self._streams
            )
        self._step_index = 0
        self._policy = None
        self._policy_version = 0
        self._history_closed_at_step: int | None = None
        self._events_applied_at_step: int | None = None
        self._planned_at_step: int | None = None
        self._event_sink: CompositeSink | None = None
        self._advance_sink: BoundedMemorySink | None = None
        self._timings: defaultdict[str, float] = defaultdict(float)
        for phase in (
            "arrival_generation_seconds", "lifetime_generation_seconds",
            "rendezvous_selection_seconds", "controller_work_seconds",
            "result_construction_seconds", "sink_writes_seconds",
            "checkpointing_seconds", "stage_out_seconds",
        ):
            self._timings[phase] = 0.0

    @property
    def current_step(self) -> int:
        return self._step_index

    @property
    def current_policy(self):
        return self._policy

    @property
    def traffic_model_version(self) -> str:
        return "traffic-model/2.0" if self._realism_v2 is not None else "traffic-model/1.0"

    @property
    def latest_telemetry_v2(self) -> tuple[TelemetryObservationV2, ...]:
        return self._latest_telemetry_v2

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
        if self._advance_sink is not None:
            self._advance_sink.summary_sink.controller = controller.name
        required_history = int(getattr(controller, "required_history_windows", 0))
        if required_history != self._required_history_windows:
            self._required_history_windows = required_history
            self._history_by_group = defaultdict(
                lambda: deque(maxlen=required_history or 1),
                {
                    key: deque(items, maxlen=required_history or 1)
                    for key, items in self._history_by_group.items()
                },
            )
        self._planned_at_step = None

    def make_summary_sink(self) -> SummarySink:
        return SummarySink(
            scenario_id=self.config.scenario_id,
            seed=self.config.seed,
            step_seconds=self.config.step_seconds,
            controller=self.controller.name,
            primary_overload_metric=self.config.primary_overload_metric,
            selection_audit_stride=1,
        )

    def attach_bounded_advance_sink(self, sink: BoundedMemorySink) -> None:
        """Attach the explicit fixed-size collector used only by the causal demo."""
        if self._step_index or self._event_sink is not None:
            raise RuntimeError("a bounded advance sink must be attached before execution")
        self._advance_sink = sink

    def _accept_audit(self, audit: SelectionAudit) -> None:
        if self._event_sink is not None:
            self._event_sink.accept_audit(audit)
        elif self._advance_sink is not None:
            self._advance_sink.accept_audit(audit)

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
        if self._required_history_windows == 0:
            self._interval_arrivals.clear()
            self._interval_new_ul_by_group.clear()
            self._interval_new_dl_by_group.clear()
            return
        duration = timedelta(seconds=self.config.step_seconds * self.config.decision_interval_steps)
        window = TimeWindow(window_end - duration, window_end)
        residual = self._residual_by_upf()
        for group in self.config.groups:
            arrivals = self._interval_arrivals[group.key.selection_id]
            if self._realism_v2 is None:
                new_ul_mbps = arrivals * group.offered_ul_mbps_per_session
                new_dl_mbps = arrivals * group.offered_dl_mbps_per_session
            else:
                new_ul_mbps = self._interval_new_ul_by_group[group.key.selection_id]
                new_dl_mbps = self._interval_new_dl_by_group[group.key.selection_id]
            self._history_by_group[group.key.selection_id].append(DemandObservation(
                window=window,
                group=group.key,
                new_session_count=float(arrivals),
                new_ul_mbps=new_ul_mbps,
                new_dl_mbps=new_dl_mbps,
                existing_load_by_upf=residual,
            ))
        self._interval_arrivals.clear()
        self._interval_new_ul_by_group.clear()
        self._interval_new_dl_by_group.clear()

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
            realism_multiplier = (
                self._realism_v2.current_arrival_multiplier(group, self._step_index)
                if self._realism_v2 is not None else 1.0
            )
            count = sum(
                self._poisson(group.arrivals_per_step * self._arrival_factors[group_id]
                              * realism_multiplier, clone)
                for _ in range(self.config.decision_interval_steps)
            )
            expected_ul, expected_dl = (
                self._realism_v2.expected_rates(group)
                if self._realism_v2 is not None else (
                    group.offered_ul_mbps_per_session, group.offered_dl_mbps_per_session
                )
            )
            result[group_id] = ResidualObservation(
                count,
                count * expected_ul,
                count * expected_dl,
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
        if self._realism_v2 is not None:
            self._realism_v2.prepare_step(step)
        return window_start, window_end

    def replan(self):
        """Close the prior bucket and choose the policy for the next tick."""
        window_start, _ = self._prepare_current_step()
        states = self._states(window_start)
        self._policy_version += 1
        started = time.perf_counter()
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
        finally:
            self._timings["controller_work_seconds"] += time.perf_counter() - started
        if self._event_sink is not None:
            self._event_sink.accept_policy(
                self._policy, self._step_index, self._decision_trace_details()
            )
        self._planned_at_step = self._step_index
        return self._policy

    def _decision_trace_details(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "controller": self.controller.name,
            "controller_state": snapshot_controller(self.controller),
            "forecasts": [
                item.to_dict() for item in getattr(self.controller, "last_forecasts", ())
            ],
        }
        gate = getattr(self.controller, "last_gate_decision", None)
        if gate is not None:
            details["policy_gate"] = gate.to_dict()
        optimization = getattr(self.controller, "last_optimization", None)
        if optimization is not None:
            details["optimization"] = {
                "status": optimization.status, "message": optimization.message,
                "projected_ul_mbps_by_upf": optimization.projected_ul_mbps_by_upf,
                "projected_dl_mbps_by_upf": optimization.projected_dl_mbps_by_upf,
                "projected_sessions_by_upf": optimization.projected_sessions_by_upf,
                "max_safe_utilization": optimization.max_safe_utilization,
            }
        mpc = getattr(self.controller, "last_result", None)
        if mpc is not None:
            details["mpc"] = {
                "status": mpc.status, "message": mpc.message,
                "runtime_ms": mpc.runtime_ms,
                "first_allocation": mpc.first_allocation,
                "static_first_allocation": mpc.static_first_allocation,
                "planned_allocation": [
                    {"group_id": key[0], "horizon": key[1], "weights": weights}
                    for key, weights in sorted(mpc.planned_allocation.items())
                ],
                "certificate": asdict(mpc.certificate) if mpc.certificate is not None else None,
                "known_future_events": mpc.known_future_events,
                "survival": mpc.survival_audit,
            }
        return details

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
        active_before = self._active_counts()
        arrivals: dict[str, int] = {}
        rejections: Counter[str] = Counter()
        admitted: Counter[str] = Counter()
        rejected_by_upf: Counter[str] = Counter()
        rejected_by_upf_group: Counter[tuple[str, str]] = Counter()
        rejected_ul_by_upf: Counter[str] = Counter()
        rejected_dl_by_upf: Counter[str] = Counter()
        unplaced_rejections: Counter[str] = Counter()
        unplaced_ul_mbps = 0.0
        unplaced_dl_mbps = 0.0
        new_cohorts: Counter[tuple[str, str, int, float, float]] = Counter()
        generated_ul_by_group: Counter[str] = Counter()
        generated_dl_by_group: Counter[str] = Counter()

        for group in self.config.groups:
            group_id = group.key.selection_id
            phase_started = time.perf_counter()
            realism_multiplier = (
                self._realism_v2.arrival_multiplier(group, step)
                if self._realism_v2 is not None else 1.0
            )
            arrival_count = self._poisson(
                group.arrivals_per_step * self._arrival_factors[group_id] * realism_multiplier,
                self._streams[f"arrivals:{group_id}"],
            )
            self._timings["arrival_generation_seconds"] += time.perf_counter() - phase_started
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
                if self._realism_v2 is None:
                    session_ul_mbps = group.offered_ul_mbps_per_session
                    session_dl_mbps = group.offered_dl_mbps_per_session
                else:
                    session_ul_mbps, session_dl_mbps = self._realism_v2.sample_rates(group)
                    self._interval_new_ul_by_group[group_id] += session_ul_mbps
                    self._interval_new_dl_by_group[group_id] += session_dl_mbps
                generated_ul_by_group[group_id] += session_ul_mbps
                generated_dl_by_group[group_id] += session_dl_mbps
                sequence = self._session_sequence[group_id]
                self._session_sequence[group_id] += 1
                if not weights:
                    rejections[group_id] += 1
                    unplaced_rejections[group_id] += 1
                    if self._realism_v2 is not None:
                        unplaced_ul_mbps += session_ul_mbps
                        unplaced_dl_mbps += session_dl_mbps
                    session_key = f"{self.config.scenario_id}:{self.config.seed}:{group_id}:{sequence}"
                    self._accept_audit(SelectionAudit(
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
                phase_started = time.perf_counter()
                selected, hash_value = rendezvous_select(
                    session_key, selection_namespace, weights
                )
                self._timings["rendezvous_selection_seconds"] += time.perf_counter() - phase_started
                reason = "fallback_static" if policy.fallback.used else "optimizer_weighted"
                if policy.fallback.source_policy_id:
                    reason = "fallback_last_safe"
                self._accept_audit(SelectionAudit(
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
                    if self._realism_v2 is not None:
                        rejected_ul_by_upf[selected] += session_ul_mbps
                        rejected_dl_by_upf[selected] += session_dl_mbps
                    self._interval_rejected_by_group_upf[(group_id, selected)] += 1
                    continue
                phase_started = time.perf_counter()
                lifetime = (
                    self._streams[f"lifetimes:{group_id}"].randint(
                        group.lifetime_steps_min, group.lifetime_steps_max
                    ) if self._realism_v2 is None else self._realism_v2.sample_lifetime(group)
                )
                self._timings["lifetime_generation_seconds"] += time.perf_counter() - phase_started
                admitted[selected] += 1
                new_cohorts[(
                    group_id, selected, lifetime,
                    session_ul_mbps, session_dl_mbps,
                )] += 1

        for key, count in new_cohorts.items():
            group_id, upf_id, lifetime, ul_mbps, dl_mbps = key
            ul_load = count * ul_mbps
            dl_load = count * dl_mbps
            self._active_sessions[upf_id] += count
            self._active_ul_mbps[upf_id] += ul_load
            self._active_dl_mbps[upf_id] += dl_load
            self._active_sessions_by_group_upf[(group_id, upf_id)] += count
            if self._realism_v2 is not None:
                self._active_ul_by_group_upf[(group_id, upf_id)] += ul_load
                self._active_dl_by_group_upf[(group_id, upf_id)] += dl_load
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
            rejected_ul_mbps = rejected_ul_by_upf[upf_id]
            rejected_dl_mbps = rejected_dl_by_upf[upf_id]
            if self._realism_v2 is None and rejected_by_upf[upf_id]:
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

        if self._realism_v2 is None:
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
                if self._realism_v2 is None:
                    group_ul_mbps = active * group.offered_ul_mbps_per_session
                    group_dl_mbps = active * group.offered_dl_mbps_per_session
                else:
                    group_ul_mbps = self._active_ul_by_group_upf[(group_id, upf_id)]
                    group_dl_mbps = self._active_dl_by_group_upf[(group_id, upf_id)]
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
                    offered_ul_mbps=group_ul_mbps,
                    offered_dl_mbps=group_dl_mbps,
                ))
            self._interval_admitted_by_group_upf.clear()
            self._interval_rejected_by_group_upf.clear()

        phase_started = time.perf_counter()
        step_result = StepResult(
            scenario_id=self.config.scenario_id, seed=self.config.seed, step=step,
            window_start=window_start, window_end=window_end,
            policy_id=policy.policy_id if policy is not None else "none",
            group_arrivals=arrivals, group_rejections=dict(rejections), upfs=upf_results,
            group_upf_admissions=group_upf_admissions,
            group_upf_buckets=group_upf_buckets,
            group_generated_load_mbps={
                group.key.selection_id: {
                    "ul": float(generated_ul_by_group[group.key.selection_id]),
                    "dl": float(generated_dl_by_group[group.key.selection_id]),
                }
                for group in self.config.groups
            } if self._realism_v2 is not None else {},
            unplaced_rejected_ul_bytes=unplaced_ul_mbps * 1_000_000 / 8 * self.config.step_seconds,
            unplaced_rejected_dl_bytes=unplaced_dl_mbps * 1_000_000 / 8 * self.config.step_seconds,
        )
        self._timings["result_construction_seconds"] += time.perf_counter() - phase_started
        if self._realism_v2 is not None:
            self._latest_telemetry_v2 = self._realism_v2.observe(step_result)
        for (group_id, upf_id, ul_mbps, dl_mbps), count in self._departures_by_step.pop(step, {}).items():
            self._active_sessions[upf_id] -= count
            self._active_ul_mbps[upf_id] -= count * ul_mbps
            self._active_dl_mbps[upf_id] -= count * dl_mbps
            self._active_sessions_by_group_upf[(group_id, upf_id)] -= count
            if self._realism_v2 is not None:
                self._active_ul_by_group_upf[(group_id, upf_id)] -= count * ul_mbps
                self._active_dl_by_group_upf[(group_id, upf_id)] -= count * dl_mbps
            if self._active_sessions[upf_id] == 0:
                del self._active_sessions[upf_id]
            if abs(self._active_ul_mbps[upf_id]) < 1e-7:
                del self._active_ul_mbps[upf_id]
            if abs(self._active_dl_mbps[upf_id]) < 1e-7:
                del self._active_dl_mbps[upf_id]
            if self._active_sessions_by_group_upf[(group_id, upf_id)] == 0:
                del self._active_sessions_by_group_upf[(group_id, upf_id)]
            if self._realism_v2 is not None:
                if abs(self._active_ul_by_group_upf[(group_id, upf_id)]) < 1e-7:
                    del self._active_ul_by_group_upf[(group_id, upf_id)]
                if abs(self._active_dl_by_group_upf[(group_id, upf_id)]) < 1e-7:
                    del self._active_dl_by_group_upf[(group_id, upf_id)]
        for (_, upf_id, ul_mbps, dl_mbps), count in self._new_window_departures_by_step.pop(step, {}).items():
            self._new_window_ul_mbps[upf_id] -= count * ul_mbps
            self._new_window_dl_mbps[upf_id] -= count * dl_mbps
            if abs(self._new_window_ul_mbps[upf_id]) < 1e-7:
                del self._new_window_ul_mbps[upf_id]
            if abs(self._new_window_dl_mbps[upf_id]) < 1e-7:
                del self._new_window_dl_mbps[upf_id]
        self._step_index += 1
        if self._advance_sink is not None:
            self._advance_sink.accept_step(step_result)
        return step_result

    @staticmethod
    def _counter_state(counter: Counter[Any]) -> list[list[Any]]:
        # Counter insertion order is causal state: floating-point cohort
        # removals must replay in the same order for bit-exact artifacts.
        return [[list(key) if isinstance(key, tuple) else key, value] for key, value in counter.items()]

    @staticmethod
    def _restore_counter(items: list[list[Any]], *, tuple_key: bool = False) -> Counter[Any]:
        return Counter({tuple(key) if tuple_key else key: value for key, value in items})

    @staticmethod
    def _observation_state(item: DemandObservation) -> dict[str, Any]:
        return {
            "window": {"start": iso_utc(item.window.start), "end": iso_utc(item.window.end)},
            "group": asdict(item.group), "new_session_count": item.new_session_count,
            "new_ul_mbps": item.new_ul_mbps, "new_dl_mbps": item.new_dl_mbps,
            "existing_load_by_upf": {
                key: asdict(value) for key, value in item.existing_load_by_upf.items()
            },
            "quality_flags": list(item.quality_flags),
            "regime": item.regime,
            "event_features": dict(item.event_features),
            "available_at": {
                key: iso_utc(value) for key, value in item.available_at.items()
            },
            "telemetry_age_seconds": item.telemetry_age_seconds,
            "telemetry_missing": item.telemetry_missing,
            "counter_reset": item.counter_reset,
        }

    @staticmethod
    def _restore_observation(item: dict[str, Any]) -> DemandObservation:
        return DemandObservation(
            window=TimeWindow.from_dict(item["window"]),
            group=GroupKey.from_dict(item["group"]),
            new_session_count=float(item["new_session_count"]),
            new_ul_mbps=float(item["new_ul_mbps"]),
            new_dl_mbps=float(item["new_dl_mbps"]),
            existing_load_by_upf={
                key: ResidualObservation(**value)
                for key, value in item["existing_load_by_upf"].items()
            },
            quality_flags=tuple(item.get("quality_flags", [])),
            regime=str(item.get("regime", "normal")),
            event_features={
                str(key): float(value)
                for key, value in item.get("event_features", {}).items()
            },
            available_at={
                str(key): parse_utc(value)
                for key, value in item.get("available_at", {}).items()
            },
            telemetry_age_seconds=float(item.get("telemetry_age_seconds", 0.0)),
            telemetry_missing=bool(item.get("telemetry_missing", False)),
            counter_reset=bool(item.get("counter_reset", False)),
        )

    def snapshot_state(self) -> dict[str, Any]:
        """Return the complete JSON-safe causal state at a post-step boundary."""
        state = {
            "codec_version": "simulator-state/1.0",
            "current_step": self._step_index,
            "policy_version": self._policy_version,
            "policy": self._policy.to_dict() if self._policy is not None else None,
            "boundary_markers": {
                "history_closed_at_step": self._history_closed_at_step,
                "events_applied_at_step": self._events_applied_at_step,
                "planned_at_step": self._planned_at_step,
            },
            "upfs": {
                key: {
                    "health": value.health, "ul_factor": value.ul_factor,
                    "dl_factor": value.dl_factor, "ul_queue_bytes": value.ul_queue_bytes,
                    "dl_queue_bytes": value.dl_queue_bytes,
                } for key, value in sorted(self._upfs.items())
            },
            "counters": {
                "active_sessions": self._counter_state(self._active_sessions),
                "active_ul_mbps": self._counter_state(self._active_ul_mbps),
                "active_dl_mbps": self._counter_state(self._active_dl_mbps),
                "active_sessions_by_group_upf": self._counter_state(self._active_sessions_by_group_upf),
                "interval_admitted_by_group_upf": self._counter_state(self._interval_admitted_by_group_upf),
                "interval_rejected_by_group_upf": self._counter_state(self._interval_rejected_by_group_upf),
                "new_window_ul_mbps": self._counter_state(self._new_window_ul_mbps),
                "new_window_dl_mbps": self._counter_state(self._new_window_dl_mbps),
                "interval_arrivals": self._counter_state(self._interval_arrivals),
                "session_sequence": self._counter_state(self._session_sequence),
            },
            "departure_schedules": {
                "active": [[step, self._counter_state(counter)] for step, counter in sorted(self._departures_by_step.items())],
                "new_window": [[step, self._counter_state(counter)] for step, counter in sorted(self._new_window_departures_by_step.items())],
            },
            "forecast_history": {
                key: [self._observation_state(item) for item in values]
                for key, values in sorted(self._history_by_group.items())
            },
            "arrival_factors": dict(self._arrival_factors),
            "scheduled_forecast_multipliers": dict(self._scheduled_forecast_multipliers),
            "path_latency_overrides": [
                [list(key), value] for key, value in sorted(self._path_latency_overrides.items())
            ],
            "events": [[step, [asdict(event) for event in events]] for step, events in sorted(self._events_by_step.items())],
            "rng_states": {key: stream.getstate() for key, stream in sorted(self._streams.items())},
            "controller": snapshot_controller(self.controller),
        }
        if self._realism_v2 is not None:
            state["traffic_model_version"] = "traffic-model/2.0"
            state["counters"]["interval_new_ul_by_group"] = self._counter_state(
                self._interval_new_ul_by_group
            )
            state["counters"]["interval_new_dl_by_group"] = self._counter_state(
                self._interval_new_dl_by_group
            )
            state["counters"]["active_ul_by_group_upf"] = self._counter_state(
                self._active_ul_by_group_upf
            )
            state["counters"]["active_dl_by_group_upf"] = self._counter_state(
                self._active_dl_by_group_upf
            )
            state["realism_v2"] = self._realism_v2.snapshot_state()
        return state

    def restore_state(self, state: dict[str, Any]) -> None:
        if state.get("codec_version") != "simulator-state/1.0":
            raise ValueError("unsupported simulator checkpoint codec")
        if self._step_index != 0:
            raise RuntimeError("resume requires a fresh Simulator")
        self._step_index = int(state["current_step"])
        if not 0 <= self._step_index <= self.config.steps:
            raise ValueError("checkpoint step lies outside the scenario")
        self._policy_version = int(state["policy_version"])
        self._policy = Policy.from_dict(state["policy"]) if state.get("policy") is not None else None
        markers = state["boundary_markers"]
        self._history_closed_at_step = markers["history_closed_at_step"]
        self._events_applied_at_step = markers["events_applied_at_step"]
        self._planned_at_step = markers["planned_at_step"]
        if set(state["upfs"]) != set(self._upfs):
            raise ValueError("checkpoint topology does not match simulator UPFs")
        for key, values in state["upfs"].items():
            runtime = self._upfs[key]
            runtime.health = values["health"]
            runtime.ul_factor = float(values["ul_factor"])
            runtime.dl_factor = float(values["dl_factor"])
            runtime.ul_queue_bytes = float(values["ul_queue_bytes"])
            runtime.dl_queue_bytes = float(values["dl_queue_bytes"])
        counters = state["counters"]
        for name in ("active_sessions", "active_ul_mbps", "active_dl_mbps", "new_window_ul_mbps", "new_window_dl_mbps", "interval_arrivals", "session_sequence"):
            setattr(self, f"_{name}", self._restore_counter(counters[name]))
        for name in ("active_sessions_by_group_upf", "interval_admitted_by_group_upf", "interval_rejected_by_group_upf"):
            setattr(self, f"_{name}", self._restore_counter(counters[name], tuple_key=True))
        if self._realism_v2 is not None:
            if state.get("traffic_model_version") != "traffic-model/2.0" or "realism_v2" not in state:
                raise ValueError("traffic-model/2.0 checkpoint is missing realism state")
            self._interval_new_ul_by_group = self._restore_counter(
                counters["interval_new_ul_by_group"]
            )
            self._interval_new_dl_by_group = self._restore_counter(
                counters["interval_new_dl_by_group"]
            )
            self._active_ul_by_group_upf = self._restore_counter(
                counters["active_ul_by_group_upf"], tuple_key=True
            )
            self._active_dl_by_group_upf = self._restore_counter(
                counters["active_dl_by_group_upf"], tuple_key=True
            )
            self._realism_v2.restore_state(state["realism_v2"])
            self._latest_telemetry_v2 = tuple(self._realism_v2.telemetry)
        elif state.get("traffic_model_version") is not None:
            raise ValueError("traffic-model checkpoint does not match this v1 scenario")
        self._departures_by_step = defaultdict(Counter, {
            int(step): self._restore_counter(items, tuple_key=True)
            for step, items in state["departure_schedules"]["active"]
        })
        self._new_window_departures_by_step = defaultdict(Counter, {
            int(step): self._restore_counter(items, tuple_key=True)
            for step, items in state["departure_schedules"]["new_window"]
        })
        self._history_by_group = defaultdict(
            lambda: deque(maxlen=self._required_history_windows or 1),
            {
                key: deque(
                    (self._restore_observation(item) for item in values),
                    maxlen=self._required_history_windows or 1,
                ) for key, values in state["forecast_history"].items()
            },
        )
        self._arrival_factors = {str(k): float(v) for k, v in state["arrival_factors"].items()}
        self._scheduled_forecast_multipliers = {
            str(k): float(v) for k, v in state["scheduled_forecast_multipliers"].items()
        }
        self._path_latency_overrides = {
            tuple(key): float(value) for key, value in state["path_latency_overrides"]
        }
        self._events_by_step = defaultdict(list, {
            int(step): [ScenarioEvent(**event) for event in events]
            for step, events in state["events"]
        })
        if set(state["rng_states"]) != set(self._streams):
            raise ValueError("checkpoint RNG stream identities mismatch")
        def tuples(value: Any) -> Any:
            return tuple(tuples(item) for item in value) if isinstance(value, list) else value
        for key, random_state in state["rng_states"].items():
            self._streams[key].setstate(tuples(random_state))
        restore_controller(self.controller, state["controller"])

    def run(
        self,
        sinks: CompositeSink | list[Any] | tuple[Any, ...],
        checkpoint_manager: Any | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        *,
        progress_interval_steps: int | None = None,
    ) -> RunOutcome:
        if progress_callback is not None and progress_interval_steps is None:
            progress_interval_steps = 1
        if progress_interval_steps is not None and progress_callback is None:
            raise ValueError("a progress interval requires a progress callback")
        if progress_interval_steps is not None and progress_interval_steps <= 0:
            raise ValueError("progress_interval_steps must be positive")
        if self._advance_sink is not None:
            raise RuntimeError("run() cannot be combined with the demo's bounded advance sink")
        composite = (
            sinks if isinstance(sinks, CompositeSink)
            else CompositeSink(sinks if isinstance(sinks, (list, tuple)) else [sinks])
        )
        summary_sinks = [sink for sink in composite.sinks if isinstance(sink, SummarySink)]
        if len(summary_sinks) != 1:
            raise ValueError("exactly one SummarySink is required")
        summary_sink = summary_sinks[0]
        checkpoint_path: str | None = None
        completed = False
        self._event_sink = composite
        try:
            if checkpoint_manager is not None and self._step_index == 0:
                restored = checkpoint_manager.restore_latest(self, composite)
                checkpoint_path = str(restored) if restored is not None else None
            while self._step_index < self.config.steps:
                if checkpoint_manager is not None and checkpoint_manager.stop_requested:
                    if not checkpoint_manager.is_checkpoint_current(self._step_index):
                        checkpoint_started = time.perf_counter()
                        checkpoint_path = str(checkpoint_manager.save(self, composite))
                        self._timings["checkpointing_seconds"] += time.perf_counter() - checkpoint_started
                    break
                step = self.advance()
                sink_started = time.perf_counter()
                composite.accept_step(step)
                self._timings["sink_writes_seconds"] += time.perf_counter() - sink_started
                if checkpoint_manager is not None and checkpoint_manager.should_checkpoint(self._step_index):
                    checkpoint_started = time.perf_counter()
                    checkpoint_path = str(checkpoint_manager.save(self, composite))
                    self._timings["checkpointing_seconds"] += time.perf_counter() - checkpoint_started
                if (
                    progress_callback is not None
                    and progress_interval_steps is not None
                    and (
                        self._step_index % progress_interval_steps == 0
                        or self._step_index == self.config.steps
                    )
                ):
                    progress_callback(self._step_index, self.config.steps)
                if checkpoint_manager is not None and checkpoint_manager.stop_requested:
                    if not checkpoint_manager.is_checkpoint_current(self._step_index):
                        checkpoint_started = time.perf_counter()
                        checkpoint_path = str(checkpoint_manager.save(self, composite))
                        self._timings["checkpointing_seconds"] += time.perf_counter() - checkpoint_started
                    break
            completed = self._step_index == self.config.steps
            composite.close(success=completed)
        except BaseException:
            composite.close(success=False)
            raise
        finally:
            self._event_sink = None
        return RunOutcome(
            summary=summary_sink.summary,
            step_count=summary_sink.step_count,
            audit_count=summary_sink.audit_count,
            artifacts=tuple(composite.artifacts),
            timings=dict(self._timings),
            completed=completed,
            completion_status="complete" if completed else "checkpointed",
            checkpoint=checkpoint_path,
        )

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
