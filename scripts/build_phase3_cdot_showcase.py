#!/usr/bin/env python3
"""Build the C-DOT Phase-2.1/3 visual evidence and presentation package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "cdot-phase3-mpl"))
os.environ.setdefault("XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "cdot-phase3-cache"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import ListedColormap, TwoSlopeNorm
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


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
LIGHT_GREEN = "#DDF3E8"
LIGHT_CORAL = "#FDE4E7"

FORECAST_LABELS = {
    "calendar-ridge": "Calendar ridge",
    "hist-gradient-quantile": "Histogram gradient",
    "lightgbm-quantile": "LightGBM",
    "regime-ensemble": "Regime ensemble",
    "ridge-v2": "Ridge-v2",
}
CANDIDATE_LABELS = {
    "existing-baseline": "Existing MPC",
    "empirical-survival": "Empirical survival",
    "scheduled-only": "Scheduled-only h12/t2",
    "failure-domain": "Failure-domain",
    "conservative-combined": "Conservative combined",
    "calendar-optimistic-stale": "Calendar optimistic",
    "calendar-conservative": "Calendar conservative",
    "scheduled-h2-t10": "Scheduled h2/t10",
    "scheduled-h3-t10": "Scheduled h3/t10",
    "scheduled-h6-t10": "Scheduled h6/t10",
    "scheduled-h3-t30": "Scheduled h3/t30",
    "scheduled-h6-t30": "Scheduled h6/t30",
    "calendar-conservative-h3-t30": "Calendar cons. h3/t30",
}
SCENARIOS = ("surge", "scheduled_fault", "unannounced_outage", "mixed_stress")
SCENARIO_LABELS = ("Surge", "Scheduled fault", "Unknown outage", "Mixed stress")
GATE_LABELS = {
    "mean_pair_ul_improvement_at_least_10_percent": "Mean UL ≥10%",
    "bootstrap_lower_bound_above_zero": "Bootstrap lower >0",
    "positive_severity_weighted_improvement": "Severity-weighted >0",
    "unknown_mixed_regression_no_worse_than_minus_2_percent": "Combined severity-weighted unknown + mixed ≥−2%",
    "worst_pair_better_than_minus_10_percent": "Worst pair >−10%",
    "no_dl_overload_drop_or_establishment_regression": "No DL/drop/session regression",
    "no_solver_timeout_or_error": "No timeout/error",
    "unexpected_fallback_fraction_within_1_percent": "Unexpected fallback ≤1%",
    "skipped_decision_fraction_within_95_percent": "Skipped decisions ≤95%",
    "normalized_churn_within_0_05_l1_per_group_decision": "Churn ≤0.05",
    "measured_empirical_survival_robustness": "Measured survival robust",
}


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


def _title(fig: Any, heading: str, subtitle: str, *, footer: str | None = None) -> None:
    fig.suptitle(heading, x=0.055, y=0.975, ha="left", fontsize=20,
                 fontweight="bold", color=NAVY)
    fig.text(0.055, 0.925, subtitle, color=MUTED, fontsize=10.5)
    fig.text(0.945, 0.025,
             footer or "Development seeds 46101–46112 · validation/release untouched",
             ha="right", color=MUTED, fontsize=8.5)


def _save(fig: Any, figures: Path, stem: str) -> list[Path]:
    figures.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for suffix in ("png", "svg"):
        target = figures / f"{stem}.{suffix}"
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        fig.savefig(temporary, format=suffix, dpi=240 if suffix == "png" else None,
                    bbox_inches="tight", facecolor="white")
        os.replace(temporary, target)
        outputs.append(target)
    plt.close(fig)
    return outputs


def _load_inputs(root: Path) -> dict[str, Any]:
    paths = {
        "forecast_addendum": root / "output/control-science/v1/phase2.1-addendum-v1/phase2-audit-addendum-v1.json",
        "survival_calibration": root / "output/control-science/v1/survival-phase3-v1/calibration-v1.json",
        "survival_guardrail": root / "output/control-science/v1/survival-phase3-v1/survival-guardrail-v2.json",
        "day5_decision": root / "output/control-science/v1/phase3-day5-v1/development-decision-v1.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing authoritative Phase-3 inputs: {missing}")
    payload = {name: _read(path) for name, path in paths.items()}
    forecast = payload["forecast_addendum"]
    guardrail = payload["survival_guardrail"]
    decision = payload["day5_decision"]
    if forecast.get("sealed_phase2_modified") is not False or forecast.get("protected_test_seed_consumed") is not False:
        raise ValueError("forecast addendum does not preserve the sealed/protected state")
    if not guardrail.get("passed") or not all(guardrail.get("criteria", {}).values()):
        raise ValueError("survival guardrail is not a complete pass")
    if decision.get("decision") != "stop_and_retain_static" or decision.get("selected_candidate") is not None:
        raise ValueError("Day-5 decision is not the sealed no-advance result")
    if decision.get("validation_seeds_consumed") or decision.get("release_seeds_consumed"):
        raise ValueError("protected MPC seeds have been consumed")
    evaluations: dict[str, dict[str, Any]] = {}
    evaluation_paths: dict[str, Path] = {}
    for candidate in decision["candidates"]:
        path = Path(candidate["evaluation_path"])
        if _sha256(path) != candidate["evaluation_sha256"]:
            raise ValueError(f"evaluation hash mismatch: {path}")
        evaluation = _read(path)
        if {int(row["seed"]) for row in evaluation["pairs"]} != set(range(46101, 46113)):
            raise ValueError(f"candidate seed coverage is invalid: {candidate['candidate']}")
        evaluations[candidate["candidate"]] = evaluation
        evaluation_paths[candidate["candidate"]] = path
    survival_comparisons: dict[str, dict[str, Any]] = {}
    survival_paths: dict[str, Path] = {}
    comparison_root = root / "output/control-science/v1/survival-phase3-v1/mpc-comparisons"
    for path in sorted(comparison_root.glob("*/evaluation.json")):
        survival_comparisons[path.parent.name] = _read(path)
        survival_paths[path.parent.name] = path
    bundle_root = root / "output/control-science/v1/survival-phase3-v1"
    bundle_paths = {
        name: bundle_root / filename
        for name, filename in {
            "oracle": "oracle.json", "empirical-n100": "empirical-n100.json",
            "empirical-n1000": "empirical-n1000.json",
            "empirical-n10000": "empirical-n10000.json", "uniform": "uniform.json",
            "static-fallback": "static-fallback.json",
        }.items()
    }
    bundles = {name: _read(path) for name, path in bundle_paths.items()}
    payload.update({
        "paths": paths,
        "evaluations": evaluations,
        "evaluation_paths": evaluation_paths,
        "survival_comparisons": survival_comparisons,
        "survival_paths": survival_paths,
        "survival_bundle_paths": bundle_paths,
        "survival_bundles": bundles,
    })
    return payload


def _forecast_targets(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    candidates = data["forecast_addendum"]["candidates"]
    names = [row["model_family"] for row in candidates]
    x = np.arange(len(names))
    series = (
        ("Sessions", "new_session_count", BLUE),
        ("UL Mbps", "new_ul_mbps", CYAN),
        ("DL Mbps", "new_dl_mbps", PURPLE),
        ("Macro target", None, GOLD),
    )
    fig, ax = plt.subplots(figsize=(13.5, 7.2))
    _title(fig, "Forecast gains are real—but below the promotion bar",
           "Target-separated WAPE prevents unlike units from being hidden inside the pooled headline",
           footer="Seed 46003 generated + sealed · never evaluated or selected")
    width = 0.18
    values_by_series: dict[str, list[float]] = {}
    for index, (label, target, color) in enumerate(series):
        values = [
            100 * (row["target_separated"][target]["relative_wape_improvement"]
                   if target else row["macro_average_target_relative_improvement"])
            for row in candidates
        ]
        values_by_series[label] = values
        ax.bar(x + (index - 1.5) * width, values, width, label=label, color=color)
    pooled = [100 * row["pooled_cross_target_relative_improvement"] for row in candidates]
    ax.scatter(x, pooled, marker="D", s=54, color=NAVY, label="Pooled cross-target", zorder=4)
    ax.axhline(15, color=CORAL, lw=2, ls="--")
    ax.text(4.45, 15.5, "15% promotion threshold", ha="right", color=CORAL, fontweight="bold")
    ax.axhline(0, color=NAVY, lw=0.9)
    ax.set_xticks(x, [FORECAST_LABELS[name] for name in names], rotation=12, ha="right")
    ax.set_ylabel("WAPE improvement vs best simple baseline (%)")
    ax.set_ylim(-25, 20)
    ax.grid(axis="y")
    ax.legend(ncol=3, loc="lower left")
    fig.subplots_adjust(top=0.84, bottom=0.18, left=0.09, right=0.97)
    return _save(fig, figures, "01_forecast_target_separated_wape"), {
        "candidates": names, "series_percent": values_by_series,
        "pooled_percent": pooled, "promotion_threshold_percent": 15,
    }


def _forecast_worst_slice(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    row = next(item for item in data["forecast_addendum"]["candidates"]
               if item["model_family"] == "lightgbm-quantile")
    worst = row["worst_aggregate_slice"]
    regression = 100 * worst["relative_regression"]
    interval = [100 * item for item in worst["relative_regression_cluster_bootstrap_95_interval"]]
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.7), gridspec_kw={"width_ratios": [0.9, 1.35]})
    _title(fig, "The worst LightGBM slice is visible—and statistically fragile",
           "Detected-surge DL load at 30 minutes: n=9 observations across 8 groups",
           footer="Seed 46003 generated + sealed · never evaluated or selected")
    axes[0].bar([0, 1], [100 * worst["baseline_wape"], 100 * worst["wape"]],
                color=[MUTED, PURPLE], width=0.62)
    axes[0].set_xticks([0, 1], ["Moving-average\nbaseline", "LightGBM"])
    axes[0].set_ylabel("WAPE (%)")
    axes[0].set_ylim(0, 70)
    axes[0].grid(axis="y")
    for index, value in enumerate([100 * worst["baseline_wape"], 100 * worst["wape"]]):
        axes[0].text(index, value + 1.5, f"{value:.1f}%", ha="center", fontweight="bold", color=NAVY)
    axes[1].axvline(0, color=NAVY, lw=1)
    axes[1].axvline(5, color=CORAL, lw=1.5, ls="--")
    axes[1].errorbar(regression, 0,
                     xerr=[[regression - interval[0]], [interval[1] - regression]],
                     fmt="o", markersize=11, color=CORAL, ecolor=CORAL, capsize=8, lw=3)
    axes[1].set_yticks([0], ["LightGBM worst slice"])
    axes[1].set_xlabel("Regression vs baseline (%)  → worse")
    axes[1].set_xlim(-12, 55)
    axes[1].grid(axis="x")
    axes[1].text(regression, 0.16, f"point estimate {regression:.1f}%", ha="center",
                 color=CORAL, fontweight="bold")
    axes[1].text(np.mean(interval), -0.22, f"95% group-cluster CI [{interval[0]:.1f}%, {interval[1]:.1f}%]",
                 ha="center", color=MUTED)
    fig.text(0.055, 0.07,
             "Interpretation: keep the regression visible, but do not present nine observations as a stable population estimate.",
             color=NAVY, fontsize=10.5, fontweight="bold")
    fig.subplots_adjust(top=0.82, bottom=0.20, left=0.09, right=0.97, wspace=0.32)
    return _save(fig, figures, "02_forecast_worst_slice_uncertainty"), {
        "model": "lightgbm-quantile", "regression_percent": regression,
        "bootstrap_95_interval_percent": interval,
        "scored_observations": worst["scored_observations"],
        "contributing_groups": worst["contributing_groups"],
    }


def _survival_calibration(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    calibration = data["survival_calibration"]["calibration"]
    sizes = np.array([100, 1_000, 10_000])
    empirical = [calibration[f"empirical-n{size}"] for size in sizes]
    exposure = 100 * np.array([row["load_exposure_relative_absolute_error"] for row in empirical])
    mean_error = 100 * np.array([row["mean_group_absolute_calibration_error"] for row in empirical])
    maximum = 100 * np.array([row["max_group_horizon_absolute_calibration_error"] for row in empirical])
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.8))
    _title(fig, "Kaplan–Meier mechanics converge on synthetic lifecycle telemetry",
           "Synthetic censored records test estimation mechanics—not real-world distribution independence")
    axes[0].plot(sizes, exposure, "o-", lw=3, ms=8, color=BLUE, label="Load-exposure error")
    axes[0].plot(sizes, mean_error, "o-", lw=2.5, ms=7, color=CYAN, label="Mean calibration error")
    axes[0].axhline(100 * calibration["static-fallback"]["load_exposure_relative_absolute_error"],
                    color=GOLD, ls="--", label="Static prior exposure error")
    axes[0].axhline(100 * calibration["uniform"]["load_exposure_relative_absolute_error"],
                    color=CORAL, ls=":", label="Uniform exposure error")
    axes[0].set_xscale("log")
    axes[0].set_xticks(sizes, ["100", "1,000", "10,000"])
    axes[0].set_xlabel("Lifecycle samples per group")
    axes[0].set_ylabel("Relative error (%)")
    axes[0].set_ylim(0, 45)
    axes[0].grid(True)
    axes[0].legend(loc="upper right", fontsize=9)
    axes[1].plot(sizes, maximum, "o-", lw=3, ms=8, color=PURPLE)
    axes[1].axhline(5, color=CORAL, ls="--", lw=2)
    axes[1].fill_between(sizes, 0, 5, color=LIGHT_GREEN, alpha=0.65)
    axes[1].set_xscale("log")
    axes[1].set_xticks(sizes, ["100", "1,000", "10,000"])
    axes[1].set_xlabel("Lifecycle samples per group")
    axes[1].set_ylabel("Worst group/horizon absolute error (%)")
    axes[1].set_ylim(0, 21)
    axes[1].grid(True)
    axes[1].text(9_000, 5.5, "5% calibration target", ha="right", color=CORAL, fontweight="bold")
    axes[1].annotate(f"{maximum[-1]:.2f}%", (sizes[-1], maximum[-1]), xytext=(-50, 22),
                     textcoords="offset points", arrowprops={"arrowstyle": "->", "color": PURPLE},
                     color=PURPLE, fontweight="bold")
    fig.subplots_adjust(top=0.82, bottom=0.15, left=0.08, right=0.97, wspace=0.27)
    return _save(fig, figures, "03_survival_calibration_convergence"), {
        "sample_sizes": sizes.tolist(), "exposure_error_percent": exposure.tolist(),
        "mean_calibration_error_percent": mean_error.tolist(),
        "max_calibration_error_percent": maximum.tolist(),
    }


def _survival_curves(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    order = ("oracle", "empirical-n100", "empirical-n1000", "empirical-n10000", "static-fallback", "uniform")
    labels = {
        "oracle": "Oracle upper bound", "empirical-n100": "Empirical n=100",
        "empirical-n1000": "Empirical n=1,000", "empirical-n10000": "Empirical n=10,000",
        "static-fallback": "Static prior", "uniform": "Uniform naive",
    }
    colors = {"oracle": NAVY, "empirical-n100": GOLD, "empirical-n1000": CYAN,
              "empirical-n10000": BLUE, "static-fallback": PURPLE, "uniform": CORAL}
    styles = {"oracle": "-", "empirical-n100": "--", "empirical-n1000": "--",
              "empirical-n10000": "-", "static-fallback": ":", "uniform": ":"}
    means: dict[str, list[float]] = {}
    fig, ax = plt.subplots(figsize=(13.5, 7.0))
    _title(fig, "What the MPC actually consumes: expected cohort survival",
           "Average across 96 traffic groups; empirical n=10,000 closely tracks the non-deployable oracle")
    for name in order:
        groups = data["survival_bundles"][name]["groups"].values()
        values = np.mean(np.asarray([group["probabilities"] for group in groups], dtype=float), axis=0)
        means[name] = values.tolist()
        width = 3.2 if name in {"oracle", "empirical-n10000"} else 2.0
        ax.plot(np.arange(len(values)), 100 * values, styles[name], lw=width,
                color=colors[name], label=labels[name])
    ax.set_xlabel("Decision-bucket lag")
    ax.set_ylabel("Expected cohort still active (%)")
    ax.set_xlim(0, 11)
    ax.set_ylim(0, 102)
    ax.grid(True)
    ax.legend(ncol=2, loc="lower left")
    fig.subplots_adjust(top=0.84, bottom=0.14, left=0.09, right=0.97)
    return _save(fig, figures, "04_survival_curves_consumed_by_mpc"), {"mean_curves": means}


def _survival_mpc_forest(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    order = ("oracle", "empirical-n100", "empirical-n1000", "empirical-n10000", "uniform", "stale-empirical")
    labels = {
        "oracle": "Oracle (upper bound)", "empirical-n100": "Empirical n=100",
        "empirical-n1000": "Empirical n=1,000", "empirical-n10000": "Empirical n=10,000",
        "uniform": "Uniform naive", "stale-empirical": "Stale empirical → Static",
    }
    means = np.array([100 * data["survival_comparisons"][name]["mean_pair_ul_overload_area_relative_reduction"] for name in order])
    intervals = np.array([[100 * value for value in data["survival_comparisons"][name]["mean_pair_ul_reduction_bootstrap_95_interval"]] for name in order])
    y = np.arange(len(order))[::-1]
    fig, ax = plt.subplots(figsize=(13.5, 7.0))
    _title(fig, "Empirical survival is relatively equivalent—not operationally robust",
           "Oracle timeout 68.75% · empirical n=10,000 timeout 69.50% · same 12 development seeds")
    colors = [NAVY, GOLD, CYAN, BLUE, CORAL, MUTED]
    for index, name in enumerate(order):
        ax.errorbar(means[index], y[index],
                    xerr=[[means[index] - intervals[index, 0]], [intervals[index, 1] - means[index]]],
                    fmt="o", ms=9, capsize=5, lw=2.2, color=colors[index])
    ax.axvline(0, color=NAVY, lw=1)
    ax.axvline(10, color=CORAL, ls="--", lw=2)
    ax.set_yticks(y, [labels[name] for name in order])
    ax.set_xlabel("Mean-pair UL overload-area improvement (%)")
    ax.set_xlim(-6, 31)
    ax.grid(axis="x")
    ax.text(10.4, 5.25, "10% mean gate", color=CORAL, fontweight="bold")
    ax.text(-5.5, -0.65, "Stale telemetry returns exactly to Static (0%).", color=MUTED)
    fig.subplots_adjust(top=0.83, bottom=0.15, left=0.22, right=0.97)
    return _save(fig, figures, "05_survival_mpc_robustness_forest"), {
        "order": list(order), "mean_percent": means.tolist(), "interval_percent": intervals.tolist(),
    }


def _survival_paired(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    oracle = {(row["scenario_kind"], row["seed"]): row for row in data["survival_comparisons"]["oracle"]["pairs"]}
    empirical = {(row["scenario_kind"], row["seed"]): row for row in data["survival_comparisons"]["empirical-n10000"]["pairs"]}
    keys = sorted(oracle)
    x = np.array([100 * oracle[key]["relative_reduction"]["overload_area_seconds"]["ul"] for key in keys])
    y = np.array([100 * empirical[key]["relative_reduction"]["overload_area_seconds"]["ul"] for key in keys])
    color_map = {"surge": BLUE, "scheduled_fault": GREEN, "unannounced_outage": GOLD, "mixed_stress": PURPLE}
    fig, ax = plt.subplots(figsize=(9.4, 8.0))
    _title(fig, "Imperfect survival follows oracle behavior pair by pair",
           "Empirical n=10,000 versus oracle upper bound; 12 identical development pairs")
    limits = [min(-16, x.min() - 3, y.min() - 3), max(62, x.max() + 3, y.max() + 3)]
    ax.plot(limits, limits, color=MUTED, ls="--", lw=1.5)
    for scenario in SCENARIOS:
        selected = [index for index, key in enumerate(keys) if key[0] == scenario]
        ax.scatter(x[selected], y[selected], s=85, color=color_map[scenario],
                   label=SCENARIO_LABELS[SCENARIOS.index(scenario)], edgecolor="white", linewidth=0.8)
    ax.set_xlim(limits)
    ax.set_ylim(limits)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Oracle UL improvement (%)")
    ax.set_ylabel("Empirical n=10,000 UL improvement (%)")
    ax.grid(True)
    ax.legend(loc="upper left")
    ax.text(limits[1] - 1, limits[0] + 2, "below line: empirical underperforms oracle",
            ha="right", color=MUTED, fontsize=9)
    fig.subplots_adjust(top=0.84, bottom=0.13, left=0.13, right=0.97)
    return _save(fig, figures, "06_survival_empirical_vs_oracle_pairs"), {
        "pairs": [{"scenario": key[0], "seed": key[1], "oracle_percent": float(x[index]),
                   "empirical_percent": float(y[index])} for index, key in enumerate(keys)]
    }


def _candidate_forest(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    rows = data["day5_decision"]["candidates"]
    names = [row["candidate"] for row in rows]
    means = np.array([100 * row["mean_pair_ul_improvement"] for row in rows])
    intervals = np.array([[100 * value for value in row["bootstrap_95_interval"]] for row in rows])
    y = np.arange(len(rows))[::-1]
    fig, ax = plt.subplots(figsize=(13.5, 9.0))
    _title(fig, "All 13 guarded MPC candidates fail the frozen Day-5 bar",
           "Forest plot shows paired mean UL improvement and bootstrap 95% interval")
    for index, name in enumerate(names):
        color = BLUE if index < 7 else CYAN
        ax.errorbar(means[index], y[index],
                    xerr=[[means[index] - intervals[index, 0]], [intervals[index, 1] - means[index]]],
                    fmt="o", ms=8, capsize=4, lw=2, color=color)
    ax.axvline(0, color=NAVY, lw=1)
    ax.axvline(10, color=CORAL, ls="--", lw=2)
    ax.set_yticks(y, [CANDIDATE_LABELS[name] for name in names])
    ax.set_xlabel("Mean-pair UL overload-area improvement (%)")
    ax.set_xlim(-6, 31)
    ax.grid(axis="x")
    ax.text(10.4, 12.25, "10% mean gate", color=CORAL, fontweight="bold")
    ax.text(23.5, 1.1, "Blue: planned guarded candidates\nCyan: solver-feasibility tuning",
            color=MUTED, fontsize=9)
    fig.subplots_adjust(top=0.86, bottom=0.11, left=0.25, right=0.97)
    return _save(fig, figures, "07_all_mpc_candidates_forest"), {
        "candidates": names, "mean_percent": means.tolist(), "interval_percent": intervals.tolist(),
    }


def _scenario_heatmap(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    names = [row["candidate"] for row in data["day5_decision"]["candidates"]]
    matrix = np.array([
        [100 * data["evaluations"][name]["by_scenario"][scenario]["aggregate_ul_overload_area_relative_reduction"]
         for scenario in SCENARIOS]
        for name in names
    ])
    fig, ax = plt.subplots(figsize=(11.5, 9.2))
    _title(fig, "Scenario view: broad MPC gains are concentrated and unstable",
           "Aggregate UL overload-area improvement; three paired seeds per scenario")
    norm = TwoSlopeNorm(vmin=min(-25, float(matrix.min())), vcenter=0, vmax=max(60, float(matrix.max())))
    image = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", norm=norm)
    ax.set_xticks(np.arange(4), SCENARIO_LABELS)
    ax.set_yticks(np.arange(len(names)), [CANDIDATE_LABELS[name] for name in names])
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            value = matrix[i, j]
            ax.text(j, i, f"{value:.1f}%", ha="center", va="center",
                    color="white" if abs(value) > 12 else NAVY, fontsize=8.5,
                    fontweight="bold" if abs(value) > 5 else "normal")
    colorbar = fig.colorbar(image, ax=ax, fraction=0.035, pad=0.025)
    colorbar.set_label("UL improvement (%)")
    fig.subplots_adjust(top=0.86, bottom=0.09, left=0.26, right=0.92)
    return _save(fig, figures, "08_mpc_scenario_improvement_heatmap"), {
        "candidates": names, "scenarios": list(SCENARIOS), "improvement_percent": matrix.tolist(),
    }


def _solver_status(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    names = [row["candidate"] for row in data["day5_decision"]["candidates"]]
    status_rows = []
    for name in names:
        totals: dict[str, int] = {}
        for pair in data["evaluations"][name]["pairs"]:
            for status, count in pair["solver_statuses"].items():
                totals[status] = totals.get(status, 0) + int(count)
        decisions = sum(totals.values())
        status_rows.append({
            "optimal": totals.get("optimal", 0) / decisions,
            "skipped": totals.get("skipped", 0) / decisions,
            "failed": sum(totals.get(item, 0) for item in ("timeout", "error", "infeasible")) / decisions,
            "counts": totals,
        })
    y = np.arange(len(names))[::-1]
    fig, ax = plt.subplots(figsize=(13.5, 9.0))
    _title(fig, "Solver-feasibility tuning removed timeouts—not the performance gap",
           "Share of controller decisions by solver status; failed = timeout, error, or infeasible")
    left = np.zeros(len(names))
    for field, label, color in (("optimal", "Optimal", GREEN), ("skipped", "Guarded skip → Static", MUTED),
                                ("failed", "Failed solve → Static", CORAL)):
        values = np.array([100 * row[field] for row in status_rows])
        ax.barh(y, values, left=left, color=color, height=0.68, label=label)
        left += values
    ax.set_yticks(y, [CANDIDATE_LABELS[name] for name in names])
    ax.set_xlabel("Controller decisions (%)")
    ax.set_xlim(0, 100)
    ax.grid(axis="x")
    ax.legend(ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.12))
    ax.axhline(5.5, color=GRID, lw=2)
    ax.text(99, 5.7, "solver-feasibility sweep", ha="right", va="bottom", color=MUTED, fontsize=9)
    fig.subplots_adjust(top=0.86, bottom=0.16, left=0.25, right=0.97)
    return _save(fig, figures, "09_solver_status_and_static_fallback"), {
        "candidates": names, "status_fractions": status_rows,
    }


def _gate_scorecard(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    names = [row["candidate"] for row in data["day5_decision"]["candidates"]]
    gate_keys = list(GATE_LABELS)
    matrix = np.array([[bool(data["evaluations"][name]["development_gates"][gate]) for gate in gate_keys]
                       for name in names], dtype=int)
    fig, ax = plt.subplots(figsize=(15.5, 9.5))
    _title(fig, "Fail closed means every gate must pass",
           "Green = pass, red = fail; no candidate clears all 11 frozen development gates")
    ax.imshow(matrix, aspect="auto", cmap=ListedColormap([LIGHT_CORAL, LIGHT_GREEN]), vmin=0, vmax=1)
    ax.set_xticks(np.arange(len(gate_keys)), [GATE_LABELS[key] for key in gate_keys], rotation=42, ha="right")
    ax.set_yticks(np.arange(len(names)), [CANDIDATE_LABELS[name] for name in names])
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax.text(j, i, "✓" if matrix[i, j] else "×", ha="center", va="center",
                    color=GREEN if matrix[i, j] else CORAL, fontsize=12, fontweight="bold")
    ax.set_xticks(np.arange(-0.5, len(gate_keys), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(names), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.5)
    ax.tick_params(which="minor", bottom=False, left=False)
    fig.subplots_adjust(top=0.86, bottom=0.28, left=0.22, right=0.98)
    return _save(fig, figures, "10_day5_gate_scorecard"), {
        "candidates": names, "gates": gate_keys, "pass_matrix": matrix.tolist(),
    }


def _box(ax: Any, xy: tuple[float, float], width: float, height: float, text: str,
         *, face: str, edge: str = NAVY, fontsize: float = 10.5) -> None:
    patch = FancyBboxPatch(xy, width, height, boxstyle="round,pad=0.015,rounding_size=0.02",
                           transform=ax.transAxes, facecolor=face, edgecolor=edge, linewidth=1.7)
    ax.add_patch(patch)
    ax.text(xy[0] + width / 2, xy[1] + height / 2, text, transform=ax.transAxes,
            ha="center", va="center", color=NAVY, fontsize=fontsize, fontweight="bold")


def _arrow(ax: Any, start: tuple[float, float], end: tuple[float, float], *, color: str = MUTED) -> None:
    ax.add_patch(FancyArrowPatch(start, end, transform=ax.transAxes, arrowstyle="-|>",
                                mutation_scale=14, linewidth=1.7, color=color))


def _guarded_architecture(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    fig, ax = plt.subplots(figsize=(14.5, 8.2))
    _title(fig, "Production architecture: MPC is an optional, guarded branch",
           "Unknown outages, uncertain telemetry, stale survival, or failed certification return immediately to Static")
    ax.axis("off")
    _box(ax, (0.03, 0.56), 0.16, 0.16, "Telemetry +\nevent calendar", face=PALE)
    _box(ax, (0.25, 0.56), 0.18, 0.16, "Causal input gates\nknown event?\ntelemetry certain?", face="#E7F1FF", edge=BLUE)
    _box(ax, (0.49, 0.56), 0.17, 0.16, "Empirical KM\nsurvival bundle\nmeasured pass", face="#E8F8F5", edge=CYAN)
    _box(ax, (0.72, 0.56), 0.12, 0.16, "Cohort MPC\nplanner", face="#F0EBFF", edge=PURPLE)
    _box(ax, (0.88, 0.56), 0.10, 0.16, "Same-state\ncertificate", face="#FFF4D6", edge=GOLD, fontsize=9.5)
    _box(ax, (0.72, 0.20), 0.26, 0.16, "Apply certified policy\nwith churn budget", face=LIGHT_GREEN, edge=GREEN)
    _box(ax, (0.25, 0.20), 0.30, 0.16, "STATIC controller\nproduction default", face=LIGHT_CORAL, edge=CORAL, fontsize=12)
    for start, end in (((0.19, 0.64), (0.25, 0.64)), ((0.43, 0.64), (0.49, 0.64)),
                       ((0.66, 0.64), (0.72, 0.64)), ((0.84, 0.64), (0.88, 0.64)),
                       ((0.93, 0.56), (0.88, 0.36))):
        _arrow(ax, start, end)
    _arrow(ax, (0.34, 0.56), (0.34, 0.36), color=CORAL)
    _arrow(ax, (0.57, 0.56), (0.48, 0.36), color=CORAL)
    _arrow(ax, (0.78, 0.56), (0.52, 0.36), color=CORAL)
    ax.text(0.19, 0.43, "unknown outage / stale calendar", transform=ax.transAxes, color=CORAL, fontsize=9)
    ax.text(0.48, 0.44, "stale / insufficient survival", transform=ax.transAxes, color=CORAL, fontsize=9)
    ax.text(0.69, 0.43, "timeout / error / unsafe", transform=ax.transAxes, color=CORAL, fontsize=9)
    ax.text(0.74, 0.39, "all checks pass", transform=ax.transAxes, color=GREEN, fontsize=9, fontweight="bold")
    ax.text(0.03, 0.08,
            "Measured result: the guardrail plumbing works; no tested MPC branch earned authority to replace Static.",
            transform=ax.transAxes, color=NAVY, fontsize=11.5, fontweight="bold")
    fig.subplots_adjust(top=0.86, bottom=0.04, left=0.03, right=0.99)
    return _save(fig, figures, "11_guarded_mpc_production_architecture"), {
        "static_default": True,
        "fallback_triggers": ["unknown outage", "telemetry uncertainty", "stale survival",
                              "solver timeout/error", "failed same-state certificate"],
    }


def _campaign_scale(data: dict[str, Any], figures: Path) -> tuple[list[Path], dict[str, Any]]:
    stages = ["Survival\ncomparison", "Guarded\ncandidates", "Solver\nfeasibility"]
    pairs = [72, 84, 72]
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 7.0), gridspec_kw={"width_ratios": [1.1, 1]})
    _title(fig, "228 paired one-day runs support a disciplined stop decision",
           "The campaign used development seeds repeatedly and preserved validation/release for a genuine winner")
    bars = axes[0].bar(stages, pairs, color=[CYAN, BLUE, PURPLE], width=0.65)
    axes[0].set_ylabel("One-day paired simulations")
    axes[0].set_ylim(0, 100)
    axes[0].grid(axis="y")
    for bar, value in zip(bars, pairs):
        axes[0].text(bar.get_x() + bar.get_width() / 2, value + 3, str(value), ha="center",
                     fontweight="bold", color=NAVY, fontsize=13)
    axes[0].text(1, 92, "228 total", ha="center", color=NAVY, fontsize=15, fontweight="bold")
    axes[1].axis("off")
    _box(axes[1], (0.08, 0.70), 0.84, 0.15, "13 frozen MPC candidates", face="#E7F1FF", edge=BLUE, fontsize=13)
    _box(axes[1], (0.18, 0.43), 0.64, 0.15, "0 pass all 11 gates", face=LIGHT_CORAL, edge=CORAL, fontsize=13)
    _box(axes[1], (0.26, 0.16), 0.48, 0.15, "STATIC retained", face=LIGHT_GREEN, edge=GREEN, fontsize=14)
    _arrow(axes[1], (0.50, 0.70), (0.50, 0.58))
    _arrow(axes[1], (0.50, 0.43), (0.50, 0.31))
    axes[1].text(0.5, 0.04, "46201–46216 validation untouched\n46301–46330 release untouched",
                 transform=axes[1].transAxes, ha="center", color=NAVY, fontweight="bold")
    fig.subplots_adjust(top=0.83, bottom=0.14, left=0.08, right=0.97, wspace=0.25)
    return _save(fig, figures, "12_campaign_scale_and_release_discipline"), {
        "stage_pairs": dict(zip(stages, pairs)), "total_pairs": sum(pairs),
        "candidates": 13, "passing_candidates": 0,
        "validation_consumed": False, "release_consumed": False,
    }


def _build_pdf(output: Path, figures: list[Path]) -> None:
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    with PdfPages(temporary) as pdf:
        cover = plt.figure(figsize=(13.333, 7.5), facecolor=NAVY)
        cover.text(0.07, 0.78, "C-DOT control science", color="white", fontsize=28, fontweight="bold")
        cover.text(0.07, 0.64, "Phase 2.1 + Phase 3", color="#8FD3FF", fontsize=38, fontweight="bold")
        cover.text(0.07, 0.53, "Forecast audit · synthetic lifecycle survival · guarded MPC", color="white", fontsize=18)
        cover.text(0.07, 0.31, "Outcome", color=GOLD, fontsize=13, fontweight="bold")
        cover.text(0.07, 0.22, "No candidate passed all Day-5 gates. Static remains production default.",
                   color="white", fontsize=17, fontweight="bold")
        cover.text(0.07, 0.08, "228 paired one-day development runs · protected validation and release seeds untouched",
                   color="#B8C7D9", fontsize=11)
        plt.axis("off")
        pdf.savefig(cover, facecolor=NAVY)
        plt.close(cover)
        for path in figures:
            image = plt.imread(path)
            page = plt.figure(figsize=(13.333, 7.5), facecolor="white")
            ax = page.add_axes([0.01, 0.01, 0.98, 0.98])
            ax.imshow(image)
            ax.axis("off")
            pdf.savefig(page, facecolor="white", bbox_inches="tight", pad_inches=0.02)
            plt.close(page)
        final = plt.figure(figsize=(13.333, 7.5), facecolor="white")
        final.text(0.07, 0.83, "Decision", color=NAVY, fontsize=30, fontweight="bold")
        final.text(0.07, 0.66, "Retain Static", color=GREEN, fontsize=38, fontweight="bold")
        final.text(0.07, 0.52,
                   "Synthetic lifecycle telemetry validates Kaplan–Meier mechanics.\n"
                   "Neither survival path nor the tested MPC controllers are production-eligible.",
                   color=NAVY, fontsize=18, linespacing=1.5)
        final.text(0.07, 0.28,
                   "This is a positive experimental outcome: the release discipline prevented\n"
                   "mean gains from hiding tail risk, solver failures, churn, and regressions.",
                   color=MUTED, fontsize=14, linespacing=1.45)
        final.text(0.07, 0.08, "Do not consume validation or release seeds for these candidates.",
                   color=CORAL, fontsize=13, fontweight="bold")
        plt.axis("off")
        pdf.savefig(final, facecolor="white")
        plt.close(final)
    os.replace(temporary, output)


def _report() -> str:
    return """# C-DOT Phase 2.1/3 visual evidence package

This package visualizes every experiment completed through the Phase-3 Day-5
decision. It does not modify or rerun sealed evidence.

## Presentation sequence

1. Forecast target-separated WAPE: useful gains, none at the 15% bar.
2. Worst forecast slice: 14.70% regression, but only n=9 with a wide interval.
3. Kaplan–Meier mechanics on synthetically generated censored lifecycle telemetry.
4. Survival curves consumed by MPC, including oracle and deployable fallbacks.
5. Imperfect-versus-oracle relative survival equivalence under solver pressure.
6. Pair-level empirical-versus-oracle correspondence.
7. Forest plot of all 13 MPC development candidates.
8. Scenario-level MPC controllability heatmap.
9. Solver status and immediate-Static fallback behavior.
10. Complete 11-gate Day-5 scorecard.
11. Guarded production architecture and fallback triggers.
12. Cluster campaign scale and protected-seed discipline.

The consolidated PDF contains a cover, all twelve plots, and a decision page.
PNG files are optimized for slides; SVG files remain editable and lossless.

## Authoritative conclusion

The synthetic censored-lifecycle experiment validates Kaplan–Meier mechanics,
but does not establish real-world distribution-independent calibration. Its
paired MPC check is relative equivalence, not operational robustness: oracle
timed out on 68.75% of decisions and empirical n=10,000 on 69.50%. No tested
MPC candidate passes every frozen development gate, so Static remains the
production controller. Forecast seed 46003 was generated and sealed but never
used for model evaluation or selection; validation seeds 46201–46216 and
release seeds 46301–46330 remain untouched.
"""


def _talk_track() -> str:
    return """# C-DOT Phase 2.1/3 talk track

## 01 — Forecast targets

The pooled headline has been corrected: sessions and Mbps are shown separately.
LightGBM improves DL by 12.62%, sessions by 11.09%, and UL by 9.99%. These are
useful results, but none reaches the frozen 15% promotion requirement.

## 02 — Worst forecast slice

The worst LightGBM slice regresses 14.70%, but it contains nine scored
observations across eight groups. The wide confidence interval is the key
message: visible risk, not a stable population estimate.

## 03–04 — Synthetic lifecycle survival validation

Kaplan–Meier is validated on synthetically generated censored lifecycle
telemetry. At 10,000 samples per group, load-exposure error is 0.42% and worst
group/horizon error is 1.93%. This validates censoring and estimation mechanics;
it is not real-world or distribution-independent telemetry evidence. Oracle is
shown only as a non-deployable upper bound.

## 05–06 — Relative survival equivalence

Empirical survival tracks oracle outcomes within the measured relative
guardrail: oracle timed out on 1,188 of 1,728 decisions (68.75%), while the
empirical n=10,000 path timed out on 1,201 of 1,728 (69.50%). This is relative
survival equivalence under solver pressure, not an operational robustness pass.
Stale survival returns exactly to Static. A naive uniform curve can look good
in an MPC outcome while being badly miscalibrated, so outcome alone is
insufficient.

## 07–08 — MPC effectiveness

The broad candidates show mean gains, but their intervals cross zero and tail
risk remains. Scenario gains are concentrated rather than general. The narrow
scheduled variants are safe but essentially neutral.

## 09 — Solver behavior

The first scheduled sweep timed out. Shorter horizons and larger budgets
eliminated timeouts, proving the branch was exercised, but did not create a
material benefit. The stop decision is therefore not merely a timeout artifact.

## 10 — Frozen gates

Promotion is conjunctive: every gate must pass. Mean improvement cannot excuse
negative confidence bounds, worst-pair loss, regressions, churn, or solver
failure. No row is all green. Static therefore remains deployed even where a
candidate's average improvement appears attractive.

## 11 — Architecture

Static is the production default. MPC is an optional scheduled-event branch.
Unknown outage, uncertain telemetry, stale survival, failed solve, or failed
same-state certification returns immediately to Static.

## 12 — Experimental discipline

The Phase-3 campaign produced 228 paired one-day runs. Thirteen candidates were
tested and zero advanced. Forecast test seed 46003 was generated and sealed,
but untouched by model evaluation or selection. Validation seeds 46201–46216
and release seeds 46301–46330 remain unused for a future genuinely frozen
winner.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path,
                        default=PROJECT_ROOT / "output/control-science/v1/phase3-cdot-showcase-v1")
    args = parser.parse_args()
    if args.output_root.exists():
        raise FileExistsError(f"refusing to overwrite presentation package: {args.output_root}")
    _style()
    data = _load_inputs(PROJECT_ROOT)
    figures_root = args.output_root / "figures"
    builders = (
        _forecast_targets, _forecast_worst_slice, _survival_calibration,
        _survival_curves, _survival_mpc_forest, _survival_paired,
        _candidate_forest, _scenario_heatmap, _solver_status,
        _gate_scorecard, _guarded_architecture, _campaign_scale,
    )
    figure_outputs: list[Path] = []
    figure_data: dict[str, Any] = {}
    for builder in builders:
        outputs, payload = builder(data, figures_root)
        figure_outputs.extend(outputs)
        figure_data[outputs[0].stem] = payload
    args.output_root.mkdir(parents=True, exist_ok=True)
    _atomic_json(args.output_root / "figure-data.json", figure_data)
    _atomic_text(args.output_root / "REPORT.md", _report())
    _atomic_text(args.output_root / "TALK_TRACK.md", _talk_track())
    pngs = sorted(path for path in figure_outputs if path.suffix == ".png")
    pdf = args.output_root / "cdot_phase3_control_science_showcase.pdf"
    _build_pdf(pdf, pngs)
    source_paths = (
        list(data["paths"].values()) + list(data["evaluation_paths"].values())
        + list(data["survival_paths"].values()) + list(data["survival_bundle_paths"].values())
        + [Path(__file__).resolve()]
    )
    outputs = sorted(path for path in args.output_root.rglob("*") if path.is_file()
                     and path.name != "artifact-manifest.json")
    manifest = {
        "schema_version": "phase3-cdot-showcase/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_artifacts": {str(path.relative_to(PROJECT_ROOT)): _sha256(path) for path in sorted(set(source_paths))},
        "outputs": {str(path.relative_to(args.output_root)): _sha256(path) for path in outputs},
        "figures": len(pngs),
        "protected_seed_state": {
            "forecast_46003_consumed": False,
            "forecast_46003_status": "generated_and_sealed_but_never_evaluated_or_selected",
            "validation_46201_46216_consumed": False,
            "release_46301_46330_consumed": False,
        },
        "decision": "stop_and_retain_static",
    }
    _atomic_json(args.output_root / "artifact-manifest.json", manifest)
    print(json.dumps({
        "output": str(args.output_root.resolve()), "figures": len(pngs),
        "pdf": str(pdf.resolve()), "artifacts": len(outputs) + 1,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
