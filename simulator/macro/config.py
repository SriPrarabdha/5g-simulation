from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any

from schemas import GroupKey
from schemas.common import parse_utc


@dataclass(frozen=True, slots=True)
class GroupProfile:
    key: GroupKey
    arrivals_per_step: float
    lifetime_steps_min: int
    lifetime_steps_max: int
    offered_ul_mbps_per_session: float
    offered_dl_mbps_per_session: float
    eligible_upfs: tuple[str, ...]
    realism: "GroupRealismV2 | None" = None

    def __post_init__(self) -> None:
        if self.arrivals_per_step < 0:
            raise ValueError("arrivals_per_step must be non-negative")
        if self.lifetime_steps_min < 1 or self.lifetime_steps_max < self.lifetime_steps_min:
            raise ValueError("invalid lifetime range")
        if self.offered_ul_mbps_per_session < 0 or self.offered_dl_mbps_per_session < 0:
            raise ValueError("offered demand must be non-negative")
        if not self.eligible_upfs:
            raise ValueError(f"group {self.key.selection_id} must declare eligible UPFs")


@dataclass(frozen=True, slots=True)
class HoldingTimeV2:
    distribution: str
    shape: float
    scale_steps: float
    min_steps: int
    max_steps: int

    def __post_init__(self) -> None:
        if self.distribution not in {"lognormal", "pareto"}:
            raise ValueError("v2 holding-time distribution must be lognormal or pareto")
        if self.shape <= 0 or self.scale_steps <= 0:
            raise ValueError("v2 holding-time shape and scale must be positive")
        if self.min_steps < 1 or self.max_steps < self.min_steps:
            raise ValueError("invalid bounded v2 holding-time range")


@dataclass(frozen=True, slots=True)
class DemandResidualV2:
    ar1_phi: float
    innovation_sigma: float
    burst_enter_probability: float
    burst_exit_probability: float
    burst_pareto_alpha: float
    burst_max_multiplier: float

    def __post_init__(self) -> None:
        if not -0.999 < self.ar1_phi < 0.999:
            raise ValueError("v2 AR(1) coefficient must lie in (-0.999, 0.999)")
        if self.innovation_sigma < 0:
            raise ValueError("v2 innovation sigma must be non-negative")
        if not 0 <= self.burst_enter_probability <= 1 or not 0 <= self.burst_exit_probability <= 1:
            raise ValueError("v2 burst transition probabilities must lie in [0, 1]")
        if self.burst_pareto_alpha <= 1 or self.burst_max_multiplier < 1:
            raise ValueError("v2 burst tail must have alpha > 1 and a multiplier bound >= 1")


@dataclass(frozen=True, slots=True)
class JointRateBinV2:
    ul_mbps: float
    dl_mbps: float
    probability: float

    def __post_init__(self) -> None:
        if self.ul_mbps < 0 or self.dl_mbps < 0 or self.probability <= 0:
            raise ValueError("v2 joint rate bins require bounded non-negative rates and positive mass")


@dataclass(frozen=True, slots=True)
class JointRateModelV2:
    correlation: float
    ul_median_mbps: float
    dl_median_mbps: float
    ul_sigma: float
    dl_sigma: float
    ul_min_mbps: float
    ul_max_mbps: float
    dl_min_mbps: float
    dl_max_mbps: float
    bins: tuple[JointRateBinV2, ...]

    def __post_init__(self) -> None:
        if len(self.bins) != 16:
            raise ValueError("traffic-model/2.0 requires exactly sixteen joint UL/DL rate bins")
        if not -0.999 <= self.correlation <= 0.999:
            raise ValueError("v2 UL/DL Gaussian-copula correlation is invalid")
        if min(self.ul_median_mbps, self.dl_median_mbps) <= 0:
            raise ValueError("v2 median rates must be positive")
        if min(self.ul_sigma, self.dl_sigma) < 0:
            raise ValueError("v2 lognormal sigmas must be non-negative")
        if not 0 <= self.ul_min_mbps <= self.ul_max_mbps:
            raise ValueError("v2 UL rate bounds are invalid")
        if not 0 <= self.dl_min_mbps <= self.dl_max_mbps:
            raise ValueError("v2 DL rate bounds are invalid")
        if abs(sum(item.probability for item in self.bins) - 1.0) > 1e-12:
            raise ValueError("v2 joint rate-bin probabilities must sum to one")
        for item in self.bins:
            if not self.ul_min_mbps <= item.ul_mbps <= self.ul_max_mbps:
                raise ValueError("v2 UL rate bin falls outside its configured bounds")
            if not self.dl_min_mbps <= item.dl_mbps <= self.dl_max_mbps:
                raise ValueError("v2 DL rate bin falls outside its configured bounds")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JointRateModelV2":
        correlation = float(data["correlation"])
        ul = data["ul_lognormal"]
        dl = data["dl_lognormal"]
        explicit = data.get("bins")
        if explicit is None:
            normal = NormalDist()
            bins: list[JointRateBinV2] = []
            # A fixed copula lattice is intentionally finite: the stochastic
            # simulator can create no more than sixteen rate cohorts per group.
            permutation = (5, 13, 1, 9, 7, 15, 3, 11, 4, 12, 0, 8, 6, 14, 2, 10)
            for index in range(16):
                z_ul = normal.inv_cdf((index + 0.5) / 16)
                z_independent = normal.inv_cdf((permutation[index] + 0.5) / 16)
                z_dl = correlation * z_ul + math.sqrt(1 - correlation * correlation) * z_independent
                ul_rate = float(ul["median_mbps"]) * math.exp(float(ul["sigma"]) * z_ul)
                dl_rate = float(dl["median_mbps"]) * math.exp(float(dl["sigma"]) * z_dl)
                bins.append(JointRateBinV2(
                    ul_mbps=min(float(ul["max_mbps"]), max(float(ul["min_mbps"]), ul_rate)),
                    dl_mbps=min(float(dl["max_mbps"]), max(float(dl["min_mbps"]), dl_rate)),
                    probability=1 / 16,
                ))
        else:
            bins = [JointRateBinV2(**item) for item in explicit]
        return cls(
            correlation=correlation,
            ul_median_mbps=float(ul["median_mbps"]),
            dl_median_mbps=float(dl["median_mbps"]),
            ul_sigma=float(ul["sigma"]), dl_sigma=float(dl["sigma"]),
            ul_min_mbps=float(ul["min_mbps"]), ul_max_mbps=float(ul["max_mbps"]),
            dl_min_mbps=float(dl["min_mbps"]), dl_max_mbps=float(dl["max_mbps"]),
            bins=tuple(bins),
        )


@dataclass(frozen=True, slots=True)
class GroupRealismV2:
    holding_time: HoldingTimeV2
    demand: DemandResidualV2
    rates: JointRateModelV2

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupRealismV2":
        return cls(
            holding_time=HoldingTimeV2(**data["holding_time"]),
            demand=DemandResidualV2(**data["demand"]),
            rates=JointRateModelV2.from_dict(data["joint_rates"]),
        )


@dataclass(frozen=True, slots=True)
class MobilityPhaseV2:
    start_step: int
    transition_by_origin: dict[str, dict[str, float]]


@dataclass(frozen=True, slots=True)
class StadiumPhaseV2:
    name: str
    start_step: int
    end_step: int
    group_ids: tuple[str, ...]
    arrival_multiplier: float
    forecast_hint_multiplier: float = 1.0
    known_at_step: int = 0
    forecast_start_step: int | None = None
    forecast_end_step: int | None = None


@dataclass(frozen=True, slots=True)
class TelemetryPathologyV2:
    missing_scrape_probability: float = 0.0
    reset_probability: float = 0.0
    restart_probability: float = 0.0
    stale_probability: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.missing_scrape_probability, self.reset_probability,
            self.restart_probability, self.stale_probability,
        )
        if any(not 0 <= value <= 1 for value in values):
            raise ValueError("v2 telemetry pathology probabilities must lie in [0, 1]")


@dataclass(frozen=True, slots=True)
class TrafficModelV2:
    schema_version: str
    aggregate_population_by_zone: dict[str, int]
    mobility_phases: tuple[MobilityPhaseV2, ...]
    stadium_phases: tuple[StadiumPhaseV2, ...]
    telemetry: TelemetryPathologyV2

    def __post_init__(self) -> None:
        if self.schema_version != "traffic-model/2.0":
            raise ValueError("the optional realism block must use traffic-model/2.0")
        if not self.aggregate_population_by_zone or any(
            not zone or int(count) < 0 for zone, count in self.aggregate_population_by_zone.items()
        ):
            raise ValueError("v2 aggregate zone populations are invalid")
        if sum(self.aggregate_population_by_zone.values()) != 16_000_000:
            raise ValueError("traffic-model/2.0 zone populations must sum exactly to 16,000,000")
        zones = set(self.aggregate_population_by_zone)
        previous = -1
        for phase in self.mobility_phases:
            if phase.start_step <= previous:
                raise ValueError("v2 mobility phases must have strictly increasing start steps")
            previous = phase.start_step
            if set(phase.transition_by_origin) != zones:
                raise ValueError("each v2 transition matrix must include every origin zone")
            for origin, row in phase.transition_by_origin.items():
                if set(row) != zones or any(value < 0 for value in row.values()):
                    raise ValueError(f"v2 transition row for {origin} must cover every zone")
                if abs(sum(row.values()) - 1.0) > 1e-12:
                    raise ValueError(f"v2 transition row for {origin} must sum to one")
        allowed = {"ingress", "kickoff", "match", "halftime_upload", "final_whistle", "egress"}
        for phase in self.stadium_phases:
            if phase.name not in allowed or not 0 <= phase.start_step < phase.end_step:
                raise ValueError("invalid correlated stadium phase")
            if phase.arrival_multiplier < 0 or not phase.group_ids:
                raise ValueError("stadium phases require groups and a non-negative multiplier")
            if phase.forecast_hint_multiplier <= 0:
                raise ValueError("stadium calendar hints must be positive")
            forecast_start = (
                phase.start_step if phase.forecast_start_step is None
                else phase.forecast_start_step
            )
            forecast_end = (
                phase.end_step if phase.forecast_end_step is None
                else phase.forecast_end_step
            )
            if not 0 <= phase.known_at_step <= forecast_start < forecast_end:
                raise ValueError("stadium calendar timing is invalid")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrafficModelV2":
        return cls(
            schema_version=data["schema_version"],
            aggregate_population_by_zone={str(k): int(v) for k, v in data["aggregate_population_by_zone"].items()},
            mobility_phases=tuple(MobilityPhaseV2(
                start_step=int(item["start_step"]),
                transition_by_origin={
                    str(origin): {str(destination): float(probability) for destination, probability in row.items()}
                    for origin, row in item["transition_by_origin"].items()
                },
            ) for item in data.get("mobility_phases", [])),
            stadium_phases=tuple(StadiumPhaseV2(
                name=item["name"], start_step=int(item["start_step"]), end_step=int(item["end_step"]),
                group_ids=tuple(item["group_ids"]), arrival_multiplier=float(item["arrival_multiplier"]),
                forecast_hint_multiplier=float(item.get("forecast_hint_multiplier", 1.0)),
                known_at_step=int(item.get("known_at_step", 0)),
                forecast_start_step=(
                    int(item["forecast_start_step"])
                    if item.get("forecast_start_step") is not None else None
                ),
                forecast_end_step=(
                    int(item["forecast_end_step"])
                    if item.get("forecast_end_step") is not None else None
                ),
            ) for item in data.get("stadium_phases", [])),
            telemetry=TelemetryPathologyV2(**data.get("telemetry", {})),
        )


@dataclass(frozen=True, slots=True)
class UPFProfile:
    upf_id: str
    zone: str
    capacity_ul_mbps: float
    capacity_dl_mbps: float
    safe_utilization_ul: float
    safe_utilization_dl: float
    session_capacity: int
    session_safe_utilization: float
    queue_limit_seconds: float
    path_latency_ms_by_zone: dict[str, float]

    def __post_init__(self) -> None:
        if self.capacity_ul_mbps <= 0 or self.capacity_dl_mbps <= 0:
            raise ValueError("UPF directional capacity must be positive")
        if not 0 < self.safe_utilization_ul <= 1 or not 0 < self.safe_utilization_dl <= 1:
            raise ValueError("safe utilization must be in (0, 1]")
        if self.session_capacity <= 0 or not 0 < self.session_safe_utilization <= 1:
            raise ValueError("session limits must be positive")
        if self.queue_limit_seconds < 0:
            raise ValueError("queue_limit_seconds must be non-negative")


@dataclass(frozen=True, slots=True)
class ScenarioEvent:
    step: int
    event_type: str
    upf_id: str | None = None
    group_id: str | None = None
    ul_factor: float | None = None
    dl_factor: float | None = None
    health: str | None = None
    arrival_factor: float | None = None
    zone: str | None = None
    latency_ms: float | None = None
    known_at_step: int | None = None
    forecast_hint_multiplier: float | None = None

    def __post_init__(self) -> None:
        if self.step < 0:
            raise ValueError("event step must be non-negative")
        if self.known_at_step is not None and not 0 <= self.known_at_step <= self.step:
            raise ValueError("known_at_step must be between zero and the event step")
        if self.forecast_hint_multiplier is not None:
            if self.event_type != "arrival_factor":
                raise ValueError("forecast hints are only valid for arrival-factor events")
            if self.known_at_step is None:
                raise ValueError("forecast hints require known_at_step")
            if self.forecast_hint_multiplier <= 0:
                raise ValueError("forecast_hint_multiplier must be positive")
        if self.event_type not in {"capacity_factor", "health", "arrival_factor", "path_latency"}:
            raise ValueError(f"unsupported event type: {self.event_type}")
        if self.event_type == "capacity_factor":
            if not self.upf_id:
                raise ValueError("capacity event requires upf_id")
            if self.ul_factor is None and self.dl_factor is None:
                raise ValueError("capacity event requires a directional factor")
            if any(value is not None and value < 0 for value in (self.ul_factor, self.dl_factor)):
                raise ValueError("capacity factors must be non-negative")
        if self.event_type == "health":
            if not self.upf_id or self.health not in {"healthy", "degraded", "unavailable", "unknown"}:
                raise ValueError("health event requires upf_id and a valid state")
        if self.event_type == "arrival_factor":
            if not self.group_id or self.arrival_factor is None or self.arrival_factor < 0:
                raise ValueError("arrival event requires group_id and a non-negative factor")
        if self.event_type == "path_latency":
            if not self.upf_id or not self.zone or self.latency_ms is None or self.latency_ms < 0:
                raise ValueError("path-latency event requires upf_id, zone, and non-negative latency_ms")


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    scenario_id: str
    seed: int
    start_time: datetime
    steps: int
    step_seconds: int = 30
    decision_interval_steps: int = 20
    selection_audit_stride: int = 1
    primary_overload_metric: str = "overload_area_seconds.ul"
    groups: tuple[GroupProfile, ...] = field(default_factory=tuple)
    upfs: tuple[UPFProfile, ...] = field(default_factory=tuple)
    events: tuple[ScenarioEvent, ...] = field(default_factory=tuple)
    traffic_model: TrafficModelV2 | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_time", parse_utc(self.start_time))
        if not self.scenario_id or self.steps <= 0 or self.step_seconds <= 0:
            raise ValueError("scenario identity, steps, and step_seconds must be positive")
        if self.decision_interval_steps <= 0:
            raise ValueError("decision_interval_steps must be positive")
        if self.selection_audit_stride <= 0:
            raise ValueError("selection_audit_stride must be positive")
        if self.primary_overload_metric not in {
            "overload_area_seconds.ul", "overload_area_seconds.dl",
            "overload_duration_seconds.ul", "overload_duration_seconds.dl",
        }:
            raise ValueError("primary_overload_metric must select one directional overload metric")
        upf_ids = {upf.upf_id for upf in self.upfs}
        if len(upf_ids) != len(self.upfs) or not upf_ids:
            raise ValueError("UPF IDs must be unique and non-empty")
        group_ids = {group.key.selection_id for group in self.groups}
        if len(group_ids) != len(self.groups) or not group_ids:
            raise ValueError("selection groups must be unique and non-empty")
        for group in self.groups:
            unknown = set(group.eligible_upfs) - upf_ids
            if unknown:
                raise ValueError(f"group {group.key.selection_id} references unknown UPFs: {sorted(unknown)}")
        for event in self.events:
            if event.upf_id is not None and event.upf_id not in upf_ids:
                raise ValueError(f"event references unknown UPF: {event.upf_id}")
            if event.group_id is not None and event.group_id not in group_ids:
                raise ValueError(f"event references unknown group: {event.group_id}")
            if event.step >= self.steps:
                raise ValueError("event step falls outside the scenario")
        if self.traffic_model is not None:
            zones = set(self.traffic_model.aggregate_population_by_zone)
            unknown_group_zones = {group.key.zone for group in self.groups} - zones
            if unknown_group_zones:
                raise ValueError(f"v2 groups reference zones without population cohorts: {sorted(unknown_group_zones)}")
            for phase in self.traffic_model.mobility_phases:
                if phase.start_step >= self.steps:
                    raise ValueError("v2 mobility phase falls outside the scenario")
            for phase in self.traffic_model.stadium_phases:
                unknown = set(phase.group_ids) - group_ids
                if unknown:
                    raise ValueError(f"v2 stadium phase references unknown groups: {sorted(unknown)}")
                if phase.end_step > self.steps:
                    raise ValueError("v2 stadium phase falls outside the scenario")
            missing_realism = [group.key.selection_id for group in self.groups if group.realism is None]
            if missing_realism:
                raise ValueError(f"every traffic-model/2.0 group requires a realism block: {missing_realism[:3]}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScenarioConfig":
        traffic_model = (
            TrafficModelV2.from_dict(data["traffic_model"])
            if data.get("traffic_model") is not None else None
        )
        groups = tuple(
            GroupProfile(
                key=GroupKey.from_dict(item["key"]), arrivals_per_step=item["arrivals_per_step"],
                lifetime_steps_min=item["lifetime_steps"]["min"],
                lifetime_steps_max=item["lifetime_steps"]["max"],
                offered_ul_mbps_per_session=item["offered_mbps_per_session"]["ul"],
                offered_dl_mbps_per_session=item["offered_mbps_per_session"]["dl"],
                eligible_upfs=tuple(item["eligible_upfs"]),
                realism=GroupRealismV2.from_dict(item["realism"]) if item.get("realism") else None,
            ) for item in data["groups"]
        )
        upfs = tuple(
            UPFProfile(
                upf_id=item["upf_id"], zone=item["zone"],
                capacity_ul_mbps=item["capacity_mbps"]["ul"],
                capacity_dl_mbps=item["capacity_mbps"]["dl"],
                safe_utilization_ul=item["safe_utilization"]["ul"],
                safe_utilization_dl=item["safe_utilization"]["dl"],
                session_capacity=item["session_capacity"],
                session_safe_utilization=item["session_safe_utilization"],
                queue_limit_seconds=item.get("queue_limit_seconds", 0),
                path_latency_ms_by_zone=dict(item.get("path_latency_ms_by_zone", {})),
            ) for item in data["upfs"]
        )
        events = tuple(ScenarioEvent(**item) for item in data.get("events", []))
        return cls(
            scenario_id=data["scenario_id"], seed=data["seed"], start_time=data["start_time"],
            steps=data["steps"], step_seconds=data.get("step_seconds", 30),
            decision_interval_steps=data.get("decision_interval_steps", 20),
            selection_audit_stride=data.get("selection_audit_stride", 1),
            primary_overload_metric=data.get("primary_overload_metric", "overload_area_seconds.ul"),
            groups=groups, upfs=upfs, events=events, traffic_model=traffic_model,
        )


def load_scenario(path: str | Path) -> ScenarioConfig:
    with Path(path).open(encoding="utf-8") as stream:
        return ScenarioConfig.from_dict(json.load(stream))
