#!/usr/bin/env python3
"""Build the C-DOT Phase-1/2 forecast evidence and presentation package."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "cdot-fc-phase2-mpl"))
os.environ.setdefault("XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "cdot-fc-phase2-cache"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import TwoSlopeNorm
from matplotlib.patches import FancyBboxPatch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


NAVY = "#0B1F33"
BLUE = "#1877F2"
CYAN = "#00A6A6"
GOLD = "#F4B400"
CORAL = "#F45B69"
GREEN = "#22A06B"
PURPLE = "#7457D5"
MUTED = "#66788A"
GRID = "#D9E2EC"
PALE = "#F4F7FA"

FAMILIES = (
    "calendar-ridge",
    "ridge-v2",
    "hist-gradient-quantile",
    "lightgbm-quantile",
    "regime-ensemble",
)
LABELS = {
    "calendar-ridge": "Calendar ridge",
    "ridge-v2": "Ridge-v2",
    "hist-gradient-quantile": "Histogram gradient",
    "lightgbm-quantile": "LightGBM",
    "regime-ensemble": "Regime ensemble",
}
COLORS = {
    "calendar-ridge": MUTED,
    "ridge-v2": BLUE,
    "hist-gradient-quantile": CYAN,
    "lightgbm-quantile": PURPLE,
    "regime-ensemble": GOLD,
}
GATE_LABELS = {
    "wape_improves_15_percent_over_best_simple_baseline": "WAPE improves ≥15%",
    "scheduled_detected_peak_underprediction_improves_20_percent": "Peak underprediction improves ≥20%",
    "p90_coverage_between_88_and_95_percent": "p90 coverage 88–95%",
    "no_regime_or_horizon_worsens_over_5_percent": "No slice worsens >5%",
    "unknown_surge_scored_only_after_observation": "Unknown surge scored causally",
}
REGIMES = ("normal", "scheduled_event", "detected_surge", "outage", "recovery")
REGIME_LABELS = ("Normal", "Scheduled event", "Detected surge", "Outage", "Recovery")
HORIZONS = (10, 20, 30, 80)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, payload: Any) -> None:
    _atomic_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10.5,
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


def _title(fig: Any, heading: str, subtitle: str, *, footer: bool = True) -> None:
    fig.suptitle(heading, x=0.055, y=0.975, ha="left", fontsize=20,
                 fontweight="bold", color=NAVY)
    fig.text(0.055, 0.925, subtitle, color=MUTED, fontsize=10.5)
    if footer:
        fig.text(0.945, 0.025, "Synthetic seed-46002 selection · seed 46003 untouched",
                 ha="right", color=MUTED, fontsize=8.5)


def _save(fig: Any, figures: Path, stem: str) -> list[Path]:
    figures.mkdir(parents=True, exist_ok=True)
    outputs = []
    for suffix in ("png", "svg"):
        target = figures / f"{stem}.{suffix}"
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        fig.savefig(temporary, format=suffix, dpi=240 if suffix == "png" else None,
                    bbox_inches="tight", facecolor="white")
        os.replace(temporary, target)
        outputs.append(target)
    plt.close(fig)
    return outputs


def _load_evidence(root: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    selection = _read(root / "forecast-selection-v3.json")
    if selection.get("protected_test_seed_consumed") is not False:
        raise ValueError("protected seed state is not fail-closed")
    candidates = {row["model_family"]: row for row in selection["candidates"]}
    if set(candidates) != set(FAMILIES):
        raise ValueError("forecast selection family set is incomplete")
    metrics: dict[str, list[dict[str, Any]]] = {}
    for family in FAMILIES:
        paths = sorted((root / "selection-v3" / family).glob("metrics-*.json"))
        rows = [_read(path) for path in paths]
        if len(rows) != 96 or {row["group_index"] for row in rows} != set(range(96)):
            raise ValueError(f"{family} does not have 96 unique metric shards")
        metrics[family] = rows
    return selection, metrics


def _weighted_slice(rows: list[dict[str, Any]], field: str) -> float:
    total = sum(float(row["actual_sum"]) for row in rows)
    return sum(float(row[field]) * float(row["actual_sum"]) for row in rows) / total if total else 0.0


def _slice_matrix(metrics: dict[str, list[dict[str, Any]]], dimension: str) -> dict[str, dict[Any, dict[str, float]]]:
    values: dict[str, dict[Any, dict[str, float]]] = {}
    for family, groups in metrics.items():
        family_values: dict[Any, dict[str, float]] = {}
        keys = REGIMES if dimension == "regime" else HORIZONS
        for key in keys:
            slices = [
                item for group in groups for item in group["slices"]
                if item[dimension if dimension == "regime" else "horizon_minutes"] == key
            ]
            candidate = _weighted_slice(slices, "wape")
            baseline = _weighted_slice(slices, "moving_average_wape")
            family_values[key] = {
                "candidate_wape": candidate,
                "baseline_wape": baseline,
                "relative_improvement": (baseline - candidate) / baseline if baseline else 0.0,
                "count": sum(int(row["count"]) for row in slices),
            }
        values[family] = family_values
    return values


def _wape_figure(candidates: dict[str, dict[str, Any]], figures: Path) -> list[Path]:
    baseline = candidates[FAMILIES[0]]["best_simple_baseline_wape"]
    seasonal = []
    # Seasonal baseline is identical across families and reconstructed from all group metrics later;
    # the figure intentionally focuses on the frozen best-simple comparator.
    values = [100 * candidates[f]["wape"] for f in FAMILIES]
    improvements = [100 * candidates[f]["relative_wape_improvement"] for f in FAMILIES]
    colors = [COLORS[f] for f in FAMILIES]
    fig, axes = plt.subplots(1, 2, figsize=(13.33, 7.5), gridspec_kw={"wspace": 0.30})
    x = np.arange(len(FAMILIES))
    bars = axes[0].bar(x, values, color=colors, width=0.66)
    axes[0].axhline(100 * baseline, color=NAVY, linestyle="--", linewidth=1.7,
                    label=f"Best simple baseline · {100*baseline:.2f}%")
    axes[0].axhline(100 * baseline * 0.85, color=CORAL, linestyle=":", linewidth=2.1,
                    label=f"15% gate · {100*baseline*0.85:.2f}%")
    axes[0].set_xticks(x, [LABELS[f] for f in FAMILIES], rotation=22, ha="right")
    axes[0].set_ylabel("Weighted absolute percentage error (%)")
    axes[0].set_ylim(0, max(values) * 1.20)
    axes[0].set_title("Held-out WAPE", loc="left", color=NAVY)
    axes[0].yaxis.grid(True); axes[0].set_axisbelow(True); axes[0].legend(loc="upper left")
    for bar, value in zip(bars, values):
        axes[0].text(bar.get_x()+bar.get_width()/2, value+0.35, f"{value:.2f}%",
                     ha="center", color=NAVY, fontweight="bold", fontsize=9)
    bars = axes[1].barh(x, improvements, color=colors, height=0.62)
    axes[1].axvline(15, color=CORAL, linestyle=":", linewidth=2.1, label="Frozen gate · ≥15%")
    axes[1].axvline(0, color=NAVY, linewidth=1)
    axes[1].set_yticks(x, [LABELS[f] for f in FAMILIES]); axes[1].invert_yaxis()
    axes[1].set_xlabel("Improvement over moving average (%)")
    axes[1].set_xlim(-25, 17)
    axes[1].set_title("Relative to best simple baseline", loc="left", color=NAVY)
    axes[1].xaxis.grid(True); axes[1].set_axisbelow(True); axes[1].legend(loc="lower right")
    for bar, value in zip(bars, improvements):
        if value >= 0:
            label_x, alignment, label_color = value + 0.35, "left", NAVY
        else:
            label_x, alignment, label_color = value + 0.55, "left", "white"
        axes[1].text(label_x, bar.get_y()+bar.get_height()/2, f"{value:+.2f}%",
                     va="center", ha=alignment, color=label_color, fontweight="bold", fontsize=9)
    _title(fig, "Forecast accuracy improved—but not enough to earn the protected test",
           "Five frozen families · 96 groups · seed 46002 second-half selection · lower WAPE is better")
    return _save(fig, figures, "01_forecast_wape_vs_simple_baseline")


def _peak_figure(candidates: dict[str, dict[str, Any]], figures: Path) -> list[Path]:
    improvements = []
    for family in FAMILIES:
        row = candidates[family]
        base = row["baseline_event_peak_underprediction"]
        improvements.append(100 * (base - row["event_peak_underprediction"]) / base)
    fig, ax = plt.subplots(figsize=(13.33, 7.5))
    y = np.arange(len(FAMILIES))
    bars = ax.barh(y, improvements, color=[COLORS[f] for f in FAMILIES], height=0.62)
    ax.axvline(20, color=CORAL, linestyle=":", linewidth=2.2, label="Frozen gate · ≥20% reduction")
    ax.axvspan(20, max(max(improvements) * 1.12, 70), color=GREEN, alpha=0.07)
    ax.set_yticks(y, [LABELS[f] for f in FAMILIES]); ax.invert_yaxis()
    ax.set_xlabel("Scheduled/detected event peak-underprediction reduction (%)")
    ax.set_xlim(0, max(improvements) * 1.16); ax.xaxis.grid(True); ax.set_axisbelow(True)
    ax.legend(loc="upper left")
    for bar, value in zip(bars, improvements):
        ax.text(value+0.8, bar.get_y()+bar.get_height()/2, f"{value:.1f}%",
                va="center", color=NAVY, fontweight="bold")
    ax.text(20.7, -0.52, "PASS REGION", color=GREEN, fontweight="bold", fontsize=9)
    _title(fig, "Causal event features materially reduced peak misses",
           "Peak underprediction summed across scheduled-event and causally detected-surge episodes")
    return _save(fig, figures, "02_event_peak_underprediction")


def _coverage_figure(candidates: dict[str, dict[str, Any]], figures: Path) -> list[Path]:
    coverage = [100 * candidates[f]["coverage_p90"] for f in FAMILIES]
    fig, ax = plt.subplots(figsize=(13.33, 7.5))
    y = np.arange(len(FAMILIES))
    ax.axvspan(88, 95, color=GREEN, alpha=0.12, label="Accepted calibration band · 88–95%")
    ax.hlines(y, 86, coverage, color=GRID, linewidth=4)
    ax.scatter(coverage, y, s=180, color=[COLORS[f] for f in FAMILIES], edgecolor="white", linewidth=1.5, zorder=3)
    ax.axvline(90, color=NAVY, linestyle="--", linewidth=1.4, label="Nominal p90")
    ax.set_yticks(y, [LABELS[f] for f in FAMILIES]); ax.invert_yaxis()
    ax.set_xlim(86, 97); ax.set_xlabel("Observed p90 interval coverage (%)")
    ax.xaxis.grid(True); ax.set_axisbelow(True); ax.legend(loc="center left", bbox_to_anchor=(0.015, 0.50))
    for index, value in enumerate(coverage):
        ax.text(value+0.18, index, f"{value:.2f}%", va="center", color=NAVY, fontweight="bold")
    _title(fig, "Uncertainty calibration passed for every candidate",
           "First-half residual calibration applied once; coverage measured only on the seed-46002 second half")
    return _save(fig, figures, "03_p90_coverage_calibration")


def _tail_figure(candidates: dict[str, dict[str, Any]], figures: Path) -> list[Path]:
    regular = [family for family in FAMILIES if family != "regime-ensemble"]
    fig, (left, right) = plt.subplots(1, 2, figsize=(13.33, 7.5), gridspec_kw={"width_ratios": (2.35, 1), "wspace": 0.26})
    values = [100 * candidates[f]["max_slice_regression"] for f in regular]
    y = np.arange(len(regular))
    bars = left.barh(y, values, color=[COLORS[f] for f in regular], height=0.62)
    left.axvline(5, color=CORAL, linestyle=":", linewidth=2.2, label="Frozen limit · ≤5%")
    left.axvspan(0, 5, color=GREEN, alpha=0.08)
    left.set_yticks(y, [LABELS[f] for f in regular]); left.invert_yaxis()
    left.set_xlim(0, 18); left.set_xlabel("Worst aggregate regime/horizon regression (%)")
    left.xaxis.grid(True); left.set_axisbelow(True); left.legend(loc="lower right")
    left.set_title("Main candidates", loc="left", color=NAVY)
    for bar, value in zip(bars, values):
        left.text(value+0.3, bar.get_y()+bar.get_height()/2, f"+{value:.1f}%",
                  va="center", color=NAVY, fontweight="bold")
    regime_value = 100 * candidates["regime-ensemble"]["max_slice_regression"]
    right.barh([0], [regime_value], color=GOLD, height=0.42)
    right.axvline(5, color=CORAL, linestyle=":", linewidth=2.2)
    right.set_yticks([0], ["Regime ensemble"]); right.set_xlim(0, 650)
    right.set_xlabel("Worst regression (%)"); right.xaxis.grid(True); right.set_axisbelow(True)
    right.set_title("Outlier scale", loc="left", color=NAVY)
    right.text(regime_value-12, 0, f"+{regime_value:.1f}%", va="center", ha="right",
               color=NAVY, fontweight="bold")
    _title(fig, "Tail regressions blocked promotion despite good average accuracy",
           "Maximum weighted regression across every target × horizon × observable regime slice")
    return _save(fig, figures, "04_worst_slice_guardrail")


def _gate_figure(candidates: dict[str, dict[str, Any]], figures: Path) -> list[Path]:
    gates = list(GATE_LABELS)
    matrix = np.asarray([[1 if candidates[f]["gates"][g] else 0 for g in gates] for f in FAMILIES])
    fig, ax = plt.subplots(figsize=(13.33, 7.5))
    from matplotlib.colors import ListedColormap
    ax.imshow(matrix, cmap=ListedColormap(["#FCE8EA", "#E5F5EC"]), vmin=0, vmax=1, aspect="auto")
    compact_gate_labels = (
        "WAPE gain\n≥15%", "Peak-miss reduction\n≥20%", "p90 coverage\n88–95%",
        "Worst regression\n≤5%", "Causal surge\nscoring",
    )
    ax.set_xticks(np.arange(len(gates)), compact_gate_labels)
    ax.set_yticks(np.arange(len(FAMILIES)), [LABELS[f] for f in FAMILIES])
    for i in range(len(FAMILIES)):
        for j in range(len(gates)):
            passed = bool(matrix[i, j])
            ax.text(j, i, "PASS" if passed else "FAIL", ha="center", va="center",
                    color=GREEN if passed else CORAL, fontweight="bold", fontsize=10)
    ax.set_xticks(np.arange(-0.5, len(gates), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(FAMILIES), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=2.5); ax.tick_params(which="minor", bottom=False, left=False)
    ax.tick_params(axis="x", pad=11, labelsize=9.5)
    fig.subplots_adjust(bottom=0.17)
    _title(fig, "No model cleared the complete fail-closed selection contract",
           "Promotion requires every column to pass; favourable averages cannot override a failed safety or causality gate",
           footer=False)
    return _save(fig, figures, "05_fail_closed_gate_scorecard")


def _heatmap_figure(values: dict[str, dict[Any, dict[str, float]]], keys: tuple[Any, ...], labels: tuple[str, ...], figures: Path, stem: str, heading: str, subtitle: str) -> list[Path]:
    matrix = np.asarray([[100 * values[f][key]["relative_improvement"] for key in keys] for f in FAMILIES])
    clipped = np.clip(matrix, -50, 50)
    fig, ax = plt.subplots(figsize=(13.33, 7.5))
    image = ax.imshow(clipped, cmap="RdYlGn", norm=TwoSlopeNorm(vmin=-50, vcenter=0, vmax=50), aspect="auto")
    ax.set_xticks(np.arange(len(keys)), labels)
    ax.set_yticks(np.arange(len(FAMILIES)), [LABELS[f] for f in FAMILIES])
    for i in range(len(FAMILIES)):
        for j in range(len(keys)):
            value = matrix[i, j]
            ax.text(j, i, f"{value:+.1f}%", ha="center", va="center",
                    color="white" if abs(clipped[i, j]) > 28 else NAVY, fontweight="bold")
    colorbar = fig.colorbar(image, ax=ax, pad=0.025, shrink=0.78)
    colorbar.set_label("WAPE improvement over moving average (%)")
    ax.set_xticks(np.arange(-0.5, len(keys), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(FAMILIES), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=2.0); ax.tick_params(which="minor", bottom=False, left=False)
    fig.subplots_adjust(left=0.16, right=0.91, bottom=0.14, top=0.83)
    _title(fig, heading, subtitle)
    return _save(fig, figures, stem)


def _observability_figure(metrics: dict[str, list[dict[str, Any]]], figures: Path) -> tuple[list[Path], dict[str, int]]:
    rows = metrics["lightgbm-quantile"]
    excluded = sum(int(row["excluded_pre_observation_unknown_surge_rows"]) for row in rows)
    scored = sum(int(row["overall"]["count"]) for row in rows)
    detected = sum(int(item["count"]) for row in rows for item in row["slices"] if item["regime"] == "detected_surge")
    scheduled = sum(int(item["count"]) for row in rows for item in row["slices"] if item["regime"] == "scheduled_event")
    fig, ax = plt.subplots(figsize=(13.33, 7.5)); ax.axis("off")
    boxes = (
        (0.03, 0.57, 0.23, 0.22, BLUE, "TARGET WINDOW", f"{scored + excluded:,} rows\ncandidate + protected"),
        (0.35, 0.70, 0.30, 0.19, CORAL, "NOT YET OBSERVABLE", f"{excluded:,} rows excluded\nbefore first signal"),
        (0.35, 0.39, 0.30, 0.19, GREEN, "CAUSALLY OBSERVABLE", f"{detected:,} detected-surge rows\nscored after first signal"),
        (0.72, 0.53, 0.25, 0.22, PURPLE, "HELD-OUT METRICS", f"{scored:,} rows scored\n0 pre-signal rows leaked"),
    )
    for x, y, w, h, color, title, body in boxes:
        ax.add_patch(plt.Rectangle((x, y), w, h, transform=ax.transAxes, facecolor=PALE,
                                   edgecolor=color, linewidth=2.2))
        ax.add_patch(plt.Rectangle((x, y+h-0.045), w, 0.045, transform=ax.transAxes,
                                   facecolor=color, edgecolor=color))
        ax.text(x+0.02, y+h-0.022, title, transform=ax.transAxes, va="center",
                color="white", fontsize=9, fontweight="bold")
        ax.text(x+w/2, y+0.075, body, transform=ax.transAxes, ha="center", va="center",
                color=NAVY, fontsize=10.5, fontweight="bold", linespacing=1.45)
    arrow = dict(arrowstyle="-|>", color=MUTED, lw=2.0, mutation_scale=16)
    ax.annotate("", xy=(0.35, 0.795), xytext=(0.26, 0.685), xycoords=ax.transAxes, arrowprops=arrow)
    ax.annotate("", xy=(0.35, 0.485), xytext=(0.26, 0.685), xycoords=ax.transAxes, arrowprops=arrow)
    ax.annotate("", xy=(0.72, 0.64), xytext=(0.65, 0.485), xycoords=ax.transAxes, arrowprops=arrow)
    ax.text(0.285, 0.79, "before signal", transform=ax.transAxes, color=CORAL, fontsize=9, fontweight="bold")
    ax.text(0.275, 0.50, "after signal", transform=ax.transAxes, color=GREEN, fontsize=9, fontweight="bold")
    ax.text(0.72, 0.32, f"Scheduled-event rows scored: {scheduled:,}\nAvailability enforced per feature via available_at",
            transform=ax.transAxes, ha="right", color=MUTED, fontsize=10)
    _title(fig, "Unknown events become usable only after evidence exists",
           "The offline selection replay applies the same causal availability contract as live observations")
    return _save(fig, figures, "08_causal_unknown_surge_observability"), {
        "scored_rows": scored, "excluded_pre_observation_rows": excluded,
        "detected_surge_rows": detected, "scheduled_event_rows": scheduled,
    }


def _pipeline_figure(completion: dict[str, Any], figures: Path) -> list[Path]:
    fig, ax = plt.subplots(figsize=(13.33, 7.5)); ax.axis("off")
    stages = (
        (0.03, BLUE, "CAUSAL INPUT", "Rate-bin labels\nregime + event\nanomaly + quality"),
        (0.23, CYAN, "FROZEN CACHE", "96 groups × 2\n387,072 obs each\nseeds 46001/2"),
        (0.43, PURPLE, "REPRODUCIBLE MODEL", "train → serialize\nhash → load\n4 challengers"),
        (0.63, GOLD, "FAIL-CLOSED METRICS", "group + horizon\nregime + episode\ncoverage + peaks"),
        (0.83, GREEN, "SAFETY PLUMBING", "survival provenance\nchurn + reasons\nseed locks"),
    )
    for x, color, heading, body in stages:
        ax.add_patch(FancyBboxPatch((x, 0.48), 0.15, 0.28, boxstyle="round,pad=0.012,rounding_size=0.015",
                                       transform=ax.transAxes, facecolor=PALE, edgecolor=color, linewidth=2.2))
        ax.text(x+0.075, 0.70, heading, transform=ax.transAxes, ha="center", color=color,
                fontsize=9.2, fontweight="bold")
        ax.text(x+0.075, 0.57, body, transform=ax.transAxes, ha="center", va="center",
                color=NAVY, fontsize=10.5, fontweight="bold", linespacing=1.45)
        if x < 0.83:
            ax.annotate("", xy=(x+0.195, 0.62), xytext=(x+0.155, 0.62), xycoords=ax.transAxes,
                        arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=2, mutation_scale=15))
    ax.add_patch(FancyBboxPatch((0.15, 0.20), 0.70, 0.14, boxstyle="round,pad=0.012",
                                   transform=ax.transAxes, facecolor="#E5F5EC", edgecolor=GREEN, linewidth=1.8))
    ax.text(0.50, 0.27, "PHASE 1 FROZEN · 17/17 interface hashes revalidated\n157 passed · 1 unrelated frozen input absent · release evidence fails closed",
            transform=ax.transAxes, ha="center", va="center", color=NAVY, fontsize=10.5, fontweight="bold", linespacing=1.35)
    _title(fig, "Phase 1 closed the experiment-plumbing gaps before model selection",
           "One observation contract now connects offline training, live forecasting, model packaging and controller evidence")
    return _save(fig, figures, "09_phase1_plumbing_and_reproducibility")


def _scale_figure(completion: dict[str, Any], figures: Path) -> list[Path]:
    counts = completion["artifact_counts"]
    labels = ("Train\ncache", "Selection\ncache", "Trained\nbundles", "Calibrated\nbundles", "Metric\nshards", "Audit\nshards")
    values = (counts["training_cache_groups"], counts["selection_cache_groups"], counts["trained_candidate_bundles"], counts["calibrated_candidate_bundles"], counts["selection_metric_shards"], completion["artifact_audit"]["shards"])
    colors = (BLUE, CYAN, PURPLE, GOLD, GREEN, CORAL)
    fig, axes = plt.subplots(1, 2, figsize=(13.33, 7.5), gridspec_kw={"width_ratios": (1.45, 1), "wspace": 0.28})
    x = np.arange(len(values)); bars = axes[0].bar(x, values, color=colors, width=0.66)
    axes[0].set_xticks(x, labels); axes[0].set_ylabel("Artifacts / shards")
    axes[0].set_ylim(0, 560); axes[0].yaxis.grid(True); axes[0].set_axisbelow(True)
    axes[0].set_title("Authoritative campaign outputs", loc="left", color=NAVY)
    for bar, value in zip(bars, values):
        axes[0].text(bar.get_x()+bar.get_width()/2, value+11, f"{value:,}", ha="center",
                     color=NAVY, fontweight="bold")
    axes[1].axis("off")
    cards = (
        (0.08, 0.68, BLUE, "160 NODES", "20,000 usable CPUs available"),
        (0.08, 0.47, PURPLE, "480 SELECTION SHARDS", "Five 96-group arrays submitted concurrently"),
        (0.08, 0.26, GREEN, "768 BUNDLES RELOADED", "Every trained + calibrated challenger verified"),
        (0.08, 0.05, CORAL, "0 PROTECTED TEST RUNS", "Seed 46003 stayed behind the gate"),
    )
    for x0, y0, color, heading, body in cards:
        axes[1].add_patch(FancyBboxPatch((x0, y0), 0.84, 0.15, boxstyle="round,pad=0.012",
                                            transform=axes[1].transAxes, facecolor=PALE, edgecolor=color, linewidth=1.8))
        axes[1].text(x0+0.04, y0+0.10, heading, transform=axes[1].transAxes,
                     color=color, fontsize=12, fontweight="bold")
        axes[1].text(x0+0.04, y0+0.045, body, transform=axes[1].transAxes, color=NAVY, fontsize=10)
    axes[1].set_title("Cluster and release discipline", loc="left", color=NAVY)
    _title(fig, "The campaign was sharded for cluster-scale execution and independently audited",
           "Counts come from the sealed Phase-2 completion record; cluster capacity is not presented as measured utilization")
    return _save(fig, figures, "10_cluster_campaign_and_artifact_scale")


def _architecture_figure(figures: Path) -> list[Path]:
    fig, ax = plt.subplots(figsize=(13.33, 7.5)); ax.axis("off")
    def box(x: float, y: float, w: float, h: float, color: str, heading: str, body: str) -> None:
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.015",
                                       transform=ax.transAxes, facecolor=PALE, edgecolor=color, linewidth=2.1))
        ax.text(x+w/2, y+h*0.68, heading, transform=ax.transAxes, ha="center", color=color,
                fontsize=11, fontweight="bold")
        ax.text(x+w/2, y+h*0.31, body, transform=ax.transAxes, ha="center", va="center",
                color=NAVY, fontsize=10, fontweight="bold", linespacing=1.3)
    box(0.03, 0.48, 0.16, 0.23, BLUE, "TELEMETRY", "load · health · quality\nevent availability")
    box(0.26, 0.48, 0.18, 0.23, PURPLE, "CAUSAL FORECAST", "current production model\nconfidence + event state")
    box(0.51, 0.48, 0.17, 0.23, GOLD, "EVENT GATE", "known scheduled\ncapacity inside\nplanning horizon?")
    box(0.77, 0.63, 0.19, 0.18, GREEN, "STATIC", "normal / unknown\nobserved unplanned fault")
    box(0.77, 0.33, 0.19, 0.18, CYAN, "GUARDED MPC", "declared capacity event\nsolve failure → Static")
    arrow = dict(arrowstyle="-|>", color=MUTED, lw=2.2, mutation_scale=16)
    ax.annotate("", xy=(0.26, 0.595), xytext=(0.19, 0.595), xycoords=ax.transAxes, arrowprops=arrow)
    ax.annotate("", xy=(0.51, 0.595), xytext=(0.44, 0.595), xycoords=ax.transAxes, arrowprops=arrow)
    ax.annotate("", xy=(0.77, 0.72), xytext=(0.68, 0.62), xycoords=ax.transAxes, arrowprops=arrow)
    ax.annotate("", xy=(0.77, 0.42), xytext=(0.68, 0.57), xycoords=ax.transAxes, arrowprops=arrow)
    ax.text(0.71, 0.72, "NO / UNKNOWN", transform=ax.transAxes, color=GREEN, fontsize=8.5, fontweight="bold")
    ax.text(0.71, 0.45, "YES", transform=ax.transAxes, color=CYAN, fontsize=8.5, fontweight="bold")
    ax.add_patch(FancyBboxPatch((0.16, 0.16), 0.68, 0.13, boxstyle="round,pad=0.012",
                                   transform=ax.transAxes, facecolor="#FCE8EA", edgecolor=CORAL, linewidth=1.8))
    ax.text(0.50, 0.225, "PHASE-2 DECISION · no challenger promoted · seed 46003 locked\narchitecture remains guarded",
            transform=ax.transAxes, ha="center", va="center", color=NAVY, fontsize=10.5, fontweight="bold", linespacing=1.35)
    _title(fig, "The defensible deployment architecture is Static-first and event-gated",
           "Forecast evidence informs a bounded controller role; it does not justify universal MPC or a challenger promotion")
    return _save(fig, figures, "11_guarded_hybrid_architecture")


def _report(selection: dict[str, Any], completion: dict[str, Any], observability: dict[str, int], figure_names: list[str]) -> str:
    candidates = {row["model_family"]: row for row in selection["candidates"]}
    lines = [
        "# C-DOT Forecast Phase 1/2 Showcase Report", "",
        f"Generated: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}", "",
        "## Executive conclusion", "",
        "Phase 1 is complete and hash-frozen. Phase 2 trained, calibrated and evaluated five forecast families across all 96 traffic groups. **No candidate passed every frozen selection gate, so seed 46003 was not opened and no challenger was promoted.**", "",
        "This is a useful release-discipline result, not an unfinished campaign. LightGBM and histogram-gradient reduced WAPE by about 11.6% and scheduled/detected event peak underprediction by about 27.5%, but missed the required 15% WAPE improvement and exceeded the 5% maximum regime/horizon regression guardrail.", "",
        "## Candidate scorecard", "",
        "| Candidate | WAPE | vs moving average | Event peak reduction | p90 coverage | Worst slice | Eligible |", "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for family in FAMILIES:
        row = candidates[family]
        peak = (row["baseline_event_peak_underprediction"] - row["event_peak_underprediction"]) / row["baseline_event_peak_underprediction"]
        lines.append(f"| {LABELS[family]} | {row['wape']:.2%} | {row['relative_wape_improvement']:+.2%} | {peak:+.2%} | {row['coverage_p90']:.2%} | {row['max_slice_regression']:+.2%} | {'Yes' if row['eligible'] else 'No'} |")
    lines.extend(["", "## Figures", ""])
    captions = {
        "01_forecast_wape_vs_simple_baseline": "Overall held-out accuracy and the frozen 15% promotion threshold.",
        "02_event_peak_underprediction": "Scheduled/detected event peak-underprediction reduction.",
        "03_p90_coverage_calibration": "Observed p90 coverage against the accepted calibration band.",
        "04_worst_slice_guardrail": "Worst aggregate regime/horizon regression that blocked promotion.",
        "05_fail_closed_gate_scorecard": "Gate-by-gate pass/fail scorecard.",
        "06_wape_improvement_by_regime": "Per-regime WAPE improvement over moving average.",
        "07_wape_improvement_by_horizon": "Per-horizon WAPE improvement over moving average.",
        "08_causal_unknown_surge_observability": "Causal availability and unknown-surge scoring exclusion.",
        "09_phase1_plumbing_and_reproducibility": "Phase-1 experiment plumbing and frozen interfaces.",
        "10_cluster_campaign_and_artifact_scale": "PBS campaign decomposition and artifact audit scale.",
        "11_guarded_hybrid_architecture": "Static-first, scheduled-event-gated deployment architecture.",
    }
    for name in figure_names:
        lines.extend([f"### {name.replace('_', ' ').title()}", "", f"![{captions[name]}](figures/{name}.png)", "", captions[name], ""])
    lines.extend([
        "## Causal scoring and evidence integrity", "",
        f"- {observability['excluded_pre_observation_rows']:,} pre-observation unknown-surge target rows were excluded per family.",
        f"- {observability['detected_surge_rows']:,} detected-surge rows were scored only after the first observable signal.",
        f"- {observability['scored_rows']:,} held-out target rows were scored per family.",
        "- All 17 frozen Phase-1 interface hashes were revalidated after the campaign.",
        "- 384 trained and 384 calibrated bundles were checksum-loaded through the production loader.",
        "- 480 group metric shards and 96 independent artifact-audit shards are sealed in the completion record.", "",
        "## Presentation boundaries", "",
        "- These are deterministic synthetic results, not C-DOT traffic calibration or live-network validation.",
        "- Do not describe any forecast challenger as promoted.",
        "- Do not describe seed 46003 as tested; it remains protected because selection failed.",
        "- Empirical-survival outcome experiments are Phase 3 and are not claimed here.",
        "- Static remains the production controller winner; guarded scheduled-event MPC remains the recommended next controller experiment.", "",
        "## Authoritative inputs", "",
        "- `../forecast-selection-v3.json` — complete model metrics and gates.",
        "- `../phase2-completion-v3.json` — artifact counts, tree hashes and PBS job identities.",
        "- `../phase1-freeze.json` — frozen interfaces, environment and seed policy.", "",
    ])
    return "\n".join(lines)


def _talk_track(selection: dict[str, Any]) -> str:
    return """# C-DOT Forecast Phase 1/2 Talk Track

## Opening

Phase 1 closed the experimental plumbing gaps before any model comparison: causal event availability, telemetry-quality replay, checksum model packaging, empirical-survival provenance, fail-closed release evidence, and protected seed partitions. Phase 2 then ran five frozen model families across all 96 groups.

## Figure 1 — WAPE

The moving-average reference is 14.16% WAPE. LightGBM and histogram-gradient reach about 12.51%, an 11.6% relative improvement. That is meaningful, but below the precommitted 15% gate. We do not lower the gate after seeing the answer.

## Figure 2 — Peak underprediction

The causal challengers do better around scheduled and detected events. LightGBM and histogram-gradient reduce peak misses by about 27.5%; Ridge-v2 reduces them by 25.8%. This gate passes for four causal challengers except calendar ridge.

## Figure 3 — Coverage

Every model lands inside the accepted 88–95% p90 coverage band. Calibration worked, but calibration alone is not promotion.

## Figure 4 — Tail guardrail

Every candidate has at least one aggregate regime/horizon slice more than 5% worse than the moving average. The strongest nonlinear models regress about 14–15% in their worst slice. The regime ensemble is an extreme failure at more than 600%.

## Figure 5 — Gate scorecard

Promotion is conjunctive: every column must pass. There is no eligible row, which is why protected test seed 46003 remains untouched.

## Figures 6 and 7 — Regime and horizon detail

These heatmaps explain why one global WAPE is insufficient. Gains are not uniform across operating regimes or planning horizons. Quote a cell only with its regime/horizon context.

## Figure 8 — Causal observability

Unknown events are not labelled as knowable before evidence exists. Pre-signal target rows are excluded; detected-surge performance is measured only after the first observable signal. This prevents look-ahead leakage.

## Figure 9 — Phase 1 plumbing

The key contribution is not just another model. Offline and live observations now share one causal metadata contract; bundles are serialized, hashed and production-loaded; controller campaigns emit churn, solver and survival provenance; missing release fields fail closed.

## Figure 10 — Cluster evidence

The work was divided into hundreds of independent PBS shards: 384 trained bundles, 384 calibrated bundles, 480 metric shards and 96 independent verification shards. Cluster capacity is shown as available infrastructure, not claimed utilization.

## Figure 11 — Architecture

The honest deployment direction is Static-first. A guarded MPC branch is considered only for a causally known scheduled capacity event. Unknown or observed unplanned faults fall back immediately to Static.

## Close

The result is a disciplined negative promotion decision with positive scientific learning: causal event features help peak forecasting, uncertainty calibration is sound, and the release process correctly refuses a model with insufficient average gain and unsafe slices.
"""


def _pdf(output: Path, pngs: list[Path], candidates: dict[str, dict[str, Any]]) -> Path:
    target = output / "cdot_forecast_phase2_showcase.pdf"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with PdfPages(temporary) as pdf:
        fig = plt.figure(figsize=(13.33, 7.5))
        fig.text(0.065, 0.84, "C-DOT · CONTROL SCIENCE", fontsize=15, color=BLUE, fontweight="bold")
        fig.text(0.065, 0.61, "Causal Forecasting\nPhase 1/2 Evidence", fontsize=34, color=NAVY,
                 fontweight="bold", linespacing=1.08)
        fig.text(0.065, 0.43, "Five model families · 96 traffic groups · fail-closed selection",
                 fontsize=15, color=MUTED)
        fig.text(0.065, 0.25, "AUTHORITATIVE DECISION", fontsize=10, color=CORAL, fontweight="bold")
        fig.text(0.065, 0.15, "No challenger promoted.\nProtected seed 46003 remains untouched.",
                 fontsize=22, color=NAVY, fontweight="bold", linespacing=1.3)
        fig.text(0.935, 0.06, "Synthetic evidence · 19 August 2026", ha="right", fontsize=9, color=MUTED)
        plt.axis("off"); pdf.savefig(fig, facecolor="white"); plt.close(fig)

        fig = plt.figure(figsize=(13.33, 7.5)); plt.axis("off")
        fig.text(0.055, 0.90, "Executive scorecard", fontsize=27, color=NAVY, fontweight="bold")
        cards = (
            (0.055, 0.62, BLUE, "PHASE 1", "17/17 interface hashes match\ncausal metadata + packaging + seed locks"),
            (0.525, 0.62, PURPLE, "BEST ACCURACY", "LightGBM · 12.51% WAPE\n11.64% better than moving average"),
            (0.055, 0.33, GREEN, "EVENT BENEFIT", "27.54% lower peak underprediction\nall candidate p90 coverage calibrated"),
            (0.525, 0.33, CORAL, "PROMOTION DECISION", "0/5 candidates eligible\nseed 46003 not consumed"),
        )
        for x, y, color, heading, body in cards:
            fig.patches.append(plt.Rectangle((x, y), 0.40, 0.21, transform=fig.transFigure,
                                              facecolor=PALE, edgecolor=GRID, linewidth=1.2))
            fig.patches.append(plt.Rectangle((x, y), 0.012, 0.21, transform=fig.transFigure,
                                              facecolor=color, edgecolor=color))
            fig.text(x+0.035, y+0.15, heading, color=color, fontsize=11, fontweight="bold")
            fig.text(x+0.035, y+0.06, body, color=NAVY, fontsize=12, fontweight="bold", linespacing=1.45)
        fig.text(0.055, 0.16, "WHY NO PROMOTION", color=CORAL, fontsize=10, fontweight="bold")
        fig.text(0.055, 0.09, "Best WAPE gain was below 15%; every model exceeded the 5% worst-slice regression guardrail.",
                 color=NAVY, fontsize=13, fontweight="bold")
        pdf.savefig(fig, facecolor="white"); plt.close(fig)

        for path in pngs:
            image = plt.imread(path)
            fig, ax = plt.subplots(figsize=(13.33, 7.5)); ax.imshow(image); ax.axis("off")
            pdf.savefig(fig, facecolor="white", bbox_inches="tight"); plt.close(fig)
    os.replace(temporary, target)
    return target


def _html(output: Path, pngs: list[Path]) -> Path:
    cards = []
    for path in pngs:
        relative = path.relative_to(output).as_posix()
        title = path.stem.split("_", 1)[1].replace("_", " ").title()
        cards.append(f'<section class="card"><h2>{html.escape(title)}</h2><a href="{relative}"><img src="{relative}" alt="{html.escape(title)}"></a></section>')
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>C-DOT Forecast Phase 1/2 Evidence</title>
<style>body{{margin:0;background:#eef3f8;color:{NAVY};font:16px DejaVu Sans,Arial,sans-serif}}header{{padding:48px 7%;background:{NAVY};color:white}}header b{{color:#57c7ff;letter-spacing:.12em}}main{{max-width:1280px;margin:auto;padding:28px}}.card{{background:white;padding:22px;margin:24px 0;border-radius:12px;box-shadow:0 4px 18px #0b1f3320}}img{{width:100%;height:auto}}a{{color:{BLUE}}}.decision{{color:#ff8c96;font-weight:bold;font-size:1.25rem}}</style></head>
<body><header><b>C-DOT · CONTROL SCIENCE</b><h1>Causal Forecasting Phase 1/2 Evidence</h1><p class="decision">No challenger promoted · protected seed 46003 untouched</p><p>Five frozen families · 96 groups · reproducible, fail-closed selection</p></header><main>{''.join(cards)}</main></body></html>"""
    target = output / "index.html"; _atomic_text(target, page); return target


def _manifest(output: Path, sources: list[Path], artifacts: list[Path]) -> Path:
    payload = {
        "schema_version": "forecast-phase2-showcase-manifest/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "claim_boundary": "deterministic synthetic evidence; no C-DOT calibration or live-network validation",
        "decision": "no candidate promoted; seed 46003 not consumed",
        "sources": [{"path": str(path.resolve()), "sha256": _sha256(path)} for path in sources],
        "artifacts": [{"path": str(path.relative_to(output)), "bytes": path.stat().st_size, "sha256": _sha256(path)} for path in sorted(artifacts)],
    }
    target = output / "artifact-manifest.json"; _atomic_json(target, payload); return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase2-root", type=Path, default=PROJECT_ROOT / "output/control-science/v1/forecast-phase2")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "output/control-science/v1/forecast-phase2/showcase-v1")
    args = parser.parse_args()
    root = args.phase2_root.resolve(); output = args.output.resolve(); figures = output / "figures"
    _style()
    selection, metrics = _load_evidence(root)
    completion = _read(root / "phase2-completion-v3.json")
    candidates = {row["model_family"]: row for row in selection["candidates"]}
    regime_values = _slice_matrix(metrics, "regime")
    horizon_values = _slice_matrix(metrics, "horizon_minutes")
    output.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    figure_outputs: list[Path] = []
    figure_outputs += _wape_figure(candidates, figures)
    figure_outputs += _peak_figure(candidates, figures)
    figure_outputs += _coverage_figure(candidates, figures)
    figure_outputs += _tail_figure(candidates, figures)
    figure_outputs += _gate_figure(candidates, figures)
    figure_outputs += _heatmap_figure(regime_values, REGIMES, REGIME_LABELS, figures,
                                      "06_wape_improvement_by_regime", "Average gains hide material regime dependence",
                                      "Weighted WAPE improvement over the moving-average reference; values below −50% are colour-clipped, not numerically clipped")
    figure_outputs += _heatmap_figure(horizon_values, HORIZONS, tuple(f"{h} min" for h in HORIZONS), figures,
                                      "07_wape_improvement_by_horizon", "Forecast value changes across the planning horizon",
                                      "Weighted across all groups, targets and observable regimes for each forecast horizon")
    observability_outputs, observability = _observability_figure(metrics, figures)
    figure_outputs += observability_outputs
    figure_outputs += _pipeline_figure(completion, figures)
    figure_outputs += _scale_figure(completion, figures)
    figure_outputs += _architecture_figure(figures)
    generated += figure_outputs
    pngs = sorted(path for path in figure_outputs if path.suffix == ".png")
    figure_names = [path.stem for path in pngs]

    data = {
        "schema_version": "forecast-phase2-showcase-data/1.0",
        "selection_sha256": _sha256(root / "forecast-selection-v3.json"),
        "completion_sha256": _sha256(root / "phase2-completion-v3.json"),
        "candidates": candidates,
        "wape_improvement_by_regime": regime_values,
        "wape_improvement_by_horizon": horizon_values,
        "observability": observability,
    }
    data_path = output / "figure-data.json"; _atomic_json(data_path, data); generated.append(data_path)
    report_path = output / "REPORT.md"; _atomic_text(report_path, _report(selection, completion, observability, figure_names)); generated.append(report_path)
    talk_path = output / "TALK_TRACK.md"; _atomic_text(talk_path, _talk_track(selection)); generated.append(talk_path)
    pdf_path = _pdf(output, pngs, candidates); generated.append(pdf_path)
    html_path = _html(output, pngs); generated.append(html_path)
    sources = [root / "forecast-selection-v3.json", root / "phase2-completion-v3.json", root / "phase1-freeze.json"]
    manifest = _manifest(output, sources, generated)
    print(json.dumps({"output": str(output), "figures": len(pngs), "artifacts": len(generated)+1,
                      "pdf": str(pdf_path), "manifest": str(manifest)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
