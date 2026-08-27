"""Turn the closed loop's JSONL history into presentation plots.

    python scripts/plot_autopilot_history.py
    python scripts/plot_autopilot_history.py --history logs/history --out output/autopilot

Reads what ``demo_api.cdot_live.history`` wrote while the autopilot ran and
draws four figures:

1. **Carried load per UPF** against the capacity line, with the control cycles
   marked -- the "did steering flatten the peaks" picture.
2. **Allocated share per UPF over time** -- what the optimizer actually decided
   at each ten-minute cycle, stacked to 100%.
3. **Hottest UPF, before vs after** each solve, with the reduction it bought.
4. **Prometheus health** -- poll latency and the outcome of every cycle, so a
   gap in the load curve can be attributed to an outage rather than to quiet
   traffic.

Nothing here re-computes anything: every number comes straight out of the
history, so a plot can never disagree with what the loop actually did.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

UPF_COLOURS = ["#006f8e", "#6558b8", "#b7791f", "#1f8a5f", "#a03050"]
OUTCOME_COLOURS = {
    "applied": "#1f8a5f", "no_change": "#7f8c8d", "dry_run": "#b7791f",
    "held": "#e08e3c", "failed": "#c2413b",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # A process killed mid-write can leave one torn line; the rest
                # of the file is still perfectly good evidence.
                continue
    return rows


def when(record: dict[str, Any], key: str = "ts") -> datetime:
    return datetime.fromisoformat(str(record[key]).replace("Z", "+00:00"))


def unit_scale(unit: str) -> tuple[float, str]:
    """Plot bytes/s in MB/s; leave packets/second alone."""
    if unit and "byte" in unit.lower():
        return 1e6, "MB/s"
    return 1.0, unit or "pps"


def _bar_width(times: list[datetime]) -> float:
    """Bar width in matplotlib date units, which are *days*.

    A fixed width is wrong at every cadence but one: 0.004 days is 5.7 minutes,
    which merges six-second rehearsal cycles into a solid block and leaves
    ten-minute production cycles as slivers.  Take it from the data instead.
    """
    if len(times) < 2:
        return 0.002
    gaps = sorted(
        (later - earlier).total_seconds()
        for earlier, later in zip(times, times[1:])
        if later > earlier
    )
    if not gaps:
        return 0.002
    median = gaps[len(gaps) // 2]
    return max(median * 0.7 / 86400.0, 1e-5)


def _style(axis) -> None:
    axis.grid(True, alpha=.25, linewidth=.7)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))


def _cycle_marks(axis, cycles: list[dict[str, Any]]) -> None:
    for cycle in cycles:
        axis.axvline(
            when(cycle), color=OUTCOME_COLOURS.get(cycle.get("outcome"), "#999"),
            alpha=.5, linewidth=1, linestyle="--" if cycle.get("outcome") != "applied" else "-",
        )


def plot_load(telemetry, cycles, runs, out: Path) -> Path | None:
    if not telemetry:
        return None
    unit = telemetry[-1].get("unit") or "pps"
    scale, label = unit_scale(unit)
    times = [when(row) for row in telemetry]
    per_upf: dict[str, list[float]] = defaultdict(list)
    for row in telemetry:
        for upf, load in (row.get("upf_load") or {}).items():
            per_upf[upf].append(load.get("total", 0.0) / scale)

    figure, axis = plt.subplots(figsize=(13, 5.5))
    for index, upf in enumerate(sorted(per_upf)):
        values = per_upf[upf]
        axis.plot(times[:len(values)], values, linewidth=1.6,
                  color=UPF_COLOURS[index % len(UPF_COLOURS)], label=upf)
    capacity = None
    for run in runs:
        capacity = (run.get("capacity") or {}).get("per_upf") or capacity
    if capacity:
        axis.axhline(capacity / scale, color="#c2413b", linewidth=1.8, label="capacity")
        axis.axhspan(capacity / scale, axis.get_ylim()[1], color="#c2413b", alpha=.06)
    _cycle_marks(axis, cycles)
    axis.set_title("Carried load per UPF, with each control cycle marked", fontsize=13)
    axis.set_ylabel(label)
    axis.legend(ncol=6, fontsize=9, frameon=False)
    _style(axis)
    figure.tight_layout()
    path = out / "01-carried-load.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def plot_allocation(cycles, out: Path) -> Path | None:
    decided = [c for c in cycles if c.get("upf_share")]
    if not decided:
        return None
    times = [when(c) for c in decided]
    upfs = sorted({upf for c in decided for upf in c["upf_share"]})
    series = {upf: [c["upf_share"].get(upf, 0.0) * 100 for c in decided] for upf in upfs}

    figure, axis = plt.subplots(figsize=(13, 5))
    axis.stackplot(
        times, *[series[upf] for upf in upfs], labels=upfs,
        colors=[UPF_COLOURS[i % len(UPF_COLOURS)] for i in range(len(upfs))], alpha=.85,
    )
    axis.set_ylim(0, 100)
    axis.set_title("Share of traffic the optimizer allocated to each UPF, per cycle", fontsize=13)
    axis.set_ylabel("% of tuples' weight")
    axis.legend(ncol=6, fontsize=9, frameon=False, loc="lower left")
    _style(axis)
    figure.tight_layout()
    path = out / "02-allocated-share.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def plot_peak_reduction(cycles, out: Path) -> Path | None:
    solved = [c for c in cycles if c.get("hottest_baseline_load") is not None]
    if not solved:
        return None
    unit = solved[-1].get("unit") or "pps"
    scale, label = unit_scale(unit)
    times = [when(c) for c in solved]
    before = [c["hottest_baseline_load"] / scale for c in solved]
    after = [(c.get("hottest_projected_load") or 0) / scale for c in solved]

    figure, (top, bottom) = plt.subplots(
        2, 1, figsize=(13, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )
    top.plot(times, before, marker="o", markersize=4, linewidth=1.6,
             color="#c2413b", label="hottest UPF, current routing")
    top.plot(times, after, marker="o", markersize=4, linewidth=1.6,
             color="#1f8a5f", label="hottest UPF, after the solve")
    top.fill_between(times, after, before, color="#1f8a5f", alpha=.12)
    top.set_ylabel(label)
    top.set_title("Peak load the solve removed, cycle by cycle", fontsize=13)
    top.legend(fontsize=9, frameon=False)
    _style(top)

    reduction = [(c.get("peak_reduction") or 0) * 100 for c in solved]
    bottom.bar(times, reduction, width=_bar_width(times), color="#006f8e")
    bottom.set_ylabel("% lower")
    bottom.set_xlabel("time (UTC)")
    _style(bottom)
    figure.tight_layout()
    path = out / "03-peak-reduction.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def plot_health(telemetry, cycles, out: Path) -> Path | None:
    if not telemetry:
        return None
    times = [when(row) for row in telemetry]
    latency = [row.get("latency_ms") or 0 for row in telemetry]
    failed = [(when(r), r.get("latency_ms") or 0) for r in telemetry if not r.get("ok")]

    figure, (top, bottom) = plt.subplots(
        2, 1, figsize=(13, 6.5), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )
    top.plot(times, latency, linewidth=1.2, color="#006f8e", label="poll latency")
    if failed:
        top.scatter([f[0] for f in failed], [f[1] for f in failed],
                    color="#c2413b", s=18, zorder=3, label="failed poll")
    ok = sum(1 for r in telemetry if r.get("ok"))
    top.set_title(
        f"Prometheus health — {ok}/{len(telemetry)} polls answered "
        f"({100 * ok / len(telemetry):.1f}%)", fontsize=13,
    )
    top.set_ylabel("ms")
    top.legend(fontsize=9, frameon=False)
    _style(top)

    seen: set[str] = set()
    for cycle in cycles:
        outcome = cycle.get("outcome", "?")
        bottom.scatter(
            when(cycle), 1, s=90, marker="s",
            color=OUTCOME_COLOURS.get(outcome, "#999"),
            label=outcome if outcome not in seen else None,
        )
        seen.add(outcome)
    bottom.set_yticks([])
    bottom.set_ylabel("cycles")
    bottom.set_xlabel("time (UTC)")
    if seen:
        bottom.legend(ncol=5, fontsize=9, frameon=False, loc="upper left")
    _style(bottom)
    figure.tight_layout()
    path = out / "04-health.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def summarise(telemetry, cycles, runs) -> str:
    outcomes: dict[str, int] = defaultdict(int)
    for cycle in cycles:
        outcomes[cycle.get("outcome", "?")] += 1
    ok = sum(1 for row in telemetry if row.get("ok"))
    unit = (telemetry[-1].get("unit") if telemetry else None) or "pps"
    lines = [
        f"runs        : {len([r for r in runs if r.get('event') == 'started'])} start(s)",
        f"polls       : {len(telemetry)} ({ok} ok"
        + (f", {len(telemetry) - ok} failed" if len(telemetry) - ok else "") + ")",
        f"cycles      : {len(cycles)}  " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())),
        f"unit        : {unit}",
    ]
    applied = [c for c in cycles if c.get("outcome") == "applied" and c.get("peak_reduction")]
    if applied:
        mean = sum(c["peak_reduction"] for c in applied) / len(applied)
        lines.append(f"peak cut    : {100 * mean:.1f}% mean over {len(applied)} applied cycle(s)")
    if telemetry:
        lines.append(f"covering    : {when(telemetry[0])} .. {when(telemetry[-1])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", default="logs/history", help="directory holding the JSONL streams")
    parser.add_argument("--out", default="output/autopilot", help="where to write the PNGs")
    args = parser.parse_args(argv)

    history = Path(args.history)
    if not history.is_absolute():
        history = ROOT / history
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)

    telemetry = read_jsonl(history / "telemetry.jsonl")
    cycles = read_jsonl(history / "cycles.jsonl")
    runs = read_jsonl(history / "runs.jsonl")

    if not telemetry and not cycles:
        print(f"No history under {history}. Has the autopilot run yet?")
        return 1

    print(summarise(telemetry, cycles, runs))
    print()
    for path in (
        plot_load(telemetry, cycles, runs, out),
        plot_allocation(cycles, out),
        plot_peak_reduction(cycles, out),
        plot_health(telemetry, cycles, out),
    ):
        print(f"  wrote {path}" if path else "  (skipped a figure: not enough data yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
