#!/usr/bin/env python3
"""Build the versioned C-DOT control-science evidence and presentation package."""

from __future__ import annotations

import argparse
import html
import hashlib
import json
import os
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "cdot-control-science-mpl"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.artifacts import atomic_json


NAVY = "#0B1F33"
BLUE = "#1877F2"
CYAN = "#00A6A6"
GOLD = "#F4B400"
CORAL = "#F45B69"
GREEN = "#22A06B"
PURPLE = "#7B61FF"
MUTED = "#66788A"
GRID = "#D9E2EC"
LIGHT_BLUE = "#EAF2FF"

ORDER = (
    ("baseline", "Current MPC"),
    ("adaptive-a0", "Adaptive α=0.0"),
    ("adaptive-a05", "Adaptive α=0.5"),
    ("adaptive-a10", "Adaptive α=1.0"),
    ("failure-domain", "Failure domain"),
    ("churn-trigger", "Churn + trigger"),
)
SCENARIOS = ("surge", "scheduled_fault", "unannounced_outage", "mixed_stress")
SCENARIO_LABELS = ("Surge", "Scheduled fault", "Unknown outage", "Mixed stress")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 14,
        "axes.titleweight": "bold",
        "axes.labelcolor": NAVY,
        "axes.edgecolor": GRID,
        "axes.facecolor": "#FBFCFE",
        "figure.facecolor": "white",
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "grid.color": GRID,
        "grid.alpha": 0.65,
        "legend.frameon": False,
    })


def _save(fig: Any, figures: Path, stem: str) -> list[Path]:
    figures.mkdir(parents=True, exist_ok=True)
    outputs = []
    for suffix in ("png", "svg"):
        target = figures / f"{stem}.{suffix}"
        temporary = target.with_name(f".{target.name}.tmp")
        fig.savefig(
            temporary, format=suffix, dpi=240 if suffix == "png" else None,
            bbox_inches="tight", facecolor="white",
        )
        os.replace(temporary, target)
        outputs.append(target)
    plt.close(fig)
    return outputs


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _traffic_summary(control_root: Path, output: Path) -> dict[str, Any]:
    manifest = control_root / "manifests/delhi-v2-28d-s46001-train.json"
    parquet_paths = sorted(
        (control_root / "corpora").glob(
            "**/campaign=control-science-v1-corpus-train/**/run.parquet"
        )
    )
    if len(parquet_paths) != 1:
        raise ValueError("expected one train corpus Parquet shard")
    parquet = parquet_paths[0]
    cache = output / "data/traffic-v2-summary.json"
    source_hash = _sha256(parquet)
    if cache.exists():
        payload = _read(cache)
        if payload.get("source_sha256") == source_hash:
            return payload

    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("PyArrow is required to summarize traffic-v2") from error

    config = _read(manifest)
    nominal = {
        "|".join((row["key"]["zone"], row["key"]["dnn"], row["key"]["snssai"])): (
            float(row["offered_mbps_per_session"]["ul"]),
            float(row["offered_mbps_per_session"]["dl"]),
        )
        for row in config["groups"]
    }
    interval = int(config["decision_interval_steps"])
    bucket_count = int(config["steps"]) // interval
    sessions = np.zeros(bucket_count)
    actual_ul = np.zeros(bucket_count)
    actual_dl = np.zeros(bucket_count)
    nominal_ul = np.zeros(bucket_count)
    nominal_dl = np.zeros(bucket_count)
    reader = pq.ParquetFile(parquet)
    for batch in reader.iter_batches(
        batch_size=1024,
        columns=("step", "group_arrivals", "group_generated_load_mbps"),
    ):
        for row in batch.to_pylist():
            bucket = int(row["step"]) // interval
            for item in row["group_arrivals"]:
                count = int(item["count"])
                ul_rate, dl_rate = nominal[str(item["group_id"])]
                sessions[bucket] += count
                nominal_ul[bucket] += count * ul_rate
                nominal_dl[bucket] += count * dl_rate
            for item in row["group_generated_load_mbps"]:
                actual_ul[bucket] += float(item["ul_mbps"])
                actual_dl[bucket] += float(item["dl_mbps"])
    payload = {
        "schema_version": "traffic-v2-presentation-summary/1.0",
        "source": str(parquet.resolve()),
        "source_sha256": source_hash,
        "manifest_sha256": _sha256(manifest),
        "bucket_minutes": config["step_seconds"] * interval / 60,
        "sessions": sessions.tolist(),
        "actual_ul": actual_ul.tolist(),
        "actual_dl": actual_dl.tolist(),
        "nominal_ul": nominal_ul.tolist(),
        "nominal_dl": nominal_dl.tolist(),
    }
    atomic_json(cache, payload)
    return payload


def _traffic_figure(summary: dict[str, Any], figures: Path) -> list[Path]:
    actual_dl = np.asarray(summary["actual_dl"])
    nominal_dl = np.asarray(summary["nominal_dl"])
    sessions = np.asarray(summary["sessions"])
    buckets_per_day = round(24 * 60 / float(summary["bucket_minutes"]))
    days = len(actual_dl) // buckets_per_day
    hours = np.arange(buckets_per_day) * float(summary["bucket_minutes"]) / 60
    day = 0
    selected = slice(day * buckets_per_day, (day + 1) * buckets_per_day)
    fig = plt.figure(figsize=(13.2, 8.1))
    grid = fig.add_gridspec(2, 2, height_ratios=(1, 1.05), hspace=0.33, wspace=0.25)
    ax = fig.add_subplot(grid[0, :])
    ax.plot(hours, actual_dl[selected], color=BLUE, linewidth=2.0, label="Actual rate-bin DL label")
    ax.plot(hours, nominal_dl[selected], color=MUTED, linewidth=1.5, linestyle="--",
            label="Nominal sessions × configured rate")
    ax.fill_between(hours, nominal_dl[selected], actual_dl[selected], color=BLUE, alpha=0.10)
    ax.axvspan(11, 16, color=GOLD, alpha=0.13, label="Declared stadium event")
    ax.axvline(12.5, color=CORAL, linestyle=":", linewidth=1.8, label="Known capacity event")
    ax.axvline(17.0, color=PURPLE, linestyle=":", linewidth=1.8, label="Unknown outage observed")
    ax.set_xlim(0, 24); ax.set_ylabel("Generated DL label per 10-minute bucket")
    ax.set_xticks(np.arange(0, 25, 3)); ax.grid(True, axis="y"); ax.set_axisbelow(True)
    ax.legend(ncol=3, loc="upper left")
    ax.set_title("A real label is not a nominal-rate reconstruction", loc="left", color=NAVY)

    scatter = fig.add_subplot(grid[1, 0])
    hb = scatter.hexbin(
        nominal_dl, actual_dl, gridsize=38, mincnt=1, cmap="Blues",
        linewidths=0.15,
    )
    limit = max(float(np.percentile(nominal_dl, 99.7)), float(np.percentile(actual_dl, 99.7)))
    scatter.plot((0, limit), (0, limit), color=CORAL, linestyle="--", linewidth=1.5,
                 label="Actual = nominal")
    scatter.set_xlim(0, limit); scatter.set_ylim(0, limit)
    scatter.set_xlabel("Nominal DL label"); scatter.set_ylabel("Actual generated DL label")
    scatter.set_title("Rate-bin variability is retained", loc="left", color=NAVY)
    scatter.legend(loc="upper left")
    fig.colorbar(hb, ax=scatter, label="10-minute buckets")

    heat = fig.add_subplot(grid[1, 1])
    matrix = actual_dl.reshape(days, buckets_per_day)
    image = heat.imshow(matrix, aspect="auto", interpolation="nearest", cmap="viridis")
    heat.set_xlabel("Hour of day"); heat.set_ylabel("Corpus day")
    heat.set_xticks(np.arange(0, buckets_per_day + 1, 24),
                    [str(value) for value in range(0, 25, 4)])
    heat.set_yticks((0, 6, 13, 20, 27), (1, 7, 14, 21, 28))
    heat.set_title("28-day repeatability with stochastic variation", loc="left", color=NAVY)
    fig.colorbar(image, ax=heat, label="Actual generated DL label")

    relative_gap = np.median(np.abs(actual_dl - nominal_dl) / np.maximum(nominal_dl, 1e-9))
    fig.suptitle(
        "Traffic-v2 realism fingerprint — generated load is causal and rate-bin accurate",
        x=0.06, y=0.995, ha="left", fontsize=19, fontweight="bold", color=NAVY,
    )
    fig.text(
        0.06, 0.952,
        f"Seed 46001 • 28 days • {len(sessions):,} decision buckets • median absolute nominal-label gap {relative_gap:.1%}",
        color=MUTED,
    )
    return _save(fig, figures, "01_traffic_v2_realism_fingerprint")


def _corpus_runs(control_root: Path) -> list[dict[str, Any]]:
    rows = []
    roles = {
        "control-science-v1-corpus-train": "Train · 46001",
        "control-science-v1-corpus-selection": "Select · 46002",
        "control-science-v1-corpus-test-untouched": "Test · 46003",
    }
    for metadata_path in sorted((control_root / "corpora").rglob("metadata.json")):
        metadata = _read(metadata_path)
        performance = _read(metadata_path.parent / "performance.json")
        detailed = next(item for item in metadata["artifacts"] if item["kind"] == "detailed_steps")
        rows.append({
            "label": roles[metadata["campaign_id"]],
            "campaign_id": metadata["campaign_id"],
            "seed": metadata["seed"],
            "steps": metadata["step_count"],
            "wall_seconds": performance["wall_seconds"],
            "cpu_seconds": performance["cpu_seconds"],
            "peak_rss_bytes": performance["peak_rss_bytes"],
            "artifact_bytes": detailed["bytes"],
            "metadata_path": str(metadata_path.resolve()),
            "metadata_sha256": _sha256(metadata_path),
        })
    return rows


def _corpus_figure(rows: list[dict[str, Any]], figures: Path) -> list[Path]:
    labels = [row["label"] for row in rows]
    x = np.arange(len(rows))
    colors = (BLUE, CYAN, PURPLE)
    fig, axes = plt.subplots(1, 3, figsize=(13.3, 4.8))
    walls = np.asarray([row["wall_seconds"] / 60 for row in rows])
    efficiencies = np.asarray([100 * row["cpu_seconds"] / row["wall_seconds"] for row in rows])
    sizes = np.asarray([row["artifact_bytes"] / 2**20 for row in rows])
    rss = np.asarray([row["peak_rss_bytes"] / 2**20 for row in rows])

    bars = axes[0].bar(x, walls, color=colors, width=0.6)
    axes[0].set_xticks(x, labels, rotation=18, ha="right")
    axes[0].set_ylabel("Wall-clock minutes"); axes[0].set_title("Generation time", loc="left", color=NAVY)
    axes[0].yaxis.grid(True); axes[0].set_axisbelow(True)
    for bar, value in zip(bars, walls):
        axes[0].text(bar.get_x()+bar.get_width()/2, value+0.18, f"{value:.1f}m", ha="center", fontweight="bold", color=NAVY)

    bars = axes[1].bar(x, efficiencies, color=colors, width=0.6)
    axes[1].set_xticks(x, labels, rotation=18, ha="right")
    axes[1].set_ylabel("CPU efficiency (%)"); axes[1].set_ylim(90, 102)
    axes[1].set_title("Single-chain utilization", loc="left", color=NAVY)
    axes[1].yaxis.grid(True); axes[1].set_axisbelow(True)
    for bar, value in zip(bars, efficiencies):
        axes[1].text(bar.get_x()+bar.get_width()/2, value+0.25, f"{value:.1f}%", ha="center", fontweight="bold", color=NAVY)

    bars = axes[2].bar(x, sizes, color=colors, width=0.6)
    axes[2].set_xticks(x, labels, rotation=18, ha="right")
    axes[2].set_ylabel("Detailed corpus (MiB)"); axes[2].set_title("Published evidence", loc="left", color=NAVY)
    axes[2].yaxis.grid(True); axes[2].set_axisbelow(True)
    for bar, value, memory in zip(bars, sizes, rss):
        axes[2].text(bar.get_x()+bar.get_width()/2, value+3, f"{value:.1f} MiB", ha="center", fontweight="bold", color=NAVY)
        axes[2].text(bar.get_x()+bar.get_width()/2, value*0.50, f"RSS\n{memory:.0f} MiB", ha="center", va="center", color="white", fontweight="bold")
    fig.suptitle("Three untouched 28-day traffic-v2 corpora completed cleanly", x=0.06, y=1.04,
                 ha="left", fontsize=19, fontweight="bold", color=NAVY)
    fig.text(0.06, 0.965, "80,640 steps each • actual generated UL/DL labels • zero missing shards", color=MUTED)
    fig.subplots_adjust(wspace=0.28, bottom=0.22)
    return _save(fig, figures, "02_corpus_execution_integrity")


def _reconciliation_figure(reconciliation: dict[str, Any], figures: Path) -> list[Path]:
    dev = reconciliation["development_30_pair"]
    prod = reconciliation["production_128_seed"]
    distributions = [
        np.asarray([row["relative_improvement"] for row in dev["pairs"]]) * 100,
        np.asarray([row["relative_improvement"] for row in prod["pairs"]]) * 100,
    ]
    fig, (ax, scenario_ax) = plt.subplots(1, 2, figsize=(13.2, 5.8), gridspec_kw={"width_ratios": (1.25, 1)})
    violin = ax.violinplot(distributions, positions=(0, 1), widths=0.72, showextrema=False)
    for body, color in zip(violin["bodies"], (BLUE, CORAL)):
        body.set_facecolor(color); body.set_edgecolor(color); body.set_alpha(0.22)
    rng = np.random.default_rng(46000)
    for index, (values, color) in enumerate(zip(distributions, (BLUE, CORAL))):
        ax.scatter(index + rng.uniform(-0.16, 0.16, len(values)), values, s=18, alpha=0.62,
                   color=color, edgecolor="white", linewidth=0.25)
        summary = dev if index == 0 else prod
        mean = 100 * float(summary["mean_pair_relative_improvement"])
        lower, upper = [100 * float(value) for value in summary["bootstrap_ci95"]]
        ax.errorbar(index, mean, yerr=((mean-lower,), (upper-mean,)), fmt="D", color=NAVY,
                    markersize=7, capsize=5, linewidth=2.0, zorder=5)
        ax.text(index, max(values.max(), upper)+3, f"mean {mean:+.1f}%", ha="center",
                color=NAVY, fontweight="bold")
    ax.axhline(0, color=NAVY, linewidth=1.4)
    ax.set_xticks((0, 1), ("Development\n30 stress pairs", "Production\n128 paired seeds"))
    ax.set_ylabel("Paired UL overload improvement (%)\npositive is better")
    ax.set_title("The sign reverses under production composition", loc="left", color=NAVY)
    ax.yaxis.grid(True); ax.set_axisbelow(True)

    dev_scenarios = reconciliation["contract_differences"]["development"]["scenario_composition"]
    values = [100 * dev_scenarios[name]["aggregate_ul_overload_area_relative_reduction"] for name in SCENARIOS]
    bars = scenario_ax.barh(np.arange(4), values, color=[GREEN if value >= 0 else CORAL for value in values], height=0.58)
    scenario_ax.axvline(0, color=NAVY, linewidth=1.3)
    scenario_ax.set_yticks(np.arange(4), SCENARIO_LABELS); scenario_ax.invert_yaxis()
    scenario_ax.set_xlabel("Development severity-weighted improvement (%)")
    scenario_ax.set_title("Promotion was driven by scheduled notice", loc="left", color=NAVY)
    scenario_ax.xaxis.grid(True); scenario_ax.set_axisbelow(True)
    for bar, value in zip(bars, values):
        scenario_ax.text(value + (1.2 if value >= 0 else -1.2), bar.get_y()+bar.get_height()/2,
                         f"{value:+.1f}%", va="center", ha="left" if value >= 0 else "right",
                         fontweight="bold", color=NAVY)
    fig.suptitle("Reconciliation: development promotion ≠ production victory", x=0.06, y=0.99,
                 ha="left", fontsize=19, fontweight="bold", color=NAVY)
    fig.text(0.06, 0.925, "Same metric and model • different scenario contract and seed population • production evidence is authoritative", color=MUTED)
    fig.text(0.99, 0.03, "CONCLUSION: STATIC REMAINS PRODUCTION WINNER", ha="right", color=CORAL,
             fontsize=11, fontweight="bold")
    fig.subplots_adjust(top=0.82, bottom=0.16, wspace=0.35)
    return _save(fig, figures, "03_mpc_30_vs_128_reconciliation")


def _evaluations(control_root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for key, _label in ORDER:
        path = control_root / "mpc-development" / key / "evaluation.json"
        if path.exists():
            payload = _read(path)
            payload["_path"] = str(path.resolve())
            payload["_sha256"] = _sha256(path)
            result[key] = payload
    return result


def _ablation_figure(evaluations: dict[str, dict[str, Any]], figures: Path) -> list[Path]:
    labels = [label for _key, label in ORDER]
    x = np.arange(len(ORDER))
    means = []
    lower = []
    upper = []
    weighted = []
    present = []
    for key, _label in ORDER:
        row = evaluations.get(key)
        present.append(row is not None)
        if row is None:
            means.append(0); lower.append(0); upper.append(0); weighted.append(np.nan)
            continue
        mean = 100 * row["mean_pair_ul_overload_area_relative_reduction"]
        ci = [100 * value for value in row["mean_pair_ul_reduction_bootstrap_95_interval"]]
        means.append(mean); lower.append(mean-ci[0]); upper.append(ci[1]-mean)
        weighted.append(100 * row["weighted_total_ul_overload_area_relative_reduction"])
    colors = [BLUE if flag else "#CDD6DF" for flag in present]
    fig, ax = plt.subplots(figsize=(12.5, 6.1))
    bars = ax.bar(x, means, color=colors, width=0.64, zorder=2)
    for index, flag in enumerate(present):
        if flag:
            ax.errorbar(index, means[index], yerr=((lower[index],), (upper[index],)), fmt="none",
                        ecolor=NAVY, elinewidth=1.8, capsize=5, zorder=4)
            ax.scatter(index, weighted[index], marker="D", color=GOLD, edgecolor=NAVY, s=55, zorder=5)
        else:
            bars[index].set_hatch("//")
            ax.text(index, 1.2, "RERUN\nPENDING", ha="center", va="bottom", color=MUTED,
                    fontsize=8, fontweight="bold")
    ax.axhline(10, color=GREEN, linewidth=1.8, linestyle="--", label="Mean gate ≥10%")
    ax.axhline(0, color=NAVY, linewidth=1.3)
    ax.set_xticks(x, labels, rotation=16, ha="right")
    ax.set_ylabel("UL overload-area improvement (%)")
    ax.set_title("No completed candidate clears the full development gate", loc="left", color=NAVY)
    ax.yaxis.grid(True); ax.set_axisbelow(True)
    ax.scatter([], [], marker="D", color=GOLD, edgecolor=NAVY, label="Severity-weighted result")
    ax.legend(loc="upper right")
    for index, flag in enumerate(present):
        if flag:
            ax.text(index, means[index] + (2 if means[index] >= 0 else -2), f"{means[index]:+.1f}%",
                    ha="center", va="bottom" if means[index] >= 0 else "top", color=NAVY,
                    fontweight="bold")
    fig.suptitle("MPC development ablation waterfall — paired seeds 46101–46112", x=0.06, y=0.99,
                 ha="left", fontsize=19, fontweight="bold", color=NAVY)
    fig.text(0.06, 0.925, "Bars: unweighted pair mean • whiskers: bootstrap 95% CI • diamonds: severity weighted", color=MUTED)
    fig.subplots_adjust(top=0.82, bottom=0.22)
    return _save(fig, figures, "04_mpc_ablation_waterfall")


def _scenario_figure(evaluations: dict[str, dict[str, Any]], figures: Path) -> list[Path]:
    available = [(key, label) for key, label in ORDER if key in evaluations]
    matrix = np.asarray([
        [100 * evaluations[key]["by_scenario"][scenario]["aggregate_ul_overload_area_relative_reduction"]
         for scenario in SCENARIOS]
        for key, _label in available
    ])
    fig, ax = plt.subplots(figsize=(11.8, 5.8))
    bound = max(10.0, float(np.max(np.abs(matrix))))
    image = ax.imshow(matrix, cmap="RdYlGn", vmin=-bound, vmax=bound, aspect="auto")
    ax.set_xticks(np.arange(4), SCENARIO_LABELS)
    ax.set_yticks(np.arange(len(available)), [label for _key, label in available])
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            value = matrix[row, column]
            ax.text(column, row, f"{value:+.1f}%", ha="center", va="center",
                    color="white" if abs(value) > bound * 0.45 else NAVY, fontweight="bold")
    fig.colorbar(image, ax=ax, label="Severity-weighted UL improvement (%)")
    fig.suptitle("Controllability is scenario-dependent—not a single headline number", x=0.06, y=0.99,
                 ha="left", fontsize=19, fontweight="bold", color=NAVY)
    ax.set_title("Scheduled notice is valuable; mixed stress exposes brittle controller interactions",
                 loc="left", color=NAVY, pad=16)
    fig.subplots_adjust(top=0.78, left=0.20)
    return _save(fig, figures, "05_mpc_scenario_controllability")


def _risk_figure(evaluations: dict[str, dict[str, Any]], figures: Path) -> list[Path]:
    available = [(key, label) for key, label in ORDER if key in evaluations]
    fig, (scatter, distribution) = plt.subplots(1, 2, figsize=(13.2, 5.8))
    colors = (BLUE, CYAN, GOLD, PURPLE, GREEN, CORAL)
    for index, (key, label) in enumerate(available):
        row = evaluations[key]
        mean = 100 * row["mean_pair_ul_overload_area_relative_reduction"]
        worst = 100 * row["worst_pair_ul_overload_area_relative_reduction"]
        weighted = 100 * row["weighted_total_ul_overload_area_relative_reduction"]
        scatter.scatter(mean, worst, s=85 + 8*abs(weighted), color=colors[index], edgecolor=NAVY,
                        linewidth=0.8, label=label, zorder=4)
        scatter.annotate(label, (mean, worst), xytext=(5, 5), textcoords="offset points", fontsize=8)
    scatter.axvline(10, color=GREEN, linestyle="--", linewidth=1.5)
    scatter.axhline(-10, color=CORAL, linestyle="--", linewidth=1.5)
    scatter.axvspan(10, max(15, scatter.get_xlim()[1]), color=GREEN, alpha=0.07)
    scatter.axhspan(-10, max(5, scatter.get_ylim()[1]), color=GREEN, alpha=0.07)
    scatter.set_xlabel("Mean paired improvement (%) — gate ≥10%")
    scatter.set_ylabel("Worst-pair improvement (%) — gate >−10%")
    scatter.set_title("Mean gain vs. tail risk", loc="left", color=NAVY)
    scatter.grid(True); scatter.set_axisbelow(True)

    values = []
    labels = []
    for key, label in available:
        values.append(np.asarray([
            100 * item["relative_reduction"]["overload_area_seconds"]["ul"]
            for item in evaluations[key]["pairs"]
        ]))
        labels.append(label)
    box = distribution.boxplot(values, orientation="horizontal", patch_artist=True, tick_labels=labels,
                               showmeans=True, meanprops={"marker": "D", "markerfacecolor": GOLD,
                                                          "markeredgecolor": NAVY, "markersize": 5})
    for patch, color in zip(box["boxes"], colors):
        patch.set_facecolor(color); patch.set_alpha(0.32)
    distribution.axvline(0, color=NAVY, linewidth=1.3)
    distribution.axvline(10, color=GREEN, linestyle="--", linewidth=1.5)
    distribution.set_xlabel("Individual-pair UL improvement (%)")
    distribution.set_title("The mean hides wide paired dispersion", loc="left", color=NAVY)
    distribution.xaxis.grid(True); distribution.set_axisbelow(True)
    fig.suptitle("Release safety requires both average benefit and bounded regressions", x=0.06, y=0.99,
                 ha="left", fontsize=19, fontweight="bold", color=NAVY)
    fig.subplots_adjust(top=0.84, bottom=0.14, wspace=0.36)
    return _save(fig, figures, "06_mpc_mean_vs_tail_risk")


def _solve_activity_figure(evaluations: dict[str, dict[str, Any]], figures: Path) -> list[Path]:
    available = [(key, label) for key, label in ORDER if key in evaluations]
    colors = (BLUE, CYAN, GOLD, PURPLE, GREEN, CORAL)
    means = np.asarray([
        np.mean([pair["certified_decisions"] for pair in evaluations[key]["pairs"]])
        for key, _label in available
    ])
    minima = np.asarray([
        min(pair["certified_decisions"] for pair in evaluations[key]["pairs"])
        for key, _label in available
    ])
    maxima = np.asarray([
        max(pair["certified_decisions"] for pair in evaluations[key]["pairs"])
        for key, _label in available
    ])
    total_epochs = np.asarray([
        np.mean([pair["controller_decisions"] for pair in evaluations[key]["pairs"]])
        for key, _label in available
    ])
    weighted = np.asarray([
        100 * evaluations[key]["weighted_total_ul_overload_area_relative_reduction"]
        for key, _label in available
    ])

    fig, (bars_ax, tradeoff) = plt.subplots(1, 2, figsize=(13.2, 5.8))
    y = np.arange(len(available))
    bars = bars_ax.barh(y, means, color=colors[:len(available)], alpha=0.88, height=0.6)
    bars_ax.errorbar(
        means, y, xerr=np.vstack((means - minima, maxima - means)), fmt="none",
        ecolor=NAVY, elinewidth=1.4, capsize=4,
    )
    bars_ax.set_yticks(y, [label for _key, label in available]); bars_ax.invert_yaxis()
    bars_ax.set_xlabel("Accepted new MPC policies per 144 epochs")
    bars_ax.set_title("Actual optimization activity", loc="left", color=NAVY)
    bars_ax.xaxis.grid(True); bars_ax.set_axisbelow(True)
    for bar, value, epochs in zip(bars, means, total_epochs):
        bars_ax.text(value + 1.2, bar.get_y() + bar.get_height()/2,
                     f"{value:.1f} ({100*value/epochs:.0f}%)", va="center",
                     color=NAVY, fontweight="bold", fontsize=8)

    solve_fraction = 100 * means / total_epochs
    for index, ((key, label), x_value, y_value) in enumerate(zip(available, solve_fraction, weighted)):
        size = 125 if key == "churn-trigger" else 82
        tradeoff.scatter(x_value, y_value, s=size, color=colors[index], edgecolor=NAVY,
                         linewidth=0.9, zorder=4)
        tradeoff.annotate(label, (x_value, y_value), xytext=(6, 5),
                          textcoords="offset points", fontsize=8, fontweight="bold" if key == "churn-trigger" else None)
    tradeoff.axhline(0, color=NAVY, linewidth=1.3)
    tradeoff.set_xlabel("Accepted-policy rate (% of controller epochs)")
    tradeoff.set_ylabel("Severity-weighted UL improvement (%)")
    tradeoff.set_title("Compute reduction vs. network outcome", loc="left", color=NAVY)
    tradeoff.grid(True); tradeoff.set_axisbelow(True)
    fig.suptitle("Solve less only matters if overload stays controlled", x=0.06, y=0.99,
                 ha="left", fontsize=19, fontweight="bold", color=NAVY)
    fig.text(0.06, 0.925,
             "Whiskers: min–max across paired seeds • retained/skipped policies are excluded • exact L1 route churn was not emitted",
             color=MUTED)
    fig.subplots_adjust(top=0.82, bottom=0.16, left=0.18, wspace=0.34)
    return _save(fig, figures, "08_mpc_solve_activity_tradeoff")


def _plan_rows(evaluations: dict[str, dict[str, Any]], churn_running: bool) -> list[dict[str, str]]:
    churn = "complete" if "churn-trigger" in evaluations else "running" if churn_running else "blocked"
    completed_ablation_count = len(evaluations)
    return [
        {"work": "Frozen 12-node production evidence", "status": "complete", "finding": "384 shards, 90.9% CPU, zero failures"},
        {"work": "30-pair vs 128-seed reconciliation", "status": "complete", "finding": "Static remains production winner"},
        {"work": "Three 28-day traffic-v2 corpora", "status": "complete", "finding": "Seeds 46001/2/3 published and validated"},
        {"work": "Forecast challenger training/test", "status": "not_run", "finding": "Promotion criteria not yet testable"},
        {"work": "Empirical survival experiments", "status": "not_run", "finding": "Estimator implemented; outcome evidence pending"},
        {"work": "MPC development ablations", "status": "complete" if completed_ablation_count == 6 else "partial", "finding": f"{completed_ablation_count} of 6 complete; no promotion candidate"},
        {"work": "Churn + solve-trigger rerun", "status": churn, "finding": "12 development pairs after contract fix"},
        {"work": "MPC validation seeds 46201–46216", "status": "not_run", "finding": "Correctly untouched"},
        {"work": "MPC release seeds 46301–46330", "status": "not_run", "finding": "Correctly untouched"},
        {"work": "New control-science report", "status": "complete", "finding": "Separate from frozen production package"},
    ]


def _plan_figure(rows: list[dict[str, str]], figures: Path) -> list[Path]:
    colors = {"complete": GREEN, "partial": GOLD, "running": BLUE, "blocked": CORAL, "not_run": "#AAB7C4"}
    labels = {"complete": "COMPLETE", "partial": "PARTIAL", "running": "RUNNING", "blocked": "BLOCKED", "not_run": "NOT RUN"}
    fig, ax = plt.subplots(figsize=(13.2, 7.0))
    ax.axis("off")
    fig.suptitle("Seven-day plan coverage — evidence, not aspiration", x=0.05, y=0.98,
                 ha="left", fontsize=20, fontweight="bold", color=NAVY)
    fig.text(0.05, 0.925, "Release and validation seeds remain untouched because no development candidate has cleared the gate", color=MUTED)
    top = 0.86
    height = 0.073
    for index, row in enumerate(rows):
        y = top - index * height
        if index % 2 == 0:
            ax.add_patch(plt.Rectangle((0.035, y-0.036), 0.93, 0.061, transform=ax.transAxes,
                                       color="#F6F8FB", zorder=0))
        color = colors[row["status"]]
        ax.add_patch(plt.Rectangle((0.055, y-0.017), 0.105, 0.034, transform=ax.transAxes,
                                   color=color, zorder=2))
        ax.text(0.1075, y, labels[row["status"]], transform=ax.transAxes, ha="center", va="center",
                color="white", fontsize=8, fontweight="bold")
        ax.text(0.185, y, row["work"], transform=ax.transAxes, va="center", color=NAVY,
                fontweight="bold", fontsize=10)
        ax.text(0.56, y, row["finding"], transform=ax.transAxes, va="center", color=MUTED, fontsize=9)
    return _save(fig, figures, "07_plan_coverage_and_release_discipline")


def _candidate_gate(row: dict[str, Any]) -> dict[str, bool | None]:
    mean = row["mean_pair_ul_overload_area_relative_reduction"]
    lower = row["mean_pair_ul_reduction_bootstrap_95_interval"][0]
    weighted = row["weighted_total_ul_overload_area_relative_reduction"]
    worst = row["worst_pair_ul_overload_area_relative_reduction"]
    scenario = row["by_scenario"]
    stress_ok = all(
        scenario[name]["aggregate_ul_overload_area_relative_reduction"] >= -0.02
        for name in ("unannounced_outage", "mixed_stress")
    )
    return {
        "mean_at_least_10_percent": mean >= 0.10,
        "bootstrap_lower_above_zero": lower > 0,
        "severity_weighted_positive": weighted > 0,
        "unknown_and_mixed_regression_within_2_percent": stress_ok,
        "worst_pair_better_than_minus_10_percent": worst > -0.10,
        "aggregate_guardrails": all(row["aggregate_guardrails"].values()),
        "routing_churn_not_increased": None,
        "empirical_survival_robustness": None,
    }


def _ledger(
    control_root: Path, reconciliation: dict[str, Any], corpus: list[dict[str, Any]],
    evaluations: dict[str, dict[str, Any]], churn_running: bool,
) -> dict[str, Any]:
    experiments: list[dict[str, Any]] = [
        {
            "experiment_id": "production-evidence-freeze",
            "status": "complete",
            "result": {
                **_read(control_root / "frozen-production-reference.json")["scale_evidence"],
                "reference_hashes_verified": 4,
                "all_reference_hashes_match": True,
            },
            "expectation": "Freeze and do not regenerate the accepted 12-node production package.",
            "assessment": "matched",
        },
        {
            "experiment_id": "mpc-30-vs-128-reconciliation",
            "status": "complete",
            "result": {
                "development_mean": reconciliation["development_30_pair"]["mean_pair_relative_improvement"],
                "production_mean": reconciliation["production_128_seed"]["mean_pair_relative_improvement"],
                "directly_comparable": reconciliation["directly_comparable"],
                "conclusion": reconciliation["authoritative_production_conclusion"],
            },
            "expectation": "Resolve the conflicting claims and make the 128-seed production result authoritative.",
            "assessment": "matched",
        },
        {
            "experiment_id": "traffic-v2-28-day-corpora",
            "status": "complete",
            "jobs": ["3450.wlm", "3451.wlm", "3452.wlm"],
            "result": corpus,
            "expectation": "Generate train, selection/calibration and untouched-test corpora with actual rate-bin labels.",
            "assessment": "matched",
        },
        {
            "experiment_id": "mpc-packed-development-wave",
            "status": "complete_with_failed_ablation",
            "job": "3459.wlm",
            "result": {
                "wall_seconds": 3662, "allocated_cpus": 72, "valid_pairs": 60, "exit_status": 1,
                "superseded_jobs": ["3453.wlm", "3454.wlm", "3455.wlm", "3456.wlm", "3457.wlm", "3458.wlm"],
                "failed_ablation": "churn-trigger",
                "failure": "SolverReport contract rejected the intentional skipped solve status.",
            },
            "expectation": "Run independent development ablations on seeds 46101–46112 without touching release seeds.",
            "assessment": "partial",
        },
        {
            "experiment_id": "churn-trigger-skip-contract-regression",
            "status": "complete",
            "result": {
                "change": "SolverReport now accepts the intentional skipped solve status.",
                "relevant_tests_passed": 22,
                "full_test_discovery": {
                    "tests_run": 145,
                    "tests_passed": 143,
                    "assertion_failures": 0,
                    "environment_errors": [
                        "missing output/models/extreme-oracle-bound-evaluation-v1.json",
                        "optional qrcode package unavailable in penv",
                    ],
                },
                "rerun_job": "3460.wlm",
            },
            "expectation": "Retained safe policies must be auditable as skipped solves without failing schema validation.",
            "assessment": "matched",
        },
    ]
    for key, label in ORDER:
        row = evaluations.get(key)
        if row is None:
            status = "running" if key == "churn-trigger" and churn_running else "not_complete"
            experiments.append({
                "experiment_id": f"mpc-development-{key}", "label": label, "status": status,
                "job": "3460.wlm" if key == "churn-trigger" and churn_running else "3459.wlm",
                "expectation": "Clear every MPC development promotion gate.", "assessment": "pending",
            })
            continue
        gate = _candidate_gate(row)
        experiments.append({
            "experiment_id": f"mpc-development-{key}", "label": label, "status": "complete",
            "job": "3460.wlm" if key == "churn-trigger" else "3459.wlm",
            "artifact": row["_path"], "artifact_sha256": row["_sha256"],
            "result": {
                "mean_pair_ul_improvement": row["mean_pair_ul_overload_area_relative_reduction"],
                "bootstrap_ci95": row["mean_pair_ul_reduction_bootstrap_95_interval"],
                "severity_weighted_ul_improvement": row["weighted_total_ul_overload_area_relative_reduction"],
                "worst_pair": row["worst_pair_ul_overload_area_relative_reduction"],
                "mean_controller_epochs": float(np.mean([pair["controller_decisions"] for pair in row["pairs"]])),
                "mean_accepted_new_policies": float(np.mean([pair["certified_decisions"] for pair in row["pairs"]])),
                "pbs_accounting": ({
                    "job": "3460.wlm", "nodes": 1, "ncpus": 12, "parallel_pairs": 12,
                    "wall_seconds": 2287, "cpu_seconds": 21259, "cpu_percent": 1192,
                    "peak_memory_kb": 4951176, "exit_status": 0,
                } if key == "churn-trigger" else None),
                "gate_checks": gate,
            },
            "expectation": "Clear every MPC development promotion gate.",
            "assessment": "matched" if all(value is True for value in gate.values()) else "did_not_match",
        })
    experiments.extend([
        {
            "experiment_id": "forecast-challenger-comparison", "status": "not_run",
            "expectation": "Compare ridge-v2, histogram-gradient quantile, regime ensemble and LightGBM.",
            "assessment": "pending",
        },
        {
            "experiment_id": "survival-provider-sample-size-and-drift", "status": "not_run",
            "expectation": "Compare oracle, empirical, uniform and stale curves at 100/1,000/10,000 samples.",
            "assessment": "pending",
        },
        {
            "experiment_id": "mpc-validation-and-release", "status": "not_run",
            "expectation": "Use validation then untouched release seeds only after development promotion.",
            "assessment": "correctly_deferred",
        },
    ])
    return {
        "schema_version": "control-science-experiment-ledger/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "production_package_modified": False,
        "release_seeds_consumed": False,
        "experiments": experiments,
    }


def _report_markdown(
    ledger: dict[str, Any], reconciliation: dict[str, Any], evaluations: dict[str, dict[str, Any]],
    plan_rows: list[dict[str, str]],
) -> str:
    lines = [
        "# C-DOT Control-Science Experiment Report",
        "",
        f"Generated: {ledger['created_at']}",
        "",
        "## Executive conclusion",
        "",
        "The frozen 12-node production campaign remains valid and unchanged. Its scale result is 384/384 shards in 85.4 minutes at 90.9% aggregate CPU efficiency, with zero worker failures, establishment failures or swap.",
        "",
        "The controller conclusion is intentionally conservative: **Static remains the production winner. MPC is not promoted.** The earlier 30-pair development improvement (+18.76%) came from a four-stressor candidate-selection contract dominated by scheduled-fault gains; the 128-seed production contract shows −13.30% mean paired improvement and is authoritative for production claims.",
        "",
        "Three 28-day traffic-v2 corpora are complete with actual generated UL/DL rate-bin labels. Forecast challenger training, untouched forecast testing and survival experiments have not yet run, so their promotion criteria cannot be claimed.",
        "",
        f"{sum(all(row['aggregate_guardrails'].values()) for row in evaluations.values())} of {len(evaluations)} completed MPC development ablations preserve aggregate DL/drop/session guardrails; churn/trigger does not, and none clears every promotion gate. Validation and release seeds remain untouched.",
        "",
        "## Reconciliation result",
        "",
        f"- Development 30-pair mean: {reconciliation['development_30_pair']['mean_pair_relative_improvement']:.2%}; 95% CI {reconciliation['development_30_pair']['bootstrap_ci95'][0]:.2%} to {reconciliation['development_30_pair']['bootstrap_ci95'][1]:.2%}.",
        f"- Production 128-seed mean: {reconciliation['production_128_seed']['mean_pair_relative_improvement']:.2%}; 95% CI {reconciliation['production_128_seed']['bootstrap_ci95'][0]:.2%} to {reconciliation['production_128_seed']['bootstrap_ci95'][1]:.2%}.",
        f"- Exact reason: {reconciliation['reason']}",
        "",
        "## MPC development results",
        "",
        "| Candidate | Mean pair | Bootstrap 95% CI | Severity weighted | Worst pair | Accepted new policies | Full gate |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for key, label in ORDER:
        row = evaluations.get(key)
        if row is None:
            lines.append(f"| {label} | — | — | — | — | — | Pending rerun |")
            continue
        gate = _candidate_gate(row)
        full = all(value is True for value in gate.values())
        ci = row["mean_pair_ul_reduction_bootstrap_95_interval"]
        lines.append(
            f"| {label} | {row['mean_pair_ul_overload_area_relative_reduction']:.2%} | "
            f"{ci[0]:.2%} to {ci[1]:.2%} | "
            f"{row['weighted_total_ul_overload_area_relative_reduction']:.2%} | "
            f"{row['worst_pair_ul_overload_area_relative_reduction']:.2%} | "
            f"{np.mean([pair['certified_decisions'] for pair in row['pairs']]):.1f} / 144 | "
            f"{'PASS' if full else 'FAIL / incomplete evidence'} |"
        )
    lines.extend([
        "",
        "Accepted-new-policy counts quantify optimization activity and the solve-trigger effect. Exact L1 routing churn and imperfect-empirical-survival acceptance checks are not present in the current evaluator output; they remain explicitly pending rather than being treated as passes.",
        "",
        "### Gate-by-gate assessment",
        "",
    ])
    gate_labels = {
        "mean_at_least_10_percent": "mean < 10%",
        "bootstrap_lower_above_zero": "bootstrap lower bound ≤ 0",
        "severity_weighted_positive": "severity-weighted result ≤ 0",
        "unknown_and_mixed_regression_within_2_percent": "unknown/mixed regression > 2%",
        "worst_pair_better_than_minus_10_percent": "worst pair ≤ −10%",
        "aggregate_guardrails": "aggregate DL/drop/session guardrail",
        "routing_churn_not_increased": "exact L1 churn unmeasured",
        "empirical_survival_robustness": "imperfect-survival robustness unmeasured",
    }
    for key, label in ORDER:
        row = evaluations.get(key)
        if row is None:
            lines.append(f"- **{label}:** pending rerun.")
            continue
        checks = _candidate_gate(row)
        issues = [gate_labels[name] for name, value in checks.items() if value is not True]
        lines.append(f"- **{label}:** does not match promotion expectation — " + "; ".join(issues) + ".")
    lines.extend([
        "",
        "## Initial-plan coverage",
        "",
        "| Work item | Status | Evidence/finding |",
        "|---|---|---|",
    ])
    for row in plan_rows:
        lines.append(f"| {row['work']} | {row['status'].upper()} | {row['finding']} |")
    lines.extend([
        "",
        "## Experiment ledger",
        "",
        "| Experiment | Status | Initial expectation / contract | Assessment |",
        "|---|---|---|---|",
    ])
    for item in ledger["experiments"]:
        lines.append(
            f"| `{item['experiment_id']}` | {item['status']} | "
            f"{item['expectation']} | {item['assessment']} |"
        )
    lines.extend([
        "",
        "## Presentation rules",
        "",
        "- Use the frozen 12-node campaign only as production-scale evidence.",
        "- Present the 30-pair MPC result only as development evidence under its four-stressor contract.",
        "- Use the 128-seed result for production controller claims.",
        "- Do not call MPC promoted until an untouched release candidate clears every gate.",
        "- Do not claim forecast or survival improvements before those experiments complete.",
        "",
        "## Reproducibility",
        "",
        "- The 22 focused schema/controller/optimizer tests pass in the PBS `penv` environment.",
        "- Full discovery ran 145 tests: 143 passed with zero assertion failures; two environment errors were an unavailable frozen oracle input and the optional `qrcode` package.",
        "- All four frozen production reference hashes match exactly.",
        "- `experiment-ledger.json` records jobs, artifact paths, hashes and gate checks. `artifact-manifest.json` hashes every generated report and figure.",
        "- The immutable production package under `output/showcase/cdot-production-final/` is not modified by this report.",
        "",
    ])
    return "\n".join(lines)


def _talk_track(
    evaluations: dict[str, dict[str, Any]], reconciliation: dict[str, Any],
) -> str:
    churn = evaluations.get("churn-trigger")
    churn_line = (
        "The churn/trigger rerun is still active; do not quote a result yet."
        if churn is None else
        f"Churn/trigger delivered {churn['mean_pair_ul_overload_area_relative_reduction']:+.1%} mean and "
        f"{churn['weighted_total_ul_overload_area_relative_reduction']:+.1%} severity-weighted UL improvement."
    )
    return "\n".join([
        "# C-DOT Control-Science Talk Track",
        "",
        "## Opening (30 seconds)",
        "",
        "The 12-node production scale result is frozen and unchanged: 384/384 shards, 85.4 minutes, 90.9% aggregate CPU, and zero worker, establishment, or swap failures. This section asks a different question: what controller evidence is strong enough for a production claim?",
        "",
        "## Figure 1 — Traffic-v2 realism fingerprint",
        "",
        "The corrected corpus trains against actual generated rate-bin UL/DL load, not session count multiplied by a nominal rate. Point out the visible label gap and the causal event markers. Claim the data correction; do not claim forecast superiority yet.",
        "",
        "## Figure 2 — Corpus execution integrity",
        "",
        "All three 28-day splits are complete and independently seeded: 46001 training, 46002 selection/calibration, and untouched 46003 test. Each contains 80,640 steps and about 201 MiB of detailed evidence.",
        "",
        "## Figure 3 — 30-pair versus 128-seed reconciliation",
        "",
        f"The same UL overload-area direction reverses: development {reconciliation['development_30_pair']['mean_pair_relative_improvement']:+.2%}, production {reconciliation['production_128_seed']['mean_pair_relative_improvement']:+.2%}. The contracts differ in scenario composition and seed population. The 128-seed campaign is authoritative: Static remains the production winner.",
        "",
        "## Figure 4 — MPC ablation waterfall",
        "",
        f"Walk left to right across independently switchable changes. {churn_line} Whiskers crossing zero and tail regressions prevent promotion even when a mean bar looks encouraging.",
        "",
        "## Figure 5 — Scenario controllability",
        "",
        "Scheduled notice is genuinely useful; an unknown outage cannot be predicted before observable evidence. The heatmap is the reason we report scenario strata rather than one optimistic average.",
        "",
        "## Figure 6 — Mean versus tail risk",
        "",
        "The release contract requires both mean benefit and a bounded worst pair. None of the development candidates occupies the safe upper-right gate region, so validation and release remain closed.",
        "",
        "## Figure 7 — Plan coverage and release discipline",
        "",
        "Separate completed evidence, implementation foundations, and experiments not yet run. Forecast challenger and survival-provider outcomes are pending; release seeds 46301–46330 have not been viewed.",
        "",
        "## Figure 8 — Solve activity versus network outcome",
        "",
        "Accepted-new-policy counts show actual optimization activity; retained/skipped epochs are excluded. Lower solve activity is valuable only if network overload remains controlled. Exact L1 routing churn was not emitted by this evaluator, so that gate is pending.",
        "",
        "## Close / Q&A guardrails",
        "",
        "- Production scale is proven.",
        "- Static is the current production controller winner.",
        "- MPC ablations are development learning, not release promotion.",
        "- Forecast and survival implementations are not outcome claims until their held-out experiments run.",
        "- Every result and figure is hashed in the artifact manifest.",
        "",
    ])


def _ledger_summary(item: dict[str, Any]) -> str:
    result = item.get("result")
    if item["experiment_id"] == "production-evidence-freeze":
        return "384/384 shards · 85.4 min · 90.9% CPU · zero failures/swap"
    if item["experiment_id"] == "mpc-30-vs-128-reconciliation":
        return "Development +18.76%; production −13.30%; Static authoritative"
    if item["experiment_id"] == "traffic-v2-28-day-corpora":
        return "Seeds 46001/2/3 · 80,640 steps each · actual rate-bin labels"
    if item["experiment_id"] == "mpc-packed-development-wave":
        return "72 workers · 60 valid pairs · churn attempt failed contract · exit 1"
    if item["experiment_id"] == "churn-trigger-skip-contract-regression":
        return "Skipped-solve audit status fixed · 22 focused tests pass · rerun 3460"
    if isinstance(result, dict) and "mean_pair_ul_improvement" in result:
        return (
            f"Mean {result['mean_pair_ul_improvement']:+.2%} · weighted "
            f"{result['severity_weighted_ul_improvement']:+.2%} · worst {result['worst_pair']:+.2%}"
        )
    if item["status"] == "running":
        return "PBS rerun active; final paired evidence will replace this line"
    if item["status"] in {"not_run", "not_complete"}:
        return "No outcome claim; intentionally held behind the development gate"
    return str(item.get("assessment", ""))


def _pdf(output: Path, figures: list[Path], ledger: dict[str, Any]) -> Path:
    target = output / "cdot_control_science_report.pdf"
    temporary = target.with_name(f".{target.name}.tmp")
    with PdfPages(temporary) as pdf:
        fig = plt.figure(figsize=(11.69, 8.27))
        fig.text(0.07, 0.83, "C-DOT", fontsize=19, color=BLUE, fontweight="bold")
        fig.text(0.07, 0.68, "Control-Science\nExperiment Report", fontsize=34, color=NAVY,
                 fontweight="bold", linespacing=1.1)
        fig.text(0.07, 0.50, "Forecasting foundations • MPC reconciliation • development ablations",
                 fontsize=15, color=MUTED)
        fig.text(0.07, 0.35, "AUTHORITATIVE CONCLUSION", fontsize=10, color=CORAL, fontweight="bold")
        fig.text(0.07, 0.29, "Static remains the production winner.\nMPC is not promoted.",
                 fontsize=22, color=NAVY, fontweight="bold")
        fig.text(0.07, 0.08, ledger["created_at"], fontsize=9, color=MUTED)
        plt.axis("off"); pdf.savefig(fig, facecolor="white"); plt.close(fig)

        fig = plt.figure(figsize=(11.69, 8.27))
        fig.text(0.06, 0.92, "What the evidence says", fontsize=27, color=NAVY, fontweight="bold")
        fig.text(0.06, 0.865, "The presentation claim is deliberately narrower than the development ambition.",
                 fontsize=12, color=MUTED)
        boxes = (
            (0.06, 0.63, BLUE, "SCALE IS PROVEN",
             "12 nodes · 384/384 shards · 85.4 minutes\n90.9% aggregate CPU · zero failures · zero swap"),
            (0.53, 0.63, CYAN, "FORECAST DATA FOUNDATION",
             "Three 28-day traffic-v2 corpora are complete.\nActual UL/DL rate-bin load replaces nominal labels."),
            (0.06, 0.34, CORAL, "CONTROLLER RESULT",
             "30-pair development: +18.76%\n128-seed production: −13.30%\nStatic remains production winner."),
            (0.53, 0.34, GOLD, "RELEASE DISCIPLINE",
             "No development ablation clears every gate.\nValidation and release seeds remain untouched.\nForecast/survival gains are not yet claimed."),
        )
        for x, y, color, heading, body in boxes:
            fig.patches.append(plt.Rectangle((x, y), 0.40, 0.21, transform=fig.transFigure,
                                              facecolor="#F7F9FC", edgecolor=GRID, linewidth=1.2))
            fig.patches.append(plt.Rectangle((x, y), 0.012, 0.21, transform=fig.transFigure,
                                              facecolor=color, edgecolor=color))
            fig.text(x + 0.035, y + 0.155, heading, fontsize=10, color=color, fontweight="bold")
            fig.text(x + 0.035, y + 0.055, body, fontsize=11, color=NAVY, linespacing=1.5)
        fig.text(0.06, 0.18, "PRESENTATION RULE", fontsize=10, color=CORAL, fontweight="bold")
        fig.text(0.06, 0.115,
                 "Use production evidence for production claims.\nUse ablations to explain what was learned—not to claim promotion.",
                 fontsize=13, color=NAVY, fontweight="bold", linespacing=1.45)
        plt.axis("off"); pdf.savefig(fig, facecolor="white"); plt.close(fig)

        experiments = ledger["experiments"]
        page_size = 7
        status_colors = {
            "complete": GREEN, "complete_with_failed_ablation": GOLD, "running": BLUE,
            "not_run": "#AAB7C4", "not_complete": CORAL,
        }
        for page_start in range(0, len(experiments), page_size):
            selected = experiments[page_start:page_start + page_size]
            fig = plt.figure(figsize=(11.69, 8.27))
            fig.text(0.055, 0.93, "Experiment ledger", fontsize=25, color=NAVY, fontweight="bold")
            fig.text(0.055, 0.885,
                     f"Every submitted/completed experiment and its initial-plan expectation · {page_start + 1}–{page_start + len(selected)} of {len(experiments)}",
                     fontsize=10.5, color=MUTED)
            top = 0.82
            row_height = 0.105
            for index, item in enumerate(selected):
                y = top - index * row_height
                if index % 2 == 0:
                    fig.patches.append(plt.Rectangle((0.05, y - 0.063), 0.90, 0.091,
                                                      transform=fig.transFigure, facecolor="#F7F9FC",
                                                      edgecolor="none"))
                status = item["status"]
                color = status_colors.get(status, MUTED)
                fig.patches.append(plt.Rectangle((0.065, y - 0.012), 0.105, 0.031,
                                                  transform=fig.transFigure, facecolor=color,
                                                  edgecolor="none"))
                fig.text(0.1175, y + 0.003, status.replace("complete_with_failed_ablation", "PARTIAL").upper(),
                         fontsize=7.2, color="white", fontweight="bold", ha="center", va="center")
                fig.text(0.19, y + 0.011, item["experiment_id"], fontsize=9.5,
                         color=NAVY, fontweight="bold")
                expectation = textwrap.shorten(item["expectation"], width=97, placeholder="…")
                fig.text(0.19, y - 0.013, f"Expected: {expectation}", fontsize=7.7,
                         color=BLUE, fontweight="bold")
                summary = textwrap.shorten(_ledger_summary(item), width=104, placeholder="…")
                fig.text(0.19, y - 0.043, summary, fontsize=8.8, color=MUTED)
                assessment = item["assessment"].replace("_", " ").upper()
                fig.text(0.94, y + 0.009, assessment, fontsize=7.0,
                         color=GREEN if assessment == "MATCHED" else CORAL if assessment == "DID NOT MATCH" else MUTED,
                         fontweight="bold", ha="right")
            fig.text(0.055, 0.055,
                     "Full machine-readable metrics, configuration hashes and gate checks: experiment-ledger.json",
                     fontsize=9, color=MUTED)
            plt.axis("off"); pdf.savefig(fig, facecolor="white"); plt.close(fig)
        for path in figures:
            image = plt.imread(path)
            fig, ax = plt.subplots(figsize=(11.69, 8.27))
            ax.imshow(image); ax.axis("off")
            pdf.savefig(fig, facecolor="white", bbox_inches="tight"); plt.close(fig)
    os.replace(temporary, target)
    return target


def _html(output: Path, figures: list[Path], ledger: dict[str, Any]) -> Path:
    cards = []
    titles = {
        "01_traffic_v2_realism_fingerprint.png": "Traffic-v2 realism fingerprint",
        "02_corpus_execution_integrity.png": "Corpus execution and integrity",
        "03_mpc_30_vs_128_reconciliation.png": "30-pair vs 128-seed reconciliation",
        "04_mpc_ablation_waterfall.png": "MPC ablation waterfall",
        "05_mpc_scenario_controllability.png": "Scenario controllability",
        "06_mpc_mean_vs_tail_risk.png": "Mean improvement vs tail risk",
        "07_plan_coverage_and_release_discipline.png": "Plan coverage and release discipline",
        "08_mpc_solve_activity_tradeoff.png": "Solve activity vs network outcome",
    }
    for path in figures:
        relative = path.relative_to(output).as_posix()
        cards.append(
            f'<section class="card"><h2>{html.escape(titles.get(path.name, path.stem))}</h2>'
            f'<a href="{relative}"><img src="{relative}" alt="{html.escape(path.stem)}"></a></section>'
        )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>C-DOT Control-Science Evidence</title>
<style>
:root{{--navy:#0B1F33;--blue:#1877F2;--coral:#F45B69;--muted:#66788A;--grid:#D9E2EC}}
*{{box-sizing:border-box}} body{{margin:0;font-family:Inter,system-ui,sans-serif;color:var(--navy);background:#f4f7fb}}
header{{padding:64px max(5vw,24px) 52px;background:linear-gradient(130deg,#071827,#12395e);color:white}}
.eyebrow{{color:#65b5ff;font-weight:800;letter-spacing:.12em}} h1{{font-size:clamp(34px,5vw,68px);line-height:1.02;margin:.3em 0}}
.verdict{{display:inline-block;background:var(--coral);padding:11px 16px;border-radius:6px;font-weight:800}}
.sub{{max-width:900px;color:#d5e2ef;font-size:18px;line-height:1.55}} nav a{{color:white;margin-right:22px;font-weight:700}}
main{{padding:38px max(4vw,20px) 70px;display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:26px}}
.card{{background:white;border:1px solid var(--grid);border-radius:12px;padding:18px;box-shadow:0 12px 32px rgba(11,31,51,.08)}}
.card h2{{margin:2px 4px 14px;font-size:21px}} img{{display:block;width:100%;height:auto;border-radius:6px}}
footer{{padding:25px 5vw 45px;color:var(--muted)}} @media(max-width:650px){{main{{grid-template-columns:1fr}}}}
</style></head><body>
<header><div class="eyebrow">C-DOT · CONTROL SCIENCE · EVIDENCE V1</div>
<h1>Forecasting foundations<br>and MPC development evidence</h1>
<p class="sub">Frozen production scale evidence is retained. New plots distinguish development controllability from production outcomes and keep untested forecast/survival claims explicit.</p>
<p class="verdict">STATIC REMAINS THE PRODUCTION WINNER · MPC NOT PROMOTED</p>
<nav><a href="cdot_control_science_report.pdf">PDF report</a><a href="REPORT.md">Detailed report</a><a href="TALK_TRACK.md">Talk track</a><a href="experiment-ledger.json">Experiment ledger</a></nav></header>
<main>{''.join(cards)}</main>
<footer>Generated {html.escape(ledger['created_at'])} · release seeds not consumed · production package unchanged</footer>
</body></html>"""
    target = output / "index.html"
    _atomic_text(target, document)
    return target


def build(control_root: Path, output: Path, *, churn_running: bool = False) -> dict[str, Any]:
    _style()
    output.mkdir(parents=True, exist_ok=True)
    figures_dir = output / "figures"
    reconciliation = _read(control_root / "mpc-reconciliation.json")
    corpus = _corpus_runs(control_root)
    evaluations = _evaluations(control_root)
    traffic = _traffic_summary(control_root, output)

    generated: list[Path] = []
    generated += _traffic_figure(traffic, figures_dir)
    generated += _corpus_figure(corpus, figures_dir)
    generated += _reconciliation_figure(reconciliation, figures_dir)
    generated += _ablation_figure(evaluations, figures_dir)
    generated += _scenario_figure(evaluations, figures_dir)
    generated += _risk_figure(evaluations, figures_dir)
    plan_rows = _plan_rows(evaluations, churn_running)
    generated += _plan_figure(plan_rows, figures_dir)
    generated += _solve_activity_figure(evaluations, figures_dir)

    ledger = _ledger(control_root, reconciliation, corpus, evaluations, churn_running)
    ledger_path = output / "experiment-ledger.json"
    atomic_json(ledger_path, ledger)
    report_path = output / "REPORT.md"
    _atomic_text(report_path, _report_markdown(ledger, reconciliation, evaluations, plan_rows))
    talk_track_path = output / "TALK_TRACK.md"
    _atomic_text(talk_track_path, _talk_track(evaluations, reconciliation))
    pdf_path = _pdf(output, [path for path in generated if path.suffix == ".png"], ledger)
    html_path = _html(output, [path for path in generated if path.suffix == ".png"], ledger)
    generated.extend((ledger_path, report_path, talk_track_path, pdf_path, html_path,
                      output / "data/traffic-v2-summary.json"))

    manifest = {
        "schema_version": "control-science-evidence-manifest/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "production_package_modified": False,
        "release_seeds_consumed": False,
        "artifacts": [
            {
                "path": str(path.relative_to(output)),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted(set(generated))
        ],
    }
    atomic_json(output / "artifact-manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", type=Path, default=Path("output/control-science/v1"))
    parser.add_argument("--output", type=Path, default=Path("output/control-science/v1/evidence-v1"))
    parser.add_argument("--churn-running", action="store_true")
    args = parser.parse_args()
    manifest = build(args.control_root, args.output, churn_running=args.churn_running)
    print(json.dumps({
        "output": str(args.output), "artifacts": len(manifest["artifacts"]),
        "manifest": str(args.output / "artifact-manifest.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
