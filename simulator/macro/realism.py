from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from statistics import NormalDist
from typing import Any

from .config import GroupProfile, TrafficModelV2
from .model import StepResult


@dataclass(frozen=True, slots=True)
class TelemetryObservationV2:
    upf_id: str
    ground_truth_ul_mbps: float
    ground_truth_dl_mbps: float
    observed_ul_mbps: float | None
    observed_dl_mbps: float | None
    counter_epoch: int
    restart_id: int
    quality_flags: tuple[str, ...]


class TrafficRealismRuntimeV2:
    """Causal state for the optional traffic-model/2.0 realism layer.

    It is deliberately absent for v1 scenarios, preserving the legacy random
    streams and artifact bytes. Population cohorts affect only the origin of
    future sessions; admitted sessions remain anchored to their selected UPF.
    """

    def __init__(
        self,
        config: TrafficModelV2,
        groups: tuple[GroupProfile, ...],
        streams: dict[str, random.Random],
    ) -> None:
        self.config = config
        self.groups = {group.key.selection_id: group for group in groups}
        self.streams = streams
        self.baseline_population = dict(config.aggregate_population_by_zone)
        self.population = dict(config.aggregate_population_by_zone)
        self.residual = {group_id: 0.0 for group_id in self.groups}
        self.burst_active = {group_id: False for group_id in self.groups}
        self.burst_multiplier = {group_id: 1.0 for group_id in self.groups}
        self.burst_dwell_steps = {group_id: 0 for group_id in self.groups}
        self.counter_epoch: dict[str, int] = {}
        self.restart_id: dict[str, int] = {}
        self.last_observed: dict[str, tuple[float, float]] = {}
        self.prepared_step: int | None = None
        self.telemetry: list[TelemetryObservationV2] = []
        self._stadium_by_group = {
            group_id: tuple(phase for phase in config.stadium_phases if group_id in phase.group_ids)
            for group_id in self.groups
        }
        self._uniform_rate_bins = {
            group_id: all(
                abs(item.probability - 1 / len(group.realism.rates.bins)) <= 1e-15
                for item in group.realism.rates.bins
            )
            for group_id, group in self.groups.items() if group.realism is not None
        }
        normal = NormalDist()
        self._holding_tables: dict[str, tuple[int, ...]] = {}
        for group_id, group in self.groups.items():
            if group.realism is None:
                continue
            model = group.realism.holding_time
            values = []
            for index in range(1024):
                q = (index + .5) / 1024
                if model.distribution == "lognormal":
                    raw = math.exp(math.log(model.scale_steps) + model.shape * normal.inv_cdf(q))
                else:
                    raw = model.scale_steps / ((1 - q) ** (1 / model.shape))
                values.append(min(model.max_steps, max(model.min_steps, int(round(raw)))))
            self._holding_tables[group_id] = tuple(values)

    @staticmethod
    def _allocate_exact(count: int, probabilities: dict[str, float]) -> dict[str, int]:
        raw = {zone: count * probability for zone, probability in probabilities.items()}
        allocated = {zone: int(math.floor(value)) for zone, value in raw.items()}
        remainder = count - sum(allocated.values())
        order = sorted(raw, key=lambda zone: (-(raw[zone] - allocated[zone]), zone))
        for zone in order[:remainder]:
            allocated[zone] += 1
        return allocated

    def _apply_mobility(self, step: int) -> None:
        phase = next((item for item in self.config.mobility_phases if item.start_step == step), None)
        if phase is None:
            return
        updated = {zone: 0 for zone in self.population}
        for origin, count in sorted(self.population.items()):
            allocated = self._allocate_exact(count, phase.transition_by_origin[origin])
            for destination, moved in allocated.items():
                updated[destination] += moved
        if sum(updated.values()) != sum(self.population.values()):
            raise AssertionError("v2 mobility population was not conserved")
        self.population = updated

    def prepare_step(self, step: int) -> None:
        if self.prepared_step == step:
            return
        self._apply_mobility(step)
        self.prepared_step = step

    def arrival_multiplier(self, group: GroupProfile, step: int) -> float:
        group_id = group.key.selection_id
        realism = group.realism
        if realism is None:
            raise AssertionError("v2 runtime received a group without realism configuration")
        demand_stream = self.streams[f"v2:demand:{group_id}"]
        burst_stream = self.streams[f"v2:burst:{group_id}"]
        model = realism.demand
        if model.innovation_sigma == 0 and model.burst_enter_probability == 0:
            population_factor = (
                self.population[group.key.zone] / self.baseline_population[group.key.zone]
                if self.baseline_population[group.key.zone] else 1.0
            )
            stadium_factor = 1.0
            for phase in self._stadium_by_group[group_id]:
                if phase.start_step <= step < phase.end_step:
                    stadium_factor *= phase.arrival_multiplier
            return population_factor * stadium_factor
        innovation = demand_stream.gauss(0.0, model.innovation_sigma)
        self.residual[group_id] = model.ar1_phi * self.residual[group_id] + innovation
        active = self.burst_active[group_id]
        if active and burst_stream.random() < model.burst_exit_probability:
            active = False
            self.burst_multiplier[group_id] = 1.0
            self.burst_dwell_steps[group_id] = 0
        elif not active and burst_stream.random() < model.burst_enter_probability:
            active = True
            tail = 1.0 + burst_stream.paretovariate(model.burst_pareto_alpha) - 1.0
            self.burst_multiplier[group_id] = min(model.burst_max_multiplier, tail)
            self.burst_dwell_steps[group_id] = 0
        if active:
            self.burst_dwell_steps[group_id] += 1
        self.burst_active[group_id] = active
        population_factor = (
            self.population[group.key.zone] / self.baseline_population[group.key.zone]
            if self.baseline_population[group.key.zone] else 1.0
        )
        stadium_factor = 1.0
        for phase in self._stadium_by_group[group_id]:
            if phase.start_step <= step < phase.end_step and group_id in phase.group_ids:
                stadium_factor *= phase.arrival_multiplier
        return max(0.0, population_factor * math.exp(self.residual[group_id])
                   * self.burst_multiplier[group_id] * stadium_factor)

    def sample_rates(self, group: GroupProfile) -> tuple[float, float]:
        realism = group.realism
        if realism is None:
            raise AssertionError("v2 runtime received a group without rate bins")
        draw = self.streams[f"v2:rates:{group.key.selection_id}"].random()
        if self._uniform_rate_bins[group.key.selection_id]:
            item = realism.rates.bins[min(len(realism.rates.bins) - 1, int(draw * len(realism.rates.bins)))]
            return item.ul_mbps, item.dl_mbps
        cumulative = 0.0
        for item in realism.rates.bins:
            cumulative += item.probability
            if draw <= cumulative:
                return item.ul_mbps, item.dl_mbps
        last = realism.rates.bins[-1]
        return last.ul_mbps, last.dl_mbps

    def expected_rates(self, group: GroupProfile) -> tuple[float, float]:
        realism = group.realism
        if realism is None:
            raise AssertionError("v2 runtime received a group without rate bins")
        return (
            sum(item.ul_mbps * item.probability for item in realism.rates.bins),
            sum(item.dl_mbps * item.probability for item in realism.rates.bins),
        )

    def current_arrival_multiplier(self, group: GroupProfile, step: int) -> float:
        group_id = group.key.selection_id
        population_factor = (
            self.population[group.key.zone] / self.baseline_population[group.key.zone]
            if self.baseline_population[group.key.zone] else 1.0
        )
        stadium_factor = 1.0
        for phase in self._stadium_by_group[group_id]:
            if phase.start_step <= step < phase.end_step and group_id in phase.group_ids:
                stadium_factor *= phase.arrival_multiplier
        return max(0.0, population_factor * math.exp(self.residual[group_id])
                   * self.burst_multiplier[group_id] * stadium_factor)

    def sample_lifetime(self, group: GroupProfile) -> int:
        realism = group.realism
        if realism is None:
            raise AssertionError("v2 runtime received a group without a holding-time model")
        model = realism.holding_time
        stream = self.streams[f"v2:holding:{group.key.selection_id}"]
        table = self._holding_tables[group.key.selection_id]
        return table[min(len(table) - 1, int(stream.random() * len(table)))]

    def observe(self, result: StepResult) -> tuple[TelemetryObservationV2, ...]:
        observations: list[TelemetryObservationV2] = []
        pathology = self.config.telemetry
        fault_free = not any((
            pathology.missing_scrape_probability, pathology.reset_probability,
            pathology.restart_probability, pathology.stale_probability,
        ))
        for upf in result.upfs:
            stream = self.streams[f"v2:telemetry:{upf.upf_id}"]
            self.counter_epoch.setdefault(upf.upf_id, 0)
            self.restart_id.setdefault(upf.upf_id, 0)
            truth_ul = upf.ul.offered_bytes * 8 / 1_000_000 / (
                (result.window_end - result.window_start).total_seconds()
            )
            truth_dl = upf.dl.offered_bytes * 8 / 1_000_000 / (
                (result.window_end - result.window_start).total_seconds()
            )
            flags: list[str] = []
            observed: tuple[float | None, float | None] = (truth_ul, truth_dl)
            if fault_free:
                self.last_observed[upf.upf_id] = (truth_ul, truth_dl)
            elif stream.random() < pathology.missing_scrape_probability:
                flags.append("missing_scrape")
                observed = (None, None)
            elif stream.random() < pathology.stale_probability and upf.upf_id in self.last_observed:
                flags.append("stale_sample")
                observed = self.last_observed[upf.upf_id]
            if not fault_free and stream.random() < pathology.reset_probability:
                self.counter_epoch[upf.upf_id] += 1
                flags.append("counter_reset")
            if not fault_free and stream.random() < pathology.restart_probability:
                self.restart_id[upf.upf_id] += 1
                flags.append("source_restart")
            if observed[0] is not None and observed[1] is not None:
                self.last_observed[upf.upf_id] = (observed[0], observed[1])
            observations.append(TelemetryObservationV2(
                upf_id=upf.upf_id,
                ground_truth_ul_mbps=truth_ul, ground_truth_dl_mbps=truth_dl,
                observed_ul_mbps=observed[0], observed_dl_mbps=observed[1],
                counter_epoch=self.counter_epoch[upf.upf_id], restart_id=self.restart_id[upf.upf_id],
                quality_flags=tuple(flags),
            ))
        self.telemetry = observations
        return tuple(observations)

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "schema_version": "traffic-realism-state/2.0",
            "population": dict(self.population),
            "residual": dict(self.residual),
            "burst_active": dict(self.burst_active),
            "burst_multiplier": dict(self.burst_multiplier),
            "burst_dwell_steps": dict(self.burst_dwell_steps),
            "counter_epoch": dict(self.counter_epoch),
            "restart_id": dict(self.restart_id),
            "last_observed": {key: list(value) for key, value in self.last_observed.items()},
            "prepared_step": self.prepared_step,
            "telemetry": [asdict(item) for item in self.telemetry],
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        if state.get("schema_version") != "traffic-realism-state/2.0":
            raise ValueError("unsupported traffic realism checkpoint state")
        self.population = {str(k): int(v) for k, v in state["population"].items()}
        if sum(self.population.values()) != 16_000_000:
            raise ValueError("checkpoint mobility population is not conserved")
        self.residual = {str(k): float(v) for k, v in state["residual"].items()}
        self.burst_active = {str(k): bool(v) for k, v in state["burst_active"].items()}
        self.burst_multiplier = {str(k): float(v) for k, v in state["burst_multiplier"].items()}
        self.burst_dwell_steps = {str(k): int(v) for k, v in state["burst_dwell_steps"].items()}
        self.counter_epoch = {str(k): int(v) for k, v in state["counter_epoch"].items()}
        self.restart_id = {str(k): int(v) for k, v in state["restart_id"].items()}
        self.last_observed = {str(k): (float(v[0]), float(v[1])) for k, v in state["last_observed"].items()}
        self.prepared_step = state["prepared_step"]
        self.telemetry = [TelemetryObservationV2(
            upf_id=item["upf_id"],
            ground_truth_ul_mbps=float(item["ground_truth_ul_mbps"]),
            ground_truth_dl_mbps=float(item["ground_truth_dl_mbps"]),
            observed_ul_mbps=None if item["observed_ul_mbps"] is None else float(item["observed_ul_mbps"]),
            observed_dl_mbps=None if item["observed_dl_mbps"] is None else float(item["observed_dl_mbps"]),
            counter_epoch=int(item["counter_epoch"]), restart_id=int(item["restart_id"]),
            quality_flags=tuple(item["quality_flags"]),
        ) for item in state.get("telemetry", [])]
