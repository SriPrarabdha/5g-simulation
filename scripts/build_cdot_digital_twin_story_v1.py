#!/usr/bin/env python3
"""Build the integrated C-DOT 5G digital-twin evidence story.

This deck deliberately joins the traffic-model, forecasting, optimization,
controller-science, safety, and cluster-scale evidence without running or
rescoring any experiment.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
for import_root in (PROJECT_ROOT, SCRIPTS_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import scripts.build_phase3_cdot_showcase_v5 as v5
import scripts.build_phase3_cdot_showcase_v6 as v6
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = v5.ROOT
base = v5.base
plt = v5.plt
np = v5.np
NAVY, BLUE, CYAN = v5.NAVY, v5.BLUE, v5.CYAN
GOLD, CORAL, GREEN = v5.GOLD, v5.CORAL, v5.GREEN
PURPLE, MUTED = v5.PURPLE, v5.MUTED
LIGHT_GREEN, LIGHT_CORAL = v5.LIGHT_GREEN, v5.LIGHT_CORAL

DELHI = ROOT / "presentation/delhi/charts"
PRODUCTION = ROOT / "output/showcase/cdot-production-final"
CONTROL = ROOT / "output/control-science/v1/phase3-cdot-showcase-v6"
V6_FREEZE = ROOT / "output/control-science/v1/phase3-cdot-v6-interface-freeze.json"
STORY_FREEZE = ROOT / "output/showcase/cdot-digital-twin-story-v1-interface-freeze.json"
STORY_ARCHIVE = ROOT / "output/showcase/source-archives/cdot-digital-twin-story-v1"


def _sha256(path: Path) -> str:
    return v5._sha256(path)


def _title(fig: Any, chapter: str, heading: str, subtitle: str) -> None:
    fig.text(.06, .945, chapter.upper(), color=CYAN, fontsize=10, fontweight="bold")
    fig.text(.06, .885, heading, color=NAVY, fontsize=24, fontweight="bold")
    fig.text(.06, .835, subtitle, color=MUTED, fontsize=11)


def _box(ax: Any, xy: tuple[float, float], width: float, height: float,
         title: str, detail: str, color: str, *, title_size: float = 11) -> None:
    patch = FancyBboxPatch(
        xy, width, height, boxstyle="round,pad=.018,rounding_size=.025",
        facecolor="#F8FAFC", edgecolor=color, linewidth=2,
        transform=ax.transAxes,
    )
    ax.add_patch(patch)
    ax.text(xy[0] + width / 2, xy[1] + height * .64, title,
            transform=ax.transAxes, ha="center", va="center", color=NAVY,
            fontsize=title_size, fontweight="bold")
    ax.text(xy[0] + width / 2, xy[1] + height * .30, detail,
            transform=ax.transAxes, ha="center", va="center", color=MUTED,
            fontsize=8.3, linespacing=1.3)


def _save_custom(fig: Any, figures: Path, stem: str) -> list[Path]:
    return list(base._save(fig, figures, stem))


def _executive_slide(figures: Path) -> tuple[list[Path], dict[str, Any]]:
    fig = plt.figure(figsize=(13.333, 7.5), facecolor="white")
    _title(
        fig, "The result", "The digital twin succeeded; controller promotion did not",
        "A useful scientific outcome: engineering readiness and algorithm readiness are different claims",
    )
    ax = fig.add_axes([.04, .08, .92, .70]); ax.axis("off")
    columns = (
        (.02, "TWIN", GREEN, (
            "Causal cohort simulation", "Bounded streaming memory",
            "12 nodes · 384 shards", "Frozen, hashed evidence",
        )),
        (.345, "LEARNING + SEARCH", BLUE, (
            "Forecast gains measured", "Lifecycle survival calibrated",
            "Oracle headroom mapped", "Mechanisms fully exercised",
        )),
        (.67, "CONTROL", CORAL, (
            "MPC benefit unstable", "Pre-drain failed mixed stress",
            "Tail and overflow gates missed", "Static remains production",
        )),
    )
    for x, heading, color, rows in columns:
        patch = FancyBboxPatch(
            (x, .12), .29, .72, boxstyle="round,pad=.02,rounding_size=.03",
            facecolor=LIGHT_GREEN if color == GREEN else "#F2F7FC" if color == BLUE else LIGHT_CORAL,
            edgecolor=color, linewidth=2, transform=ax.transAxes,
        )
        ax.add_patch(patch)
        ax.text(x + .145, .74, heading, transform=ax.transAxes, ha="center",
                color=color, fontsize=14, fontweight="bold")
        for index, row in enumerate(rows):
            ax.text(x + .035, .61 - index * .13, "✓" if color != CORAL else "×",
                    transform=ax.transAxes, color=color, fontsize=15, fontweight="bold")
            ax.text(x + .075, .615 - index * .13, row, transform=ax.transAxes,
                    color=NAVY, fontsize=10.2, va="center")
    fig.text(.5, .035, "DECISION  ·  STATIC IN PRODUCTION  ·  MPC / PRE-DRAIN IN SHADOW OR REPLAY",
             ha="center", color=NAVY, fontsize=12.5, fontweight="bold")
    return _save_custom(fig, figures, "02_executive_result"), {
        "twin_ready": True, "candidate_promoted": False, "production_controller": "static-capacity-v1",
    }


def _journey_slide(figures: Path) -> tuple[list[Path], dict[str, Any]]:
    fig = plt.figure(figsize=(13.333, 7.5), facecolor="white")
    _title(
        fig, "Scientific journey", "Each stage answered a different question",
        "The final decision came from progressively harder evidence—not a single benchmark",
    )
    ax = fig.add_axes([.045, .10, .91, .67]); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    stages = (
        (.03, "1 · SIMULATE", "Can the twin conserve traffic\nand reproduce stress?", GREEN),
        (.23, "2 · FORECAST", "Can observable history predict\nfuture sessions and Mbps?", BLUE),
        (.43, "3 · BOUND", "Is there causal action-space\nheadroom at all?", GOLD),
        (.63, "4 · CONTROL", "Can MPC / pre-drain realize it\nwithout unsafe tails?", PURPLE),
        (.83, "5 · DECIDE", "Do all frozen gates pass\non fresh development pools?", CORAL),
    )
    for index, (x, heading, detail, color) in enumerate(stages):
        _box(ax, (x, .36), .145, .32, heading, detail, color, title_size=10.3)
        if index < len(stages) - 1:
            arrow = FancyArrowPatch(
                (x + .15, .52), (stages[index + 1][0] - .005, .52),
                arrowstyle="-|>", mutation_scale=16, color="#9BAABE", linewidth=1.8,
                transform=ax.transAxes,
            )
            ax.add_patch(arrow)
    outcomes = (
        (.10, "PASS", "mechanics + scale", GREEN),
        (.30, "USEFUL", "below promotion bar", BLUE),
        (.50, "YES", "oracle only", GOLD),
        (.70, "NO", "robust transfer", CORAL),
        (.90, "STATIC", "retained", GREEN),
    )
    for x, headline, detail, color in outcomes:
        ax.text(x, .22, headline, ha="center", color=color, fontsize=13, fontweight="bold")
        ax.text(x, .16, detail, ha="center", color=MUTED, fontsize=8.5)
    ax.text(.5, .04,
            "Early positive signals became hypotheses; larger frozen campaigns were allowed to reject them.",
            ha="center", color=NAVY, fontsize=11.5, fontweight="bold")
    return _save_custom(fig, figures, "03_scientific_journey"), {
        "stages": [item[1] for item in stages], "terminal_decision": "retain_static",
    }


def _architecture_slide(figures: Path) -> tuple[list[Path], dict[str, Any]]:
    fig = plt.figure(figsize=(13.333, 7.5), facecolor="white")
    _title(
        fig, "System", "The 5G twin is a closed evidence loop",
        "Every proposed action is evaluated against the same causal cohort state before it can affect future sessions",
    )
    ax = fig.add_axes([.035, .08, .93, .70]); ax.axis("off")
    boxes = (
        (.02, "TRAFFIC + EVENTS", "service × hour arrivals\nmobility · faults · capacity", BLUE),
        (.215, "COHORT TWIN", "30 s steps · UL/DL\nsessions · censoring · queues", CYAN),
        (.41, "FORECASTERS", "sessions + Mbps\nuncertainty + survival", PURPLE),
        (.605, "OPTIMIZER", "oracle bounds · MPC\npre-drain flow + slack", GOLD),
        (.80, "SAFETY GATE", "same-state certificate\nfail closed to Static", GREEN),
    )
    for index, (x, heading, detail, color) in enumerate(boxes):
        _box(ax, (x, .47), .16, .25, heading, detail, color, title_size=10.5)
        if index < len(boxes) - 1:
            ax.add_patch(FancyArrowPatch(
                (x + .165, .595), (boxes[index + 1][0] - .005, .595),
                arrowstyle="-|>", mutation_scale=15, color="#8798AD", linewidth=1.7,
                transform=ax.transAxes,
            ))
    _box(ax, (.18, .08), .25, .20, "OBSERVABLE TELEMETRY",
         "arrivals · carried/dropped Mbps\nactive sessions · lifecycle records", BLUE)
    _box(ax, (.57, .08), .25, .20, "HASHED REPLAY EVIDENCE",
         "paired seeds · scenario slices\ntails · latency · resource gates", PURPLE)
    ax.add_patch(FancyArrowPatch((.88, .46), (.70, .29), connectionstyle="arc3,rad=-.25",
                                 arrowstyle="-|>", mutation_scale=15, color=GREEN,
                                 linewidth=1.8, transform=ax.transAxes))
    ax.add_patch(FancyArrowPatch((.57, .18), (.43, .18), arrowstyle="-|>",
                                 mutation_scale=15, color="#8798AD", linewidth=1.5,
                                 transform=ax.transAxes))
    ax.add_patch(FancyArrowPatch((.18, .18), (.095, .46), connectionstyle="arc3,rad=-.2",
                                 arrowstyle="-|>", mutation_scale=15, color=BLUE,
                                 linewidth=1.8, transform=ax.transAxes))
    fig.text(.5, .035, "ACTUATION BOUNDARY  ·  EXISTING SESSIONS STAY PUT  ·  ONLY FUTURE SESSIONS CAN MOVE",
             ha="center", color=NAVY, fontsize=11.5, fontweight="bold")
    return _save_custom(fig, figures, "04_digital_twin_architecture"), {
        "causal_step_seconds": 30, "actuation_scope": "future_sessions_only",
        "production_fallback": "static-capacity-v1",
    }


def _overflow_slide(figures: Path) -> tuple[list[Path], dict[str, Any]]:
    data = v6._load_new_inputs()
    affected = []
    for row in data["rows"]:
        diagnostics = [
            item for pair in row["evaluation"]["pairs"]
            for item in pair.get("decision_diagnostics", ())
        ]
        count = sum(float(item.get("overflow", 0.0)) > 1e-7 for item in diagnostics)
        maximum = max((float(item.get("overflow", 0.0)) for item in diagnostics), default=0.0)
        if count:
            affected.append((row["label"].replace("P3.1v2 · ", "").replace("P3.2 · ", ""), count, maximum))
    fig, axes = plt.subplots(1, 2, figsize=(13.333, 7.5), gridspec_kw={"width_ratios": [1.15, 1]})
    fig.subplots_adjust(left=.08, right=.97, top=.77, bottom=.15, wspace=.33)
    _title(
        fig, "Safety audit", "Overflow slack is diagnostic—not a certificate",
        "Historical outcomes stay negative; v6 corrects the controller boundary and the language used to describe it",
    )
    labels = [row[0] for row in affected]
    counts = [row[1] for row in affected]
    maxima = [row[2] for row in affected]
    y = np.arange(len(affected))
    axes[0].barh(y, counts, color=CORAL)
    axes[0].set_yticks(y, labels, fontsize=8)
    axes[0].invert_yaxis()
    axes[0].set_xlabel("Actions with predicted overflow")
    axes[0].set_title("Recorded historical diagnostics", color=NAVY, fontweight="bold")
    axes[0].grid(axis="x")
    for index, (count, maximum) in enumerate(zip(counts, maxima)):
        axes[0].text(count + .15, index, f"{count}/216  ·  max {maximum:,.0f}",
                     va="center", color=NAVY, fontsize=8, fontweight="bold")
    axes[0].set_xlim(0, max(counts, default=1) * 1.72)
    axes[1].axis("off")
    flow = (
        (.10, .76, "SOLVER OPTIMAL", "LP found a diagnostic solution", GOLD),
        (.10, .48, "OVERFLOW > 1e−7?", "inspect UL · DL · sessions", CORAL),
        (.10, .20, "STATIC FALLBACK", "record slack · never certify", GREEN),
    )
    for index, (x, yy, heading, detail, color) in enumerate(flow):
        _box(axes[1], (x, yy), .80, .16, heading, detail, color)
        if index < len(flow) - 1:
            axes[1].add_patch(FancyArrowPatch((.5, yy), (.5, flow[index + 1][1] + .17),
                                               arrowstyle="-|>", mutation_scale=15,
                                               color="#8798AD", linewidth=1.7,
                                               transform=axes[1].transAxes))
    axes[1].text(.5, .07, "zero_predicted_overflow is now conjunctive",
                 transform=axes[1].transAxes, ha="center", color=NAVY,
                 fontsize=10.5, fontweight="bold")
    return _save_custom(fig, figures, "24_overflow_fail_closed"), {
        "affected": [{"label": a, "actions": b, "max_raw_mixed_unit_slack": c}
                     for a, b, c in affected],
        "tolerance": 1e-7, "certified_with_overflow": False,
    }


def _final_slide(figures: Path) -> tuple[list[Path], dict[str, Any]]:
    fig = plt.figure(figsize=(13.333, 7.5), facecolor="white")
    fig.text(.065, .87, "THE C-DOT 5G DIGITAL TWIN", color=CYAN, fontsize=11, fontweight="bold")
    fig.text(.065, .74, "What we can defend", color=NAVY, fontsize=30, fontweight="bold")
    rows = (
        ("BUILD", "A causal, auditable cohort twin that scales to 12 nodes / 384 shards", GREEN),
        ("MEASURE", "Separate forecast, survival, solver, tail, latency, and resource claims", BLUE),
        ("LEARN", "Better prediction and theoretical headroom do not guarantee better control", GOLD),
        ("DEPLOY", "Static remains production; MPC and pre-drain remain shadow/replay", CORAL),
    )
    for index, (tag, detail, color) in enumerate(rows):
        y = .58 - index * .13
        fig.text(.07, y, tag, color=color, fontsize=12, fontweight="bold")
        fig.text(.19, y, detail, color=NAVY, fontsize=14)
    fig.text(.065, .08,
             "The strongest result is not a promoted algorithm. It is an evidence system that knew when not to promote one.",
             color=NAVY, fontsize=14, fontweight="bold")
    return _save_custom(fig, figures, "28_final_decision"), {
        "decision": "retain_static", "protected_validation_consumed": False,
        "protected_release_consumed": False,
    }


def _copy_figure(source: Path, figures: Path, stem: str) -> list[Path]:
    if not source.is_file():
        raise FileNotFoundError(source)
    figures.mkdir(parents=True, exist_ok=True)
    outputs = []
    target = figures / f"{stem}.png"
    shutil.copy2(source, target); outputs.append(target)
    source_svg = source.with_suffix(".svg")
    if source_svg.is_file():
        svg_target = figures / f"{stem}.svg"
        shutil.copy2(source_svg, svg_target); outputs.append(svg_target)
    return outputs


def _build_pdf(output: Path, slides: list[tuple[str, Path]]) -> None:
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    with PdfPages(temporary) as pdf:
        cover = plt.figure(figsize=(13.333, 7.5), facecolor=NAVY)
        cover.text(.065, .82, "C-DOT · 5G DIGITAL TWIN", color=CYAN,
                   fontsize=13, fontweight="bold")
        cover.text(.065, .66, "From synthetic traffic", color="white",
                   fontsize=36, fontweight="bold")
        cover.text(.065, .54, "to a defensible control decision", color="white",
                   fontsize=36, fontweight="bold")
        cover.text(.065, .36,
                   "Simulator  →  Forecaster  →  Optimizer  →  MPC / pre-drain  →  safety gates",
                   color="#A9C8DF", fontsize=16)
        cover.text(.065, .20, "588 controller pairs  ·  125 distribution-blind survival trials",
                   color=GOLD, fontsize=14, fontweight="bold")
        cover.text(.065, .10, "Final evidence story v1  ·  Static production  ·  candidates shadow/replay only",
                   color="#C8D6E5", fontsize=11)
        plt.axis("off"); pdf.savefig(cover, facecolor=NAVY); plt.close(cover)
        for index, (chapter, path) in enumerate(slides, start=2):
            image = plt.imread(path)
            page = plt.figure(figsize=(13.333, 7.5), facecolor="white")
            ax = page.add_axes([.01, .025, .98, .96]); ax.imshow(image); ax.axis("off")
            page.text(.02, .012, chapter.upper(), color=MUTED, fontsize=6.8)
            page.text(.98, .012, f"{index:02d}", ha="right", color=MUTED, fontsize=6.8)
            pdf.savefig(page, facecolor="white", bbox_inches="tight", pad_inches=.015)
            plt.close(page)
    os.replace(temporary, output)


def _report() -> str:
    return """# C-DOT 5G digital-twin evidence story v1

This package joins the simulator, forecasting, survival, optimizer, MPC,
pre-drain, safety, and cluster-scale evidence into one causal narrative.

## Defensible conclusion

The digital-twin engineering is successful: traffic accounting closes, the
streaming execution is bounded, the evidence is reproducible, and the final
campaign completed 384 shards on 12 nodes with zero failures and zero swap.

Forecasting and lifecycle estimation are useful instruments, but their gains
do not automatically become robust control gains. Continuous oracle studies
show that action-space headroom exists. Deployable MPC and pre-drain candidates
did not reliably realize that headroom under mixed stress, tail, latency, and
overflow gates. Static therefore remains the production controller.

No protected validation or release seed was consumed, and no additional blend
interpolation was run to create this presentation. Historical evidence is
reported as measured; the deck does not rescore experiments.
"""


def _executive_summary() -> str:
    return """# Executive summary

| Component | What worked | Boundary / negative result |
|---|---|---|
| Synthetic twin | Causal cohort accounting, realistic stress, bounded streaming memory | Synthetic—not yet C-DOT production calibration |
| Forecasting | Useful target-separated gains and uncertainty diagnostics | No model cleared the frozen 15% promotion bar |
| Survival | Observable lifecycle fitting converged and survived hidden-family trials | Mechanically validated on simulated records only |
| Optimization | Oracle ladder proved causal/scheduled headroom exists | Oracle is not deployable control evidence |
| MPC | Guarded branch, same-state certification and fallback paths were exercised | Mean/tail benefit was unstable or slightly harmful |
| Pre-drain | Strong scheduled-fault replay benefit | Mixed stress, tails, latency, and overflow blocked promotion |
| Scale | 12 nodes, 384 shards, 90.9% CPU efficiency, zero failure/swap | Campaign throughput—not production control-plane latency |

Decision: **Static remains production. MPC and pre-drain remain guarded
shadow/replay experiments.**
"""


def _talk_track(slides: list[tuple[str, Path]]) -> str:
    notes = {
        "02": "Lead with the distinction: the platform passed; no advanced controller passed every gate.",
        "03": "Each stage reduces uncertainty. Early positive results are hypotheses, not release authority.",
        "04": "Explain causality: telemetry closes a bucket, forecasts and optimization use only observable state, and only future sessions can move.",
        "05": "The generator models service-by-hour demand, day types, autocorrelation, class mix and mobility conservation.",
        "06": "A representative synthetic UPF day combines normal load with bounded fault events and recovery.",
        "07": "Raw counter resets and missingness must be reconstructed before either forecasting or control is trustworthy.",
        "08": "Seven times longer duration grows peak RSS only 4.1%; the simulator is truly streaming.",
        "09": "The final scale ladder completed 384 shards on 12 nodes at 90.9% CPU efficiency, with zero failures and zero swap.",
        "10": "Forecast horizon and interval coverage are measured separately; point accuracy alone is insufficient.",
        "11": "LightGBM improves all separated targets, but the frozen 15% bar remains unmet.",
        "12": "Kaplan–Meier mechanics converge on censored synthetic lifecycle records; this validates mechanics, not field calibration.",
        "13": "The better 14-day forecast still failed the control gate. Prediction accuracy is an input—not the objective.",
        "14": "New-session-only control is fundamentally limited by notice time and session lifetime.",
        "15": "The oracle ladder shows headroom: faults and migration relaxation dominate what is controllable.",
        "16": "Static is the production spine. MPC is a guarded branch that loses authority on uncertainty or failed certification.",
        "17": "Removing timeouts proved the optimizer branch ran; it did not solve the performance gap.",
        "18": "Across the broad MPC sweep, intervals cross zero and scenario gains remain concentrated.",
        "19": "Distribution-blind lifecycle fitting works mechanically across five hidden families and fails closed when stale.",
        "20": "Pre-drain helps known scheduled faults, but that gain does not transfer safely to simultaneous surprise demand.",
        "21": "Blend interpolation exposes the trade-off: strong action buys mean benefit but unsafe tails; weak action loses the benefit.",
        "22": "MPC v2 fixed the warm-up interface and executed actions. The negative result is no longer an inert-branch artifact.",
        "23": "Adaptive blending traversed its range, yet surprise demand arrived after persistent-session commitments.",
        "24": "The audit correction is explicit: any overflow above 1e-7 records resource slack and returns to Static without certification.",
        "25": "These maxima are campaign-saturation measurements from 120 concurrent simulations—not isolated control-plane latency.",
        "26": "The inventory is exact: 516 candidate pairs plus 72 sensitivity comparisons equals 588, with protected seeds untouched.",
        "27": "Separate what worked in the scientific system from what did not work in candidate control algorithms.",
        "28": "Close on disciplined non-promotion: the evidence system knew when the algorithm was not ready.",
    }
    lines = ["# Talk track — C-DOT 5G digital-twin evidence story", "", "## 01 — Cover", "",
             "Frame this as an end-to-end engineering and scientific story, not a controller victory lap.", ""]
    for index, (chapter, path) in enumerate(slides, start=2):
        key = f"{index:02d}"
        lines.extend((f"## {key} — {path.stem}", "", notes[key], ""))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    if args.output_root.exists():
        raise FileExistsError(f"refusing to overwrite presentation package: {args.output_root}")
    required = (
        DELHI / "02_traffic_fingerprint.png",
        DELHI / "03_representative_upf_day.png",
        DELHI / "05_telemetry_pathology.png",
        DELHI / "06_forecast_horizon.png",
        DELHI / "07_forecast_vs_control.png",
        DELHI / "09_oracle_ladder.png",
        DELHI / "11_controllability_surface.png",
        PRODUCTION / "figures/01_streaming_memory_scaling.png",
        PRODUCTION / "figures/06_production_scaling.png",
        CONTROL / "figures/01_forecast_target_separated_wape.png",
        CONTROL / "figures/03_survival_calibration_convergence.png",
        CONTROL / "figures/07_all_mpc_candidates_forest.png",
        CONTROL / "figures/09_solver_status_and_static_fallback.png",
        CONTROL / "figures/11_guarded_mpc_production_architecture.png",
        CONTROL / "figures/13_distribution_blind_survival_matrix.png",
        CONTROL / "figures/15_new_scenario_improvement_heatmap.png",
        CONTROL / "figures/17_predrain_benefit_tail_frontier.png",
        CONTROL / "figures/18_mpc_action_funnel.png",
        CONTROL / "figures/19_end_to_end_latency_audit.png",
        CONTROL / "figures/20_adaptive_blend_causal_lag.png",
        CONTROL / "figures/21_campaign_scale_seed_firewall.png",
        CONTROL / "figures/22_worked_vs_did_not_work.png",
        PRODUCTION / "metrics.json", CONTROL / "artifact-manifest.json", V6_FREEZE, STORY_FREEZE,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"presentation evidence missing: {missing}")
    archive_paths = sorted(STORY_ARCHIVE.glob("source-*.tar.gz"))
    archive_manifests = sorted(STORY_ARCHIVE.glob("source-*.manifest.json"))
    if len(archive_paths) != 1 or len(archive_manifests) != 1:
        raise FileNotFoundError("story source freeze must have exactly one content-addressed archive")
    base._style()
    figures = args.output_root / "figures"
    figure_data: dict[str, Any] = {}
    outputs: list[Path] = []
    slides: list[tuple[str, Path]] = []

    def custom(chapter: str, builder: Any) -> None:
        generated, payload = builder(figures)
        outputs.extend(generated); figure_data[generated[0].stem] = payload
        slides.append((chapter, generated[0]))

    def copied(chapter: str, source: Path, stem: str) -> None:
        generated = _copy_figure(source, figures, stem)
        outputs.extend(generated)
        figure_data[stem] = {"source": str(source.relative_to(ROOT)), "sha256": _sha256(source)}
        slides.append((chapter, generated[0]))

    custom("Result", _executive_slide)
    custom("Journey", _journey_slide)
    custom("System", _architecture_slide)
    copied("Build the twin", DELHI / "02_traffic_fingerprint.png", "05_traffic_fingerprint")
    copied("Build the twin", DELHI / "03_representative_upf_day.png", "06_representative_upf_day")
    copied("Build the twin", DELHI / "05_telemetry_pathology.png", "07_telemetry_pathology")
    copied("Build the twin", PRODUCTION / "figures/01_streaming_memory_scaling.png", "08_streaming_memory_scaling")
    copied("Build the twin", PRODUCTION / "figures/06_production_scaling.png", "09_cluster_scale")
    copied("Teach it to predict", DELHI / "06_forecast_horizon.png", "10_forecast_horizon")
    copied("Teach it to predict", CONTROL / "figures/01_forecast_target_separated_wape.png", "11_forecast_targets")
    copied("Teach it to predict", CONTROL / "figures/03_survival_calibration_convergence.png", "12_survival_calibration")
    copied("Prediction is not control", DELHI / "07_forecast_vs_control.png", "13_forecast_vs_control")
    copied("Map the ceiling", DELHI / "11_controllability_surface.png", "14_controllability_surface")
    copied("Map the ceiling", DELHI / "09_oracle_ladder.png", "15_oracle_ladder")
    copied("Build the controller", CONTROL / "figures/11_guarded_mpc_production_architecture.png", "16_guarded_mpc_architecture")
    copied("Build the controller", CONTROL / "figures/09_solver_status_and_static_fallback.png", "17_solver_and_fallback")
    copied("Test the hypothesis", CONTROL / "figures/07_all_mpc_candidates_forest.png", "18_all_mpc_candidates")
    copied("Test the hypothesis", CONTROL / "figures/13_distribution_blind_survival_matrix.png", "19_distribution_blind_survival")
    copied("Test the hypothesis", CONTROL / "figures/15_new_scenario_improvement_heatmap.png", "20_scenario_transfer")
    copied("Test the hypothesis", CONTROL / "figures/17_predrain_benefit_tail_frontier.png", "21_predrain_frontier")
    copied("Mechanism audit", CONTROL / "figures/18_mpc_action_funnel.png", "22_mpc_action_funnel")
    copied("Mechanism audit", CONTROL / "figures/20_adaptive_blend_causal_lag.png", "23_adaptive_causal_lag")
    custom("Safety audit", _overflow_slide)
    copied("Operational audit", CONTROL / "figures/19_end_to_end_latency_audit.png", "25_campaign_saturation_latency")
    copied("Evidence discipline", CONTROL / "figures/21_campaign_scale_seed_firewall.png", "26_experiment_inventory")
    copied("Conclusion", CONTROL / "figures/22_worked_vs_did_not_work.png", "27_worked_vs_did_not")
    custom("Decision", _final_slide)

    args.output_root.mkdir(parents=True, exist_ok=True)
    base._atomic_json(args.output_root / "slide-data.json", figure_data)
    base._atomic_text(args.output_root / "REPORT.md", _report())
    base._atomic_text(args.output_root / "EXECUTIVE_SUMMARY.md", _executive_summary())
    base._atomic_text(args.output_root / "TALK_TRACK.md", _talk_track(slides))
    slide_index = [
        {"number": index, "chapter": chapter, "figure": str(path.relative_to(args.output_root))}
        for index, (chapter, path) in enumerate(slides, start=2)
    ]
    base._atomic_json(args.output_root / "slide-index.json", {"cover": 1, "slides": slide_index})
    pdf = args.output_root / "cdot_5g_digital_twin_story_v1.pdf"
    _build_pdf(pdf, slides)

    source_paths = [
        ROOT / "scripts/build_cdot_digital_twin_story_v1.py",
        ROOT / "scripts/build_phase3_cdot_showcase_v5.py",
        ROOT / "scripts/build_phase3_cdot_showcase_v6.py",
        ROOT / "presentation/delhi_evidence_manifest.json",
        ROOT / "presentation/delhi/build-report.json",
        PRODUCTION / "metrics.json", PRODUCTION / "README.md", PRODUCTION / "TALK_TRACK.md",
        CONTROL / "artifact-manifest.json", CONTROL / "REPORT.md",
        CONTROL / "EXECUTIVE_SUMMARY.md", CONTROL / "TALK_TRACK.md", V6_FREEZE,
        STORY_FREEZE, *archive_paths, *archive_manifests,
        *[path for path in required if path.suffix in {".png", ".json"}],
    ]
    generated_files = sorted(
        path for path in args.output_root.rglob("*")
        if path.is_file() and path.name != "artifact-manifest.json"
    )
    production = json.loads((PRODUCTION / "metrics.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "cdot-digital-twin-story/1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "slides": len(slides) + 1,
        "source_artifacts": {
            str(path.relative_to(ROOT)): _sha256(path) for path in sorted(set(source_paths))
        },
        "outputs": {
            str(path.relative_to(args.output_root)): _sha256(path) for path in generated_files
        },
        "evidence_inventory": {
            "declared_candidate_pairs": 516,
            "declared_candidate_configurations": 28,
            "survival_sensitivity_controller_pairs": 72,
            "controller_pairs_total": 588,
            "distribution_blind_survival_trials": 125,
        },
        "cluster_scale": production["production_summary"],
        "test_result": {"passed": 174, "failed": 0},
        "historical_results_rescored": False,
        "new_experiments_run": False,
        "protected_validation_seeds_consumed": False,
        "protected_release_seeds_consumed": False,
        "production_controller": "static-capacity-v1",
        "candidate_mode": "guarded_shadow_or_replay_only",
        "decision": "retain_static",
    }
    base._atomic_json(args.output_root / "artifact-manifest.json", manifest)
    print(json.dumps({
        "output": str(args.output_root.resolve()), "slides": len(slides) + 1,
        "figures": len([path for path in outputs if path.suffix == ".png"]),
        "pdf": str(pdf.resolve()), "artifacts": len(generated_files) + 1,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
