"""Routing-invariant demand derived from per-class carried rates.

The Codex pipeline forecast *carried load per (upf, dnn, tac)* and then re-routed
it, which is circular: change the weights and the thing you forecast changes.

The quantity that does not move when you re-route is the offered demand of a
selection group::

    D[dnn, tac](t) = sum over upf of carried(upf, dnn, tac, t)
    L[upf](t)      = sum over (dnn, tac) of w[dnn, tac, upf] * D[dnn, tac](t)

Forecast ``D``, optimise ``w``, project ``L``.  This module builds ``D`` and the
current ``w`` from a class-rate window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

import numpy as np

from .sources import ClassRate


GroupId = tuple[str, int]  # (dnn, tac)


def group_id(dnn: str, tac: int) -> str:
    return f"tac-{tac}|{dnn}|dscp-0"


def parse_group_id(selection_id: str) -> GroupId:
    tac_part, dnn, _ = selection_id.split("|")
    return dnn, int(tac_part.removeprefix("tac-"))


@dataclass(slots=True)
class DemandCube:
    """Demand per selection group and carried load per UPF, on a regular grid."""

    times: list[datetime]
    step_seconds: int
    groups: list[GroupId]
    upfs: list[str]
    # demand[direction][group_index, time_index]
    demand: dict[str, np.ndarray]
    # carried[direction][upf_index, time_index]
    carried: dict[str, np.ndarray]
    # share[group_index, upf_index, time_index] -- fraction of group demand on that UPF
    share: np.ndarray
    observed_eligibility: dict[int, set[str]] = field(default_factory=dict)

    # ------------------------------------------------------------- accessors

    def __len__(self) -> int:
        return len(self.times)

    @property
    def latest_time(self) -> datetime | None:
        return self.times[-1] if self.times else None

    def group_series(self, group: GroupId, direction: str) -> np.ndarray:
        return self.demand[direction][self.groups.index(group)]

    def group_total(self, group: GroupId) -> np.ndarray:
        index = self.groups.index(group)
        return self.demand["ul"][index] + self.demand["dl"][index]

    def upf_total(self) -> np.ndarray:
        """Per-UPF total (UL+DL) load, shape (n_upf, n_time)."""
        return self.carried["ul"] + self.carried["dl"]

    def current_weights(self, lookback: int = 10) -> dict[str, dict[str, float]]:
        """Observed routing share per group, averaged over the last few samples.

        This is what the network is *actually* doing, independent of whatever
        the SMF weight table says -- useful when SMF has no weights configured.
        """
        window = max(1, min(lookback, len(self.times)))
        result: dict[str, dict[str, float]] = {}
        for gi, group in enumerate(self.groups):
            recent = self.share[gi, :, -window:]
            mean = recent.mean(axis=1) if recent.size else np.zeros(len(self.upfs))
            total = float(mean.sum())
            if total <= 0:
                continue
            result[group_id(*group)] = {
                upf: float(value / total) for upf, value in zip(self.upfs, mean) if value > 0
            }
        return result

    def latest_upf_load(self) -> dict[str, dict[str, float]]:
        if not self.times:
            return {upf: {"ul": 0.0, "dl": 0.0} for upf in self.upfs}
        return {
            upf: {"ul": float(self.carried["ul"][i, -1]), "dl": float(self.carried["dl"][i, -1])}
            for i, upf in enumerate(self.upfs)
        }

    def projected_upf_load(self, weights: dict[str, dict[str, float]],
                           demand_by_group: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
        """Apply a weight table to a forecast demand vector."""
        out = {upf: {"ul": 0.0, "dl": 0.0} for upf in self.upfs}
        for selection_id, per_upf in weights.items():
            demand = demand_by_group.get(selection_id)
            if not demand:
                continue
            total = sum(per_upf.values()) or 1.0
            for upf, weight in per_upf.items():
                if upf not in out:
                    continue
                fraction = weight / total
                out[upf]["ul"] += fraction * demand.get("ul", 0.0)
                out[upf]["dl"] += fraction * demand.get("dl", 0.0)
        return out

    def to_series_payload(self, limit: int = 400) -> dict[str, Any]:
        """Compact chart payload: per-UPF load and per-group demand over time."""
        take = slice(max(0, len(self.times) - limit), None)
        stamps = [item.isoformat().replace("+00:00", "Z") for item in self.times[take]]
        totals = self.upf_total()
        return {
            "times": stamps,
            "step_seconds": self.step_seconds,
            "unit": "pps",
            "upf_load": {
                upf: [round(float(value), 1) for value in totals[i][take]]
                for i, upf in enumerate(self.upfs)
            },
            "group_demand": {
                group_id(*group): [
                    round(float(value), 1)
                    for value in (self.demand["ul"][i] + self.demand["dl"][i])[take]
                ]
                for i, group in enumerate(self.groups)
            },
            "network_total": [
                round(float(value), 1) for value in totals.sum(axis=0)[take]
            ],
        }


def build_demand_cube(
    rows: Iterable[ClassRate],
    *,
    upfs: Sequence[str],
    step_seconds: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> DemandCube:
    """Aggregate class rates onto a regular grid and derive demand + shares.

    Samples are bin-averaged onto ``step_seconds``.  Bins with no sample are
    forward-filled from the previous bin, which is what a scrape gap actually
    means for a rate gauge -- far better than voiding the whole window as the
    previous implementation did.
    """
    rows = list(rows)
    upfs = list(upfs)
    if not rows:
        return DemandCube([], step_seconds, [], upfs,
                          {"ul": np.zeros((0, 0)), "dl": np.zeros((0, 0))},
                          {"ul": np.zeros((len(upfs), 0)), "dl": np.zeros((len(upfs), 0))},
                          np.zeros((0, len(upfs), 0)))

    start = start or min(item.t for item in rows)
    end = end or max(item.t for item in rows)
    start = _floor(start, step_seconds)
    end = _floor(end, step_seconds)
    n_time = int((end - start).total_seconds() // step_seconds) + 1
    times = [start + timedelta(seconds=step_seconds * i) for i in range(n_time)]

    groups = sorted({(item.dnn, item.tac) for item in rows})
    group_index = {group: i for i, group in enumerate(groups)}
    upf_index = {upf: i for i, upf in enumerate(upfs)}

    # accumulate sums and counts per (group, upf, bin) so we can bin-average
    shape = (len(groups), len(upfs), n_time)
    acc = {"ul": np.zeros(shape), "dl": np.zeros(shape)}
    counts = np.zeros(shape)
    observed: dict[int, set[str]] = {}

    for item in rows:
        gi = group_index.get((item.dnn, item.tac))
        ui = upf_index.get(item.upf)
        if gi is None or ui is None:
            continue
        offset = int((item.t - start).total_seconds() // step_seconds)
        if not 0 <= offset < n_time:
            continue
        acc["ul"][gi, ui, offset] += item.ul_pps
        acc["dl"][gi, ui, offset] += item.dl_pps
        counts[gi, ui, offset] += 1
        if item.ul_pps + item.dl_pps > _OBSERVED_FLOOR_PPS:
            observed.setdefault(item.tac, set()).add(item.upf)

    with np.errstate(invalid="ignore", divide="ignore"):
        for direction in ("ul", "dl"):
            acc[direction] = np.where(counts > 0, acc[direction] / np.maximum(counts, 1), np.nan)

    for direction in ("ul", "dl"):
        acc[direction] = _forward_fill(acc[direction])

    demand = {direction: acc[direction].sum(axis=1) for direction in ("ul", "dl")}
    carried = {direction: acc[direction].sum(axis=0) for direction in ("ul", "dl")}

    group_totals = demand["ul"] + demand["dl"]
    class_totals = acc["ul"] + acc["dl"]
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(group_totals[:, None, :] > 0,
                         class_totals / np.maximum(group_totals[:, None, :], 1e-9), 0.0)

    return DemandCube(
        times=times, step_seconds=step_seconds, groups=groups, upfs=upfs,
        demand=demand, carried=carried, share=share, observed_eligibility=observed,
    )


_OBSERVED_FLOOR_PPS = 10.0
"""A class carrying less than this is treated as noise, not evidence of eligibility."""


def _floor(stamp: datetime, step_seconds: int) -> datetime:
    epoch = int(stamp.timestamp() // step_seconds) * step_seconds
    return datetime.fromtimestamp(epoch, timezone.utc)


def _forward_fill(values: np.ndarray) -> np.ndarray:
    """Carry the last observed value across gaps along the time axis."""
    filled = values.copy()
    n_time = filled.shape[-1]
    if n_time == 0:
        return np.nan_to_num(filled)
    for index in range(1, n_time):
        column = filled[..., index]
        previous = filled[..., index - 1]
        filled[..., index] = np.where(np.isnan(column), previous, column)
    return np.nan_to_num(filled)
