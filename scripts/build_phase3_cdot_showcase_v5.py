#!/usr/bin/env python3
"""Build the immutable C-DOT Phase 2.1 through Phase 3.2 showcase v5."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_phase3_cdot_showcase as base
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import ListedColormap, TwoSlopeNorm
from matplotlib.patches import FancyBboxPatch, Rectangle


ROOT = Path(__file__).resolve().parent.parent
plt = base.plt
np = base.np
NAVY, BLUE, CYAN = base.NAVY, base.BLUE, base.CYAN
GOLD, CORAL, GREEN = base.GOLD, base.CORAL, base.GREEN
PURPLE, MUTED, GRID = base.PURPLE, base.MUTED, base.GRID
PALE, LIGHT_GREEN, LIGHT_CORAL = base.PALE, base.LIGHT_GREEN, base.LIGHT_CORAL

CAMPAIGNS = (
    ("Phase 3.1 v1", "phase3.1-development-v1", BLUE),
    ("Phase 3.1 v2", "phase3.1-development-v2", PURPLE),
    ("Phase 3.2 v1", "phase3.2-development-v1", CYAN),
)

SHORT_LABELS = {
    "predrain-balanced": "P3.1v1 · pre-drain full",
    "predrain-early-diverse": "P3.1v1 · pre-drain early",
    "mpc-h3-configured-survival-diagnostic": "P3.1v1 · MPC cfg (not exercised)",
    "mpc-h3-lifecycle-lognormal": "P3.1v1 · MPC logn (not exercised)",
    "mpc-h6-lifecycle-heavy-tail": "P3.1v1 · MPC tail (not exercised)",
    "predrain-balanced-blend-050": "P3.1v2 · pre-drain 50%",
    "predrain-balanced-blend-025": "P3.1v2 · pre-drain 25%",
    "mpc-h3-ma6-configured-diagnostic": "P3.1v2 · MPC configured",
    "mpc-h3-ma6-lifecycle-lognormal": "P3.1v2 · MPC lognormal",
    "mpc-h3-ma6-lifecycle-heavy-tail": "P3.1v2 · MPC heavy-tail",
    "predrain-fixed-035": "P3.2 · fixed 35%",
    "predrain-fixed-040": "P3.2 · fixed 40%",
    "predrain-fixed-045": "P3.2 · fixed 45%",
    "predrain-adaptive-050-075": "P3.2 · adaptive 50–75",
    "predrain-adaptive-060-085": "P3.2 · adaptive 60–85",
}

GATE_LABELS = {
    **base.GATE_LABELS,
    "end_to_end_decision_latency_within_candidate_deadline": "End-to-end deadline",
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_new_inputs() -> dict[str, Any]:
    control = ROOT / "output/control-science/v1"
    survival_path = control / "phase3.1-survival-v1/CAMPAIGN.json"
    survival = _read(survival_path)
    rows: list[dict[str, Any]] = []
    source_paths: list[Path] = [survival_path]
    expected_seeds = {
        "Phase 3.1 v1": set(range(46401, 46425)),
        "Phase 3.1 v2": set(range(46425, 46449)),
        "Phase 3.2 v1": set(range(46449, 46473)),
    }
    for campaign, directory, color in CAMPAIGNS:
        root = control / directory
        decision_path = root / "DEVELOPMENT_DECISION.json"
        decision = _read(decision_path)
        source_paths.append(decision_path)
        if decision["decision"] != "retain_static" or decision["eligible_candidates"]:
            raise ValueError(f"unexpected development decision: {decision_path}")
        if set(decision["fresh_development_seeds"]) != expected_seeds[campaign]:
            raise ValueError(f"unexpected seed pool: {decision_path}")
        if decision["protected_validation_seeds_consumed"] or decision["protected_release_seeds_consumed"]:
            raise ValueError(f"protected seed consumed: {decision_path}")
        for path in sorted(root.glob("*/evaluation.json")):
            evaluation = _read(path)
            if evaluation["paired_runs"] != 24:
                raise ValueError(f"incomplete evaluation: {path}")
            candidate_id = evaluation["candidate"]["candidate_id"]
            funnel = evaluation["decision_funnel"]
            rows.append({
                "campaign": campaign,
                "color": color,
                "candidate_id": candidate_id,
                "label": SHORT_LABELS[candidate_id],
                "controller": evaluation["candidate"]["controller"],
                "mechanism_exercised": not (
                    evaluation["candidate"]["controller"] == "mpc"
                    and int(funnel.get("proposed", 0)) == 0
                ),
                "evaluation": evaluation,
                "path": path,
            })
            source_paths.append(path)
    if len(rows) != 15 or survival["trials"] != 125:
        raise ValueError("session experiment inventory is incomplete")
    return {"survival": survival, "rows": rows, "source_paths": source_paths}


def _new_title(fig: Any, heading: str, subtitle: str) -> None:
    base._title(
        fig, heading, subtitle,
        footer="Fresh development seeds 46401–46472 · validation/release untouched",
    )


def _distribution_blind_survival(data: dict[str, Any], figures: Path):
    by_dist = data["survival"]["by_distribution"]
    order = ["uniform", "weibull", "lognormal", "heavy-tail-mixture", "drift"]
    labels = ["Uniform", "Weibull", "Lognormal", "Heavy-tail mixture", "Drift"]
    means = np.asarray([by_dist[name]["mean_calibration_mae"] * 100 for name in order])
    worst = np.asarray([by_dist[name]["worst_calibration_mae"] * 100 for name in order])
    fig, ax = plt.subplots(figsize=(13.333, 7.5))
    fig.subplots_adjust(left=.14, right=.96, top=.86, bottom=.22)
    _new_title(
        fig, "Distribution-blind lifecycle survival works mechanically",
        "125 auditor-hidden trials · fit sees only start, end, censor, class and timestamps",
    )
    y = np.arange(len(order))
    ax.barh(y + 0.17, worst, height=0.30, color="#B9C7D8", label="Worst trial MAE")
    bars = ax.barh(y - 0.17, means, height=0.30, color=[BLUE, CYAN, PURPLE, GOLD, CORAL],
                   label="Mean calibration MAE")
    for bar, value in zip(bars, means):
        ax.text(value + 0.06, bar.get_y() + bar.get_height() / 2, f"{value:.2f}%",
                va="center", color=NAVY, fontweight="bold")
    refreshed = by_dist["drift"]["mean_refreshed_calibration_mae"] * 100
    ax.annotate(
        f"Refresh: {means[-1]:.2f}% → {refreshed:.2f}%",
        xy=(refreshed, y[-1] - 0.17), xytext=(5.0, y[-1] - 0.65),
        arrowprops={"arrowstyle": "->", "color": GREEN, "lw": 2},
        color=GREEN, fontweight="bold",
    )
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("Kaplan–Meier calibration MAE (%) — lower is better")
    ax.set_xlim(0, max(worst) + 1.4)
    ax.grid(axis="x")
    ax.legend(loc="upper right")
    fig.text(0.18, 0.125, "WORKED", color=GREEN, fontweight="bold", fontsize=12)
    fig.text(0.27, 0.125, "Staleness fail-closed 100% · pooling coverage 25%",
             color=NAVY, fontsize=9.5)
    fig.text(0.18, 0.078, "BOUNDARY", color=CORAL, fontweight="bold", fontsize=12)
    fig.text(0.29, 0.078, "Simulated observations ≠ external real-world calibration",
             color=NAVY, fontsize=9.5)
    return base._save(fig, figures, "13_distribution_blind_survival_matrix"), {
        "mean_mae_percent": dict(zip(order, means.tolist())),
        "worst_mae_percent": dict(zip(order, worst.tolist())),
        "drift_refreshed_mae_percent": refreshed,
    }


def _development_forest(data: dict[str, Any], figures: Path):
    rows = data["rows"]
    fig, ax = plt.subplots(figsize=(13.333, 7.5))
    fig.subplots_adjust(left=.20, right=.97, top=.86, bottom=.17)
    _new_title(
        fig, "Every new development candidate: mean, uncertainty and mechanism status",
        "360 paired one-day runs · confidence interval must exclude zero; mean must reach +10%",
    )
    y = np.arange(len(rows))[::-1]
    payload = {}
    for yi, row in zip(y, rows):
        ev = row["evaluation"]
        value = ev["mean_pair_ul_overload_area_relative_reduction"] * 100
        lo, hi = [item * 100 for item in ev["mean_pair_ul_reduction_bootstrap_95_interval"]]
        marker = "o" if row["mechanism_exercised"] else "X"
        color = row["color"] if row["mechanism_exercised"] else MUTED
        ax.errorbar(value, yi, xerr=[[value - lo], [hi - value]], fmt=marker,
                    color=color, ecolor=color, capsize=3, markersize=7, lw=1.7)
        ax.text(hi + 0.8, yi, f"{value:+.2f}%", va="center", fontsize=8.5, color=NAVY)
        payload[row["candidate_id"]] = {"mean": value, "ci": [lo, hi],
                                         "mechanism_exercised": row["mechanism_exercised"]}
    ax.axvspan(-25, 0, color=LIGHT_CORAL, alpha=0.55)
    ax.axvline(0, color=NAVY, lw=1.5)
    ax.axvline(10, color=GREEN, lw=2, ls="--")
    ax.text(10.3, len(rows) - 0.2, "Mean gate +10%", color=GREEN, fontsize=9)
    ax.set_yticks(y, [row["label"] for row in rows], fontsize=8.5)
    ax.set_xlabel("Mean paired UL-overload improvement with bootstrap 95% interval (%)")
    ax.set_xlim(-25, 42)
    ax.grid(axis="x")
    for boundary in (9.5, 4.5):
        ax.axhline(boundary, color=GRID, lw=1.4)
    fig.text(0.56, 0.075, "X = no proposed action: valid fail-closed, invalid mechanism exercise",
             color=MUTED, fontsize=8.5)
    return base._save(fig, figures, "14_all_session_candidates_forest"), payload


def _new_scenario_heatmap(data: dict[str, Any], figures: Path):
    rows = data["rows"]
    matrix = np.asarray([
        [row["evaluation"]["by_scenario"][kind]["mean_ul_reduction"] * 100
         for kind in base.SCENARIOS]
        for row in rows
    ])
    fig, ax = plt.subplots(figsize=(13.333, 7.5))
    fig.subplots_adjust(left=.18, right=.94, top=.86, bottom=.15)
    _new_title(
        fig, "Where gains came from—and where they failed",
        "Scenario-stratified mean UL-overload improvement (%) · scheduled benefit did not generalize",
    )
    norm = TwoSlopeNorm(vmin=min(-15, float(matrix.min())), vcenter=0,
                        vmax=max(45, float(matrix.max())))
    image = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", norm=norm)
    ax.set_xticks(range(4), base.SCENARIO_LABELS, fontsize=10)
    ax.set_yticks(range(len(rows)), [row["label"] for row in rows], fontsize=8)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            value = matrix[i, j]
            ax.text(j, i, f"{value:+.1f}", ha="center", va="center", fontsize=7.5,
                    color="white" if abs(value) > 25 else NAVY, fontweight="bold")
    fig.colorbar(image, ax=ax, pad=0.015, label="Improvement (%)")
    fig.text(0.52, 0.067,
             "WORKED: scheduled-fault pre-drain · DID NOT: robust transfer to mixed stress",
             color=NAVY, fontsize=10.5, fontweight="bold")
    return base._save(fig, figures, "15_new_scenario_improvement_heatmap"), {
        row["candidate_id"]: dict(zip(base.SCENARIOS, values.tolist()))
        for row, values in zip(rows, matrix)
    }


def _new_gate_scorecard(data: dict[str, Any], figures: Path):
    rows = data["rows"]
    gates = list(GATE_LABELS)
    matrix = np.asarray([
        [1 if row["evaluation"]["development_gates"].get(gate) is True
         else 0 if row["evaluation"]["development_gates"].get(gate) is False
         else -1 for gate in gates]
        for row in rows
    ])
    fig, ax = plt.subplots(figsize=(13.333, 7.5))
    fig.subplots_adjust(left=.19, right=.97, top=.86, bottom=.30)
    _new_title(
        fig, "Complete promotion scorecard: no all-green row",
        "Green = pass · red = fail · gray = gate not yet registered in that historical interface",
    )
    ax.imshow(matrix + 1, aspect="auto", cmap=ListedColormap(["#DDE5ED", LIGHT_CORAL, LIGHT_GREEN]),
              vmin=0, vmax=2)
    ax.set_yticks(range(len(rows)), [row["label"] for row in rows], fontsize=8)
    ax.set_xticks(range(len(gates)), [GATE_LABELS[gate] for gate in gates], rotation=48,
                  ha="right", fontsize=7.5)
    symbols = {-1: "—", 0: "×", 1: "✓"}
    colors = {-1: MUTED, 0: CORAL, 1: GREEN}
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax.text(j, i, symbols[int(matrix[i, j])], ha="center", va="center",
                    color=colors[int(matrix[i, j])], fontsize=10, fontweight="bold")
    ax.set_xticks(np.arange(-.5, len(gates), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(rows), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="minor", bottom=False, left=False)
    return base._save(fig, figures, "16_all_session_gate_scorecard"), {
        row["candidate_id"]: row["evaluation"]["development_gates"] for row in rows
    }


def _predrain_frontier(data: dict[str, Any], figures: Path):
    rows = [row for row in data["rows"] if row["controller"] == "predrain"]
    fig, ax = plt.subplots(figsize=(13.333, 7.5))
    fig.subplots_adjust(left=.12, right=.97, top=.86, bottom=.17)
    _new_title(
        fig, "Pre-drain benefit–tail frontier did not produce a winner",
        "Point size = routing churn · promotion requires mean ≥10% and worst pair >−10%",
    )
    ax.add_patch(Rectangle((-10, 10), 11, 12, facecolor=LIGHT_GREEN,
                           edgecolor="none", alpha=.65, zorder=0))
    ax.axvline(-10, color=CORAL, ls="--", lw=2)
    ax.axhline(10, color=GREEN, ls="--", lw=2)
    payload = {}
    label_offsets = {
        "predrain-balanced": (5, 5), "predrain-early-diverse": (5, 5),
        "predrain-balanced-blend-050": (5, 7),
        "predrain-balanced-blend-025": (5, 5),
        "predrain-fixed-035": (5, -2), "predrain-fixed-040": (5, 9),
        "predrain-fixed-045": (5, -12),
        "predrain-adaptive-050-075": (-92, 8),
        "predrain-adaptive-060-085": (-92, -12),
    }
    for row in rows:
        ev = row["evaluation"]
        worst = ev["worst_pair_ul_overload_area_relative_reduction"] * 100
        mean_value = ev["mean_pair_ul_overload_area_relative_reduction"] * 100
        churn = ev["operational"]["normalized_churn_l1_per_group_decision"]
        size = 150 + churn * 5000
        ax.scatter(worst, mean_value, s=size, color=row["color"], edgecolor="white", lw=1.5,
                   alpha=0.92)
        ax.annotate(row["label"].split(" · ")[-1], (worst, mean_value),
                    xytext=label_offsets[row["candidate_id"]],
                    textcoords="offset points", fontsize=7.5, color=NAVY)
        payload[row["candidate_id"]] = {"worst": worst, "mean": mean_value, "churn": churn}
    ax.set_xlabel("Worst paired UL-overload improvement (%) — farther right is safer")
    ax.set_ylabel("Mean paired UL-overload improvement (%) — higher is better")
    ax.set_xlim(-23, 1)
    ax.set_ylim(0, 22)
    ax.grid()
    ax.text(-9.6, 20.5, "Required quadrant", color=GREEN, fontweight="bold")
    fig.text(0.48, 0.073,
             "v2 50%: enough mean, tail miss · v2 25% / P3.2: safer, insufficient/uncertain mean",
             fontsize=9.5, color=NAVY, fontweight="bold")
    return base._save(fig, figures, "17_predrain_benefit_tail_frontier"), payload


def _mpc_funnel(data: dict[str, Any], figures: Path):
    rows = [row for row in data["rows"] if row["controller"] == "mpc"]
    fig, ax = plt.subplots(figsize=(13.333, 7.5))
    fig.subplots_adjust(left=.20, right=.97, top=.86, bottom=.18)
    _new_title(
        fig, "MPC mechanism funnel: v1 was inert; v2 exercised and still did not help",
        "Requested → proposed → certified → executed actions across 24 paired runs per candidate",
    )
    y = np.arange(len(rows))
    stages = ("requested", "proposed", "certified", "executed")
    colors = ("#D9E2EC", GOLD, CYAN, GREEN)
    offsets = (-0.27, -0.09, 0.09, 0.27)
    payload = {}
    for stage, color, offset in zip(stages, colors, offsets):
        values = [int(row["evaluation"]["decision_funnel"].get(stage, 0)) for row in rows]
        ax.barh(y + offset, values, height=0.16, color=color, label=stage.title())
        for yi, value in zip(y, values):
            if stage != "requested":
                ax.text(value + 35, yi + offset, str(value), va="center", fontsize=7.5, color=NAVY)
    for row in rows:
        payload[row["candidate_id"]] = {
            stage: int(row["evaluation"]["decision_funnel"].get(stage, 0)) for stage in stages
        }
    ax.set_yticks(y, [row["label"] for row in rows], fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlabel("Decision/action count")
    ax.grid(axis="x")
    ax.legend(ncol=4, loc="upper right")
    fig.text(0.54, 0.105, "WORKED", color=GREEN, fontsize=11.5, fontweight="bold")
    fig.text(0.63, 0.105, "Preflight enabled 1,697 proposals / 1,032 executions",
             color=NAVY, fontsize=9.5)
    fig.text(0.54, 0.067, "DID NOT", color=CORAL, fontsize=11.5, fontweight="bold")
    fig.text(0.63, 0.067, "Exercised MPC averaged −0.16% to −0.29%",
             color=NAVY, fontsize=9.5)
    return base._save(fig, figures, "18_mpc_action_funnel"), payload


def _latency_audit(data: dict[str, Any], figures: Path):
    mpc = [row for row in data["rows"] if row["campaign"] == "Phase 3.1 v2"
           and row["controller"] == "mpc"]
    phase32 = [row for row in data["rows"] if row["campaign"] == "Phase 3.2 v1"]
    fig, axes = plt.subplots(1, 2, figsize=(13.333, 7.5), gridspec_kw={"wspace": .35})
    _new_title(
        fig, "Operational latency: solver status alone hid deadline risk",
        "Left: MPC model+solve diagnostic versus 2 s per-call limit · right: full pre-drain decision versus 500 ms gate",
    )
    left_values = [row["evaluation"]["operational"]["max_solver_runtime_ms"] for row in mpc]
    left_labels = [row["label"].replace("P3.1v2 · ", "") for row in mpc]
    axes[0].bar(range(len(mpc)), left_values, color=PURPLE)
    axes[0].axhline(2000, color=CORAL, ls="--", lw=2, label="Configured 2 s per-call limit")
    axes[0].set_xticks(range(len(mpc)), left_labels, rotation=18, ha="right", fontsize=8)
    axes[0].set_ylabel("Maximum diagnostic time (ms)")
    axes[0].set_title("P3.1 v2 MPC", color=NAVY)
    axes[0].legend(fontsize=8)
    for i, value in enumerate(left_values):
        axes[0].text(i, value + 100, f"{value/1000:.2f}s", ha="center", fontsize=8, fontweight="bold")
    right_values = [row["evaluation"]["operational"]["max_decision_runtime_ms"] for row in phase32]
    right_labels = [row["label"].replace("P3.2 · ", "") for row in phase32]
    colors = [GREEN if value <= 500 else CORAL for value in right_values]
    axes[1].bar(range(len(phase32)), right_values, color=colors)
    axes[1].axhline(500, color=NAVY, ls="--", lw=2, label="Frozen 500 ms gate")
    axes[1].set_xticks(range(len(phase32)), right_labels, rotation=20, ha="right", fontsize=8)
    axes[1].set_ylabel("Maximum end-to-end decision time (ms)")
    axes[1].set_title("Phase 3.2 pre-drain", color=NAVY)
    axes[1].legend(fontsize=8)
    for i, value in enumerate(right_values):
        axes[1].text(i, value + 20, f"{value:.0f}", ha="center", fontsize=8, fontweight="bold")
    for ax in axes:
        ax.grid(axis="y")
    return base._save(fig, figures, "19_end_to_end_latency_audit"), {
        "phase31_v2_mpc_max_ms": dict(zip(left_labels, left_values)),
        "phase32_end_to_end_max_ms": dict(zip(right_labels, right_values)),
    }


def _adaptive_mechanism(data: dict[str, Any], figures: Path):
    adaptive = [row for row in data["rows"] if "adaptive" in row["candidate_id"]]
    fig, axes = plt.subplots(1, 2, figsize=(13.333, 7.5), gridspec_kw={"wspace": .28})
    _new_title(
        fig, "Adaptive pre-drain worked as coded—but causal lag defeated the tail hypothesis",
        "Applied blend responds to observed residual utilization; worst pair's surprise begins after commitment starts",
    )
    payload: dict[str, Any] = {}
    for row, color in zip(adaptive, (BLUE, PURPLE)):
        diagnostics = [
            item for pair in row["evaluation"]["pairs"]
            for item in pair["decision_diagnostics"] if item["solver_status"] == "optimal"
        ]
        util = np.asarray([item["max_residual_utilization"] * 100 for item in diagnostics])
        blend = np.asarray([item["action_blend_fraction"] * 100 for item in diagnostics])
        axes[0].scatter(util, blend, s=15, alpha=.35, color=color,
                        label=row["label"].replace("P3.2 · ", ""))
        payload[row["candidate_id"]] = {
            "blend_min": float(blend.min()), "blend_mean": float(blend.mean()),
            "blend_max": float(blend.max()), "util_min": float(util.min()),
            "util_max": float(util.max()),
        }
    axes[0].set_xlabel("Observed maximum residual utilization (%)")
    axes[0].set_ylabel("Applied pre-drain strength (%)")
    axes[0].set_xlim(20, 180)
    axes[0].set_ylim(20, 55)
    axes[0].grid()
    axes[0].legend(fontsize=8)
    axes[0].set_title("Mechanism exercised across full 25–50% range", color=GREEN)

    worst = adaptive[0]["evaluation"]
    pair = min(worst["pairs"], key=lambda item: item["relative_reduction"]["overload_area_seconds"]["ul"])
    optimal = [item for item in pair["decision_diagnostics"] if item["solver_status"] == "optimal"]
    steps = np.asarray([(int(item["version"]) - 1) * 20 for item in optimal])
    blends = np.asarray([item["action_blend_fraction"] * 100 for item in optimal])
    axes[1].step(steps, blends, where="post", color=BLUE, lw=3, label="Applied blend")
    scheduled = next(event for event in pair["events"] if event["known_at_step"] is not None
                     and event["event_type"] == "capacity_factor" and (event["ul_factor"] or 1) < 1)
    surprise = next(event for event in pair["events"] if event["event_type"] == "arrival_factor"
                    and (event["arrival_factor"] or 1) > 1)
    axes[1].axvline(scheduled["known_at_step"], color=GOLD, lw=2, label="Fault becomes known")
    axes[1].axvline(surprise["step"], color=CORAL, lw=2, label="Surprise arrival starts")
    axes[1].axvline(scheduled["step"], color=NAVY, lw=2, ls="--", label="Capacity loss")
    axes[1].set_xlabel("Simulation step")
    axes[1].set_ylabel("Applied pre-drain strength (%)")
    axes[1].set_ylim(20, 55)
    axes[1].grid()
    axes[1].legend(fontsize=8, loc="lower left")
    axes[1].set_title(f"Worst pair seed {pair['seed']}: full 50% throughout lead-up", color=CORAL)
    return base._save(fig, figures, "20_adaptive_blend_causal_lag"), payload


def _campaign_scale(data: dict[str, Any], figures: Path):
    names = ["Original\nPhase 3", "Distribution-blind\nsurvival", "Phase 3.1\nv1", "Phase 3.1\nv2", "Phase 3.2\nv1"]
    counts = [228, 125, 120, 120, 120]
    types = ["paired", "trials", "paired", "paired", "paired"]
    colors = [BLUE, GOLD, BLUE, PURPLE, CYAN]
    fig, axes = plt.subplots(1, 2, figsize=(13.333, 7.5), gridspec_kw={"width_ratios": [1.55, 1]})
    _new_title(
        fig, "Campaign scale and release discipline",
        "588 paired one-day controller runs + 125 distribution-blind survival trials · zero protected evaluations",
    )
    bars = axes[0].bar(range(len(names)), counts, color=colors)
    axes[0].set_xticks(range(len(names)), names, fontsize=9)
    axes[0].set_ylabel("Experiment count")
    axes[0].grid(axis="y")
    for bar, value, kind in zip(bars, counts, types):
        axes[0].text(bar.get_x() + bar.get_width()/2, value + 5, f"{value}\n{kind}",
                     ha="center", fontsize=9, color=NAVY, fontweight="bold")
    axes[0].set_ylim(0, 265)
    axes[0].set_title("All session experiments represented in this deck", color=NAVY)
    seed_blocks = [
        ("Development", "46101–46112\n46401–46472", BLUE, "consumed as registered"),
        ("Validation", "46201–46216", GREEN, "UNTOUCHED"),
        ("Release", "46301–46330", GREEN, "UNTOUCHED"),
        ("Forecast test", "46003", GOLD, "generated, never evaluated"),
    ]
    axes[1].axis("off")
    for index, (name, seeds, color, state) in enumerate(seed_blocks):
        y = .84 - index * .21
        patch = FancyBboxPatch((.04, y-.12), .92, .15, boxstyle="round,pad=.02,rounding_size=.025",
                               facecolor="#F8FAFC", edgecolor=color, linewidth=2,
                               transform=axes[1].transAxes)
        axes[1].add_patch(patch)
        axes[1].text(.08, y-.005, name, transform=axes[1].transAxes, color=NAVY,
                     fontweight="bold", fontsize=11)
        axes[1].text(.08, y-.065, seeds.replace("\n", " · "), transform=axes[1].transAxes,
                     color=MUTED, fontsize=8.7)
        axes[1].text(.93, y-.005, state, transform=axes[1].transAxes, color=color,
                     ha="right", fontsize=9, fontweight="bold")
    axes[1].set_title("Seed firewall held", color=GREEN, fontweight="bold")
    return base._save(fig, figures, "21_campaign_scale_seed_firewall"), {
        "campaign_counts": dict(zip(names, counts)), "paired_total": 588,
        "survival_trials": 125, "protected_consumed": False,
    }


def _worked_not_worked(data: dict[str, Any], figures: Path):
    worked = [
        ("Reproducibility", "29/29 frozen interfaces restored; content-addressed archives"),
        ("Lifecycle estimator", "Observable export + Kaplan–Meier calibrated across 5 hidden distributions"),
        ("Fail-closed behavior", "Stale survival returned to Static in 100% of trials"),
        ("Mechanism visibility", "Proposed → certified → accepted → executed funnel captured"),
        ("Scheduled headroom", "Pre-drain repeatedly reduced scheduled-fault overload"),
        ("Release discipline", "588 paired runs, 28 candidates, zero unsafe promotions"),
    ]
    failed = [
        ("Forecast promotion", "Useful gains remained below the frozen 15% target"),
        ("MPC benefit", "Exercised variants were neutral/slightly harmful"),
        ("Generalization", "Scheduled benefit did not transfer safely to mixed stress"),
        ("Tail safety", "Strong blends missed the −10% worst-pair gate"),
        ("Adaptive protection", "Surprise demand arrived after persistent-session commitment began"),
        ("Operational latency", "Solver status hid end-to-end outliers up to 970 ms / MPC >6 s"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(13.333, 7.5), gridspec_kw={"wspace": .08})
    _new_title(
        fig, "What worked—and what did not",
        "Mechanism success is not promotion success · all claims remain bounded by measured evidence",
    )
    for ax, title, rows, color, symbol in (
        (axes[0], "WORKED", worked, GREEN, "✓"),
        (axes[1], "DID NOT WORK", failed, CORAL, "×"),
    ):
        ax.axis("off")
        ax.text(.03, .94, title, transform=ax.transAxes, color=color, fontsize=20, fontweight="bold")
        for index, (heading, detail) in enumerate(rows):
            y = .83 - index * .125
            patch = FancyBboxPatch((.02, y-.075), .96, .095,
                                   boxstyle="round,pad=.012,rounding_size=.02",
                                   facecolor=LIGHT_GREEN if color == GREEN else LIGHT_CORAL,
                                   edgecolor="none", transform=ax.transAxes)
            ax.add_patch(patch)
            ax.text(.05, y-.028, symbol, transform=ax.transAxes, color=color,
                    fontsize=18, fontweight="bold", va="center")
            ax.text(.12, y, heading, transform=ax.transAxes, color=NAVY,
                    fontsize=10.5, fontweight="bold", va="top")
            ax.text(.12, y-.037, detail, transform=ax.transAxes, color=MUTED,
                    fontsize=8.4, va="top", wrap=True)
    fig.text(.50, .055, "FINAL DECISION  ·  RETAIN STATIC  ·  DO NOT CONSUME VALIDATION/RELEASE SEEDS",
             ha="center", color=NAVY, fontsize=12.5, fontweight="bold")
    return base._save(fig, figures, "22_worked_vs_did_not_work"), {
        "worked": worked, "did_not_work": failed, "decision": "retain_static",
    }


NEW_BUILDERS = (
    _distribution_blind_survival, _development_forest, _new_scenario_heatmap,
    _new_gate_scorecard, _predrain_frontier, _mpc_funnel, _latency_audit,
    _adaptive_mechanism, _campaign_scale, _worked_not_worked,
)


def _report() -> str:
    return """# C-DOT control-science showcase v5

This immutable package visualizes every control-science experiment completed
through Phase 3.2. Figures 01–12 regenerate the corrected Phase-2.1/Phase-3
story from authoritative evidence. Figures 13–22 cover all experiments executed
in the current session: 125 distribution-blind survival trials and 360 paired
one-day controller comparisons.

The deck separates five questions that must not be conflated:

1. Did the implementation execute the intended mechanism?
2. Did the mechanism improve its target scenario?
3. Did benefit generalize with confidence and acceptable tails?
4. Did the controller meet operational latency/fallback requirements?
5. Did every conjunctive release gate pass?

Some mechanisms worked: source reproducibility was restored, observable
lifecycle Kaplan–Meier fitting calibrated across hidden distributions, stale
tables failed closed, MPC was genuinely exercised after preflight repair, and
pre-drain found scheduled-fault headroom. None became release-eligible because
benefit was uncertain or scenario-concentrated, mixed-stress tails persisted,
or operational deadlines failed.

Final decision: retain Static. Validation seeds 46201–46216 and release seeds
46301–46330 remain untouched. Seed 46003 was generated and sealed but never
used for model evaluation or selection.
"""


def _talk_track() -> str:
    historical = base._talk_track()
    extension = """

# Phase 3.1/3.2 extension

## 13 — Distribution-blind survival

The fitter consumes only observable lifecycle records; hidden uniform,
Weibull, lognormal, heavy-tail and drift parameters remain auditor-only. Mean
calibration error stays near 3–4%, drift refresh improves 4.17% to 2.75%, and
stale tables fail closed. This validates mechanics on simulated observations,
not external real-world calibration.

## 14 — Every new candidate

All fifteen newer candidates appear with their paired mean and bootstrap
interval. V1 MPC rows marked with X never proposed an action because the
forecaster could not warm up; v2 repairs that interface and exercises MPC.
Strong pre-drain means are visible, but uncertainty or tails prevent promotion.

## 15 — Scenario concentration

Pre-drain works strongly on scheduled faults and is neutral on scenarios where
it correctly does not trigger. Mixed-stress regressions show the missing piece:
known-fault benefit is not robust to simultaneous surprise demand.

## 16 — Complete gates

No candidate is all green. Historical rows show gray for the end-to-end deadline
that was registered only in Phase 3.2. Gates were not weakened after near misses.

## 17 — Benefit versus tail frontier

Full-strength pre-drain has enough mean but unsafe tails/churn. Weak blends are
safer but insufficient. The fresh Phase 3.2 pool moves all interpolated points
below the mean threshold and their intervals cross zero—there is no stable
sweet spot to tune into existence.

## 18 — MPC funnel

Phase 3.1 v1 is a valid fail-closed outcome but an invalid mechanism exercise.
Short-history preflight in v2 enables 1,697 proposals and 1,032 executions.
The exercised controller still averages slightly harmful results, proving the
negative finding is no longer a warm-up artifact.

## 19 — Latency

No-timeout status was insufficient. MPC model/solve diagnostics exceeded six
seconds despite a two-second per-call setting. Phase 3.2 therefore adds an
end-to-end 500 ms gate; only one of five candidates meets it under saturation.

## 20 — Adaptive causal lag

The adaptive blend spans its complete 25–50% range across the campaign, so the
implementation works. On the worst pair, however, surprise demand starts after
pre-drain commitments begin and residual telemetry remains below the trigger.
The controller stays at 50% throughout the critical lead-up.

## 21 — Scale and seed firewall

The complete story covers 588 paired controller runs plus 125 survival trials.
Development pools were consumed as registered; validation and release pools
remain untouched. Forecast seed 46003 was generated but never evaluated.

## 22 — What worked / did not work

The successful outcome is the science and safety system: reproducibility,
mechanism visibility, calibration testing, fallbacks and conjunctive promotion.
The candidate algorithms did not deliver robust, operationally ready benefit.
Static remains deployed.
"""
    return historical + extension


def _executive_summary() -> str:
    return """# Executive summary — what worked and what did not

| Area | Worked | Did not work / boundary |
|---|---|---|
| Reproducibility | Restored 29/29 Phase-3 hashes; content-addressed archives | Historical sealed evidence alone could not reproduce from overwritten checkout |
| Survival | Observable lifecycle export, Kaplan–Meier calibration, pooling and stale fail-closed | Still simulation-derived observations; no external real-world calibration |
| MPC interface | Preflight repaired unreachable history; 1,697 proposals and 1,032 executions | Exercised variants remained neutral/slightly harmful |
| Pre-drain | Large scheduled-fault headroom; fast bounded min-cost flow | Benefit did not generalize to mixed stress; strong actions failed tails |
| Adaptive blend | Applied complete 25–50% range causally | Surprise demand arrived after commitment; worst tail persisted |
| Operations | Full funnel/model/timing telemetry; new end-to-end gate | Status-only timeout check hid multi-second MPC and sub-second outliers |
| Release process | 588 paired runs, frozen gates, zero protected-seed leakage | Zero candidate passed every gate |

Decision: **retain Static**. Do not consume validation or release seeds.
"""


def _build_pdf(output: Path, figures: list[Path]) -> None:
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    with PdfPages(temporary) as pdf:
        cover = plt.figure(figsize=(13.333, 7.5), facecolor=NAVY)
        cover.text(.07, .80, "C-DOT control science", color="white", fontsize=28, fontweight="bold")
        cover.text(.07, .65, "Phase 2.1 → Phase 3.2", color="#8FD3FF", fontsize=38, fontweight="bold")
        cover.text(.07, .53, "Every experiment · every gate · every boundary", color="white", fontsize=19)
        cover.text(.07, .32, "Evidence scale", color=GOLD, fontsize=13, fontweight="bold")
        cover.text(.07, .23, "588 paired one-day runs  +  125 survival trials",
                   color="white", fontsize=20, fontweight="bold")
        cover.text(.07, .09, "Outcome: no candidate passed every gate · Static remains production default",
                   color="#B8C7D9", fontsize=12)
        plt.axis("off"); pdf.savefig(cover, facecolor=NAVY); plt.close(cover)
        for path in figures:
            image = plt.imread(path)
            page = plt.figure(figsize=(13.333, 7.5), facecolor="white")
            ax = page.add_axes([.01, .01, .98, .98]); ax.imshow(image); ax.axis("off")
            pdf.savefig(page, facecolor="white", bbox_inches="tight", pad_inches=.02)
            plt.close(page)
        final = plt.figure(figsize=(13.333, 7.5), facecolor="white")
        final.text(.07, .82, "Final decision", color=NAVY, fontsize=28, fontweight="bold")
        final.text(.07, .66, "Retain Static", color=GREEN, fontsize=42, fontweight="bold")
        final.text(.07, .49,
                   "What worked: reproducibility, lifecycle estimation mechanics,\n"
                   "fail-closed controls, mechanism telemetry and release discipline.",
                   color=NAVY, fontsize=17, linespacing=1.5)
        final.text(.07, .30,
                   "What did not: robust cross-scenario benefit, tail safety,\n"
                   "MPC material improvement and consistent operational latency.",
                   color=CORAL, fontsize=17, linespacing=1.5)
        final.text(.07, .10, "Validation 46201–46216 · Release 46301–46330 · UNTOUCHED",
                   color=NAVY, fontsize=13, fontweight="bold")
        plt.axis("off"); pdf.savefig(final, facecolor="white"); plt.close(final)
    os.replace(temporary, output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if args.output_root.exists():
        raise FileExistsError(f"refusing to overwrite presentation package: {args.output_root}")
    base._style()
    historical = base._load_inputs(ROOT)
    current = _load_new_inputs()
    figures_root = args.output_root / "figures"
    old_builders = (
        base._forecast_targets, base._forecast_worst_slice, base._survival_calibration,
        base._survival_curves, base._survival_mpc_forest, base._survival_paired,
        base._candidate_forest, base._scenario_heatmap, base._solver_status,
        base._gate_scorecard, base._guarded_architecture, base._campaign_scale,
    )
    outputs: list[Path] = []
    figure_data: dict[str, Any] = {}
    for builder in old_builders:
        generated, payload = builder(historical, figures_root)
        outputs.extend(generated); figure_data[generated[0].stem] = payload
    for builder in NEW_BUILDERS:
        generated, payload = builder(current, figures_root)
        outputs.extend(generated); figure_data[generated[0].stem] = payload
    args.output_root.mkdir(parents=True, exist_ok=True)
    base._atomic_json(args.output_root / "figure-data.json", figure_data)
    base._atomic_text(args.output_root / "REPORT.md", _report())
    base._atomic_text(args.output_root / "TALK_TRACK.md", _talk_track())
    base._atomic_text(args.output_root / "EXECUTIVE_SUMMARY.md", _executive_summary())
    pngs = sorted(path for path in outputs if path.suffix == ".png")
    pdf = args.output_root / "cdot_control_science_showcase_v5.pdf"
    _build_pdf(pdf, pngs)
    source_paths = (
        list(historical["paths"].values()) + list(historical["evaluation_paths"].values())
        + list(historical["survival_paths"].values())
        + list(historical["survival_bundle_paths"].values())
        + current["source_paths"] + [Path(base.__file__).resolve(), Path(__file__).resolve()]
    )
    generated_files = sorted(
        path for path in args.output_root.rglob("*")
        if path.is_file() and path.name != "artifact-manifest.json"
    )
    manifest = {
        "schema_version": "phase3-cdot-showcase/1.2",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_artifacts": {
            str(path.relative_to(ROOT)): _sha256(path) for path in sorted(set(source_paths))
        },
        "outputs": {
            str(path.relative_to(args.output_root)): _sha256(path) for path in generated_files
        },
        "figures": len(pngs),
        "session_experiment_inventory": {
            "distribution_blind_survival_trials": 125,
            "paired_controller_runs": 360,
            "candidates": 15,
        },
        "complete_experiment_inventory": {
            "paired_controller_runs": 588,
            "survival_trials": 125,
            "candidate_controllers": 28,
        },
        "protected_seed_state": {
            "forecast_46003_status": "generated_and_sealed_but_never_evaluated_or_selected",
            "validation_46201_46216_consumed": False,
            "release_46301_46330_consumed": False,
        },
        "decision": "retain_static",
    }
    base._atomic_json(args.output_root / "artifact-manifest.json", manifest)
    print(json.dumps({
        "output": str(args.output_root.resolve()), "figures": len(pngs),
        "pdf": str(pdf.resolve()), "artifacts": len(generated_files) + 1,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
