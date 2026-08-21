"""Shared causal metadata construction for live and offline demand observations."""

from __future__ import annotations

import hashlib
import random
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from schemas.common import parse_utc


def _at(config: Any, step: int) -> datetime:
    return parse_utc(config.start_time) + timedelta(seconds=step * config.step_seconds)


def causal_observation_metadata(
    config: Any,
    *,
    group_id: str,
    bucket_start_step: int,
    bucket_end_step: int,
    arrivals_by_group: Mapping[str, float],
    prior_arrivals: Sequence[float],
    telemetry_flags: Sequence[str] = (),
    telemetry_age_seconds: float = 0.0,
) -> dict[str, Any]:
    """Return features whose availability is no later than this bucket's end.

    ``bucket_end_step`` is exclusive. Unannounced simulator events therefore
    become observable only after the first completed bucket containing them.
    Scheduled traffic phases are part of the scenario contract and are known
    in advance; latent traffic-v2 burst state is never inspected.
    """

    group_by_id = {group.key.selection_id: group for group in config.groups}
    group = group_by_id[group_id]
    end_time = _at(config, bucket_end_step)
    same_zone = sum(
        float(arrivals_by_group.get(other_id, 0.0))
        for other_id, other in group_by_id.items()
        if other.key.zone == group.key.zone
    )
    neighboring = sum(
        float(value) for other_id, value in arrivals_by_group.items()
        if other_id != group_id
    )

    event_features = {
        "same_zone_aggregate": same_zone,
        "neighboring_group_aggregate": neighboring,
        "known_event_phase": 0.0,
        "known_event_lead_minutes": 0.0,
        "time_since_observable_anomaly_minutes": 0.0,
    }
    available_at = {name: end_time for name in event_features}
    regime = "normal"

    traffic = getattr(config, "traffic_model", None)
    phases = (
        [phase for phase in traffic.stadium_phases if group_id in phase.group_ids]
        if traffic is not None else []
    )
    actual_active_phases = [
        phase for phase in phases
        if phase.start_step < bucket_end_step and phase.end_step > bucket_start_step
    ]
    if actual_active_phases:
        regime = "scheduled_event"
    known_phases = [
        phase for phase in phases if phase.known_at_step < bucket_end_step
    ]
    active_calendar_phases = [
        phase for phase in known_phases
        if (phase.forecast_start_step or phase.start_step) < bucket_end_step
        and (phase.forecast_end_step or phase.end_step) > bucket_start_step
    ]
    if active_calendar_phases:
        event_features["known_event_phase"] = max(
            float(phase.forecast_hint_multiplier) for phase in active_calendar_phases
        )
        available_at["known_event_phase"] = max(
            _at(config, phase.known_at_step) for phase in active_calendar_phases
        )
    future_phase_steps = [
        phase.forecast_start_step or phase.start_step
        for phase in known_phases
        if (phase.forecast_start_step or phase.start_step) >= bucket_end_step
    ]
    if future_phase_steps:
        event_features["known_event_lead_minutes"] = (
            (min(future_phase_steps) - bucket_end_step) * config.step_seconds / 60
        )

    # Explicit scheduled arrival events may be used at their declared
    # availability time. Unannounced capacity/health changes are exposed only
    # after a completed observation contains the changed state.
    latest_unannounced: dict[tuple[str, str], Any] = {}
    for event in config.events:
        if event.event_type == "arrival_factor" and event.group_id == group_id:
            if event.known_at_step is not None and event.known_at_step < bucket_end_step:
                lead = max(0.0, (event.step - bucket_end_step) * config.step_seconds / 60)
                if lead and (
                    event_features["known_event_lead_minutes"] == 0.0
                    or lead < event_features["known_event_lead_minutes"]
                ):
                    event_features["known_event_lead_minutes"] = lead
                if event.step < bucket_end_step:
                    event_features["known_event_phase"] = float(
                        event.forecast_hint_multiplier or 1.0
                    )
                    regime = "scheduled_event"
        elif (
            event.event_type in {"capacity_factor", "health"}
            and event.step < bucket_end_step
            and event.upf_id in group.eligible_upfs
            and event.known_at_step is None
        ):
            latest_unannounced[(event.upf_id or "", event.event_type)] = event

    anomalous_events = []
    recovery_events = []
    for event in latest_unannounced.values():
        is_recovery = (
            (event.event_type == "health" and event.health in {"healthy", "degraded"})
            or (
                event.event_type == "capacity_factor"
                and (event.ul_factor is None or event.ul_factor >= 0.999999)
                and (event.dl_factor is None or event.dl_factor >= 0.999999)
            )
        )
        (recovery_events if is_recovery else anomalous_events).append(event)
    if anomalous_events:
        latest = max(anomalous_events, key=lambda item: item.step)
        event_features["time_since_observable_anomaly_minutes"] = max(
            config.step_seconds / 60,
            (bucket_end_step - latest.step - 1) * config.step_seconds / 60,
        )
        if regime == "normal":
            regime = "outage"
    elif any(event.step >= bucket_start_step for event in recovery_events):
        if regime == "normal":
            regime = "recovery"

    if regime == "normal" and len(prior_arrivals) >= 3:
        baseline = statistics.median(float(value) for value in prior_arrivals[-12:])
        if float(arrivals_by_group.get(group_id, 0.0)) >= 2.0 * max(1.0, baseline):
            regime = "detected_surge"
            event_features["time_since_observable_anomaly_minutes"] = 0.0

    flags = set(telemetry_flags)
    telemetry_missing = "missing_scrape" in flags
    counter_reset = "counter_reset" in flags or "source_restart" in flags
    quality_flags = ["causal_metadata_v1"]
    quality_flags.extend(sorted(flags))
    return {
        "quality_flags": tuple(quality_flags),
        "regime": regime,
        "event_features": event_features,
        "available_at": available_at,
        "telemetry_age_seconds": max(0.0, float(telemetry_age_seconds)),
        "telemetry_missing": telemetry_missing,
        "counter_reset": counter_reset,
    }


@dataclass
class TelemetryQualityReplay:
    """Replay traffic-v2 telemetry pathology RNG without reading latent demand."""

    config: Any

    def __post_init__(self) -> None:
        self._streams = {}
        self._seen = set()
        self._last_fresh_step = {}
        for upf in self.config.upfs:
            name = f"v2:telemetry:{upf.upf_id}"
            digest = hashlib.sha256(f"{self.config.seed}:{name}".encode()).digest()
            self._streams[upf.upf_id] = random.Random(int.from_bytes(digest[:8], "big"))

    def observe_step(self, step: int) -> dict[str, tuple[tuple[str, ...], float]]:
        traffic = getattr(self.config, "traffic_model", None)
        if traffic is None:
            return {upf.upf_id: ((), 0.0) for upf in self.config.upfs}
        pathology = traffic.telemetry
        result: dict[str, tuple[tuple[str, ...], float]] = {}
        for upf in self.config.upfs:
            upf_id = upf.upf_id
            stream = self._streams[upf_id]
            flags: set[str] = set()
            missing = stream.random() < pathology.missing_scrape_probability
            stale = False
            if missing:
                flags.add("missing_scrape")
            else:
                stale = (
                    stream.random() < pathology.stale_probability
                    and upf_id in self._seen
                )
                if stale:
                    flags.add("stale_sample")
            if stream.random() < pathology.reset_probability:
                flags.add("counter_reset")
            if stream.random() < pathology.restart_probability:
                flags.add("source_restart")
            if not missing and not stale:
                self._seen.add(upf_id)
                self._last_fresh_step[upf_id] = step
            previous = self._last_fresh_step.get(upf_id, step)
            age = max(0, step - previous) * self.config.step_seconds
            result[upf_id] = (tuple(sorted(flags)), age)
        return result
