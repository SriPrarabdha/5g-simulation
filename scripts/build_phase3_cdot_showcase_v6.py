#!/usr/bin/env python3
"""Build the immutable post-audit C-DOT control-science showcase v6."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import scripts.build_phase3_cdot_showcase_v5 as v5
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import ListedColormap


ROOT = v5.ROOT
base = v5.base
plt = v5.plt
np = v5.np
NAVY, BLUE, CYAN = v5.NAVY, v5.BLUE, v5.CYAN
GOLD, CORAL, GREEN = v5.GOLD, v5.CORAL, v5.GREEN
PURPLE, MUTED = v5.PURPLE, v5.MUTED
LIGHT_GREEN, LIGHT_CORAL = v5.LIGHT_GREEN, v5.LIGHT_CORAL

FREEZE = ROOT / "output/control-science/v1/phase3-cdot-v6-interface-freeze.json"
ARCHIVE_ROOT = ROOT / "output/control-science/v1/source-archives/phase3-cdot-v6"
RUN_BREAKDOWN = (
    "516 paired runs across 28 declared candidate configurations, plus 72 "
    "survival-sensitivity controller comparisons: 588 controller pairs total."
)
GATE_LABELS = {
    **v5.GATE_LABELS,
    "unknown_mixed_regression_no_worse_than_minus_2_percent": (
        "Combined severity-weighted unknown + mixed ≥−2%"
    ),
    "zero_predicted_overflow": "Zero predicted overflow",
}


def _load_new_inputs() -> dict[str, Any]:
    data = v5._load_new_inputs()
    for row in data["rows"]:
        evaluation = row["evaluation"]
        diagnostics = [
            item for pair in evaluation["pairs"]
            for item in pair.get("decision_diagnostics", ())
        ]
        maximum = max((float(item.get("overflow", 0.0)) for item in diagnostics), default=0.0)
        tolerance = float(
            evaluation["candidate"].get("flow", {}).get("overflow_tolerance", 1e-7)
        )
        evaluation["development_gates"]["zero_predicted_overflow"] = maximum <= tolerance
        evaluation["operational"]["max_predicted_overflow"] = maximum
        evaluation["operational"]["predicted_overflow_tolerance"] = tolerance
    return data


def _gate_scorecard(data: dict[str, Any], figures: Path):
    rows = data["rows"]
    gates = list(GATE_LABELS)
    matrix = np.asarray([
        [1 if row["evaluation"]["development_gates"].get(gate) is True
         else 0 if row["evaluation"]["development_gates"].get(gate) is False
         else -1 for gate in gates]
        for row in rows
    ])
    fig, ax = plt.subplots(figsize=(13.333, 7.5))
    fig.subplots_adjust(left=.19, right=.98, top=.86, bottom=.34)
    v5._new_title(
        fig, "Post-audit promotion scorecard: no all-green row",
        "The added zero-overflow gate rejects every pre-drain family with predicted slack",
    )
    ax.imshow(
        matrix + 1, aspect="auto",
        cmap=ListedColormap(["#DDE5ED", LIGHT_CORAL, LIGHT_GREEN]), vmin=0, vmax=2,
    )
    ax.set_yticks(range(len(rows)), [row["label"] for row in rows], fontsize=8)
    ax.set_xticks(
        range(len(gates)), [GATE_LABELS[gate] for gate in gates],
        rotation=50, ha="right", fontsize=7.1,
    )
    symbols = {-1: "—", 0: "×", 1: "✓"}
    colors = {-1: MUTED, 0: CORAL, 1: GREEN}
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            value = int(matrix[i, j])
            ax.text(j, i, symbols[value], ha="center", va="center",
                    color=colors[value], fontsize=10, fontweight="bold")
    return base._save(fig, figures, "16_all_session_gate_scorecard"), {
        "candidates": [row["candidate_id"] for row in rows],
        "gates": gates,
        "pass_matrix": matrix.tolist(),
        "derived_post_audit_gate": "zero_predicted_overflow",
    }


def _latency_audit(data: dict[str, Any], figures: Path):
    mpc = [row for row in data["rows"] if row["campaign"] == "Phase 3.1 v2"
           and row["controller"] == "mpc"]
    phase32 = [row for row in data["rows"] if row["campaign"] == "Phase 3.2 v1"]
    fig, axes = plt.subplots(1, 2, figsize=(13.333, 7.5), gridspec_kw={"wspace": .35})
    v5._new_title(
        fig, "Campaign-saturation latency—not an isolated production benchmark",
        "120 concurrent simulations on a saturated 125-CPU node; useful stress evidence only",
    )
    left_values = [row["evaluation"]["operational"]["max_solver_runtime_ms"] for row in mpc]
    left_labels = [row["label"].replace("P3.1v2 · ", "") for row in mpc]
    axes[0].bar(range(len(mpc)), left_values, color=PURPLE)
    axes[0].axhline(2000, color=CORAL, ls="--", lw=2, label="Configured 2 s per-call limit")
    axes[0].set_xticks(range(len(mpc)), left_labels, rotation=18, ha="right", fontsize=8)
    axes[0].set_ylabel("Maximum diagnostic time (ms)")
    axes[0].set_title("P3.1 v2 MPC under campaign saturation", color=NAVY)
    axes[0].legend(fontsize=8)
    for i, value in enumerate(left_values):
        axes[0].text(i, value + 100, f"{value/1000:.2f}s", ha="center", fontsize=8,
                     fontweight="bold")
    right_values = [row["evaluation"]["operational"]["max_decision_runtime_ms"]
                    for row in phase32]
    right_labels = [row["label"].replace("P3.2 · ", "") for row in phase32]
    axes[1].bar(range(len(phase32)), right_values,
                color=[GREEN if value <= 500 else CORAL for value in right_values])
    axes[1].axhline(500, color=NAVY, ls="--", lw=2, label="Frozen 500 ms campaign gate")
    axes[1].set_xticks(range(len(phase32)), right_labels, rotation=20, ha="right", fontsize=8)
    axes[1].set_ylabel("Maximum end-to-end decision time (ms)")
    axes[1].set_title("P3.2 pre-drain under campaign saturation", color=NAVY)
    axes[1].legend(fontsize=8)
    for i, value in enumerate(right_values):
        axes[1].text(i, value + 20, f"{value:.0f}", ha="center", fontsize=8,
                     fontweight="bold")
    for ax in axes:
        ax.grid(axis="y")
    return base._save(fig, figures, "19_end_to_end_latency_audit"), {
        "measurement_context": "120 concurrent simulations on a saturated 125-CPU node",
        "production_control_plane_benchmark": False,
        "phase31_v2_mpc_max_ms": dict(zip(left_labels, left_values)),
        "phase32_end_to_end_max_ms": dict(zip(right_labels, right_values)),
    }


def _campaign_scale(data: dict[str, Any], figures: Path):
    names = [
        "Historical\ncandidates", "Survival-sensitivity\ncomparisons",
        "Phase 3.1\nv1", "Phase 3.1\nv2", "Phase 3.2\nv1",
    ]
    counts = [156, 72, 120, 120, 120]
    colors = [BLUE, GOLD, BLUE, PURPLE, CYAN]
    fig, axes = plt.subplots(1, 2, figsize=(13.333, 7.5),
                             gridspec_kw={"width_ratios": [1.55, 1]})
    v5._new_title(fig, "Exact controller-pair inventory and seed firewall",
                  "516 declared-candidate pairs + 72 survival-sensitivity comparisons = 588 total")
    bars = axes[0].bar(range(len(names)), counts, color=colors)
    axes[0].set_xticks(range(len(names)), names, fontsize=8.5)
    axes[0].set_ylabel("Controller-pair count")
    axes[0].grid(axis="y")
    for bar, value in zip(bars, counts):
        axes[0].text(bar.get_x() + bar.get_width()/2, value + 4, str(value),
                     ha="center", fontsize=9, color=NAVY, fontweight="bold")
    axes[0].set_ylim(0, 185)
    axes[0].set_title("No ambiguity: all 588 pairs classified", color=NAVY)
    seed_blocks = [
        ("Development", "46101–46112 · 46401–46472", BLUE, "consumed as registered"),
        ("Validation", "46201–46216", GREEN, "UNTOUCHED"),
        ("Release", "46301–46330", GREEN, "UNTOUCHED"),
        ("Forecast test", "46003", GOLD, "generated, never evaluated"),
    ]
    axes[1].axis("off")
    for index, (name, seeds, color, state) in enumerate(seed_blocks):
        y = .84 - index * .21
        patch = v5.FancyBboxPatch(
            (.04, y-.12), .92, .15, boxstyle="round,pad=.02,rounding_size=.025",
            facecolor="#F8FAFC", edgecolor=color, linewidth=2,
            transform=axes[1].transAxes,
        )
        axes[1].add_patch(patch)
        axes[1].text(.08, y-.005, name, transform=axes[1].transAxes, color=NAVY,
                     fontweight="bold", fontsize=11)
        axes[1].text(.08, y-.065, seeds, transform=axes[1].transAxes,
                     color=MUTED, fontsize=8.7)
        axes[1].text(.93, y-.005, state, transform=axes[1].transAxes, color=color,
                     ha="right", fontsize=9, fontweight="bold")
    axes[1].set_title("Seed firewall held", color=GREEN, fontweight="bold")
    return base._save(fig, figures, "21_campaign_scale_seed_firewall"), {
        "declared_candidate_pairs": 516,
        "declared_candidate_configurations": 28,
        "survival_sensitivity_controller_pairs": 72,
        "controller_pairs_total": 588,
        "campaign_counts": dict(zip(names, counts)),
        "survival_trials": 125,
        "protected_consumed": False,
    }


def _worked_not_worked(data: dict[str, Any], figures: Path):
    worked = [
        ("Reproducibility", "Historical freezes retained; v6 corrections separately hashed and archived"),
        ("Lifecycle estimator", "Observable export + Kaplan–Meier calibrated across 5 hidden distributions"),
        ("Overflow safety", "Nonzero predicted slack now records by resource and fails closed to Static"),
        ("Oracle fixture", "Authoritative completed-job evaluation restored; full suite is green"),
        ("Scheduled headroom", "Pre-drain repeatedly reduced scheduled-fault overload in replay"),
        ("Release discipline", "516 candidate pairs + 72 sensitivity pairs; zero unsafe promotions"),
    ]
    failed = [
        ("Forecast promotion", "Useful gains remained below the frozen 15% target"),
        ("MPC benefit", "Exercised variants were neutral/slightly harmful"),
        ("Generalization", "Scheduled benefit did not transfer safely to mixed stress"),
        ("Tail safety", "Strong blends missed the −10% worst-pair gate"),
        ("Historical certification", "Optimal-with-overflow actions are reclassified as non-certified"),
        ("Latency claim", "225–970 ms reflects saturated campaign execution, not production isolation"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(13.333, 7.5), gridspec_kw={"wspace": .08})
    v5._new_title(fig, "What worked—and what did not", "Post-audit corrections change labels, not outcomes")
    for ax, title, rows, color, symbol in (
        (axes[0], "WORKED", worked, GREEN, "✓"),
        (axes[1], "DID NOT WORK", failed, CORAL, "×"),
    ):
        ax.axis("off")
        ax.text(.03, .94, title, transform=ax.transAxes, color=color,
                fontsize=20, fontweight="bold")
        for index, (heading, detail) in enumerate(rows):
            y = .83 - index * .125
            patch = v5.FancyBboxPatch(
                (.02, y-.075), .96, .095, boxstyle="round,pad=.012,rounding_size=.02",
                facecolor=LIGHT_GREEN if color == GREEN else LIGHT_CORAL,
                edgecolor="none", transform=ax.transAxes,
            )
            ax.add_patch(patch)
            ax.text(.05, y-.028, symbol, transform=ax.transAxes, color=color,
                    fontsize=18, fontweight="bold", va="center")
            ax.text(.12, y, heading, transform=ax.transAxes, color=NAVY,
                    fontsize=10.5, fontweight="bold", va="top")
            ax.text(.12, y-.037, detail, transform=ax.transAxes, color=MUTED,
                    fontsize=8.2, va="top", wrap=True)
    fig.text(.50, .055, "FINAL DECISION  ·  RETAIN STATIC  ·  SHADOW/REPLAY ONLY FOR MPC/PRE-DRAIN",
             ha="center", color=NAVY, fontsize=12.5, fontweight="bold")
    return base._save(fig, figures, "22_worked_vs_did_not_work"), {
        "worked": worked, "did_not_work": failed, "decision": "retain_static",
    }


def _report() -> str:
    return f"""# C-DOT control-science showcase v6

This immutable post-audit package preserves the v5 evidence and corrects its
control-safety and presentation labels. Static remains the deployed controller;
MPC and pre-drain remain guarded shadow/replay experiments only.

## Corrections closed

- Pre-drain now rejects to Static whenever predicted overflow exceeds `1e-7`.
- UL, DL and session overflow is published in `ConstraintSlack`; an
  optimal-with-overflow result is proposed but never certified, accepted or executed.
- `zero_predicted_overflow` is a conjunctive promotion gate.
- The authoritative seed-36001/36002 oracle evaluation was restored from the
  completed PBS job; the full repository suite passes 174/174 tests.
- {RUN_BREAKDOWN}
- The combined gate is labelled “combined severity-weighted unknown + mixed.”
- The 225–970 ms pre-drain maxima are labelled campaign-saturation latency from
  120 concurrent simulations on a saturated 125-CPU node—not an isolated
  production control-plane benchmark.

Historical evaluations are not rescored and their negative decisions do not
change. Their recorded overflow means the affected pre-drain actions should not
be described as flow-feasible or certified. Validation seeds 46201–46216,
release seeds 46301–46330 and sealed forecast seed 46003 remain unconsumed.
"""


def _talk_track() -> str:
    return v5._talk_track().replace(
        "# Phase 3.1/3.2 extension", "# Phase 3.1/3.2 extension — v6 post-audit wording"
    ).replace(
        "No candidate is all green. Historical rows show gray for the end-to-end deadline\n"
        "that was registered only in Phase 3.2. Gates were not weakened after near misses.",
        "No candidate is all green. The post-audit zero-overflow gate is derived from recorded\n"
        "diagnostics and rejects all pre-drain families that used slack. The combined stress\n"
        "gate is severity-weighted across unknown outage plus mixed stress; it does not hide\n"
        "the separately reported mixed-stress regression.",
    ).replace(
        "end-to-end 500 ms gate; only one of five candidates meets it under saturation.",
        "end-to-end 500 ms campaign gate; only one of five candidates meets it while 120\n"
        "simulations share a saturated 125-CPU node. This is not isolated production latency.",
    ).replace(
        "The complete story covers 588 paired controller runs plus 125 survival trials.",
        RUN_BREAKDOWN + " There are also 125 distribution-blind survival trials.",
    ) + """

## v6 safety correction

The flow solver intentionally uses overflow variables to keep the linear program
diagnostic. v6 makes the controller boundary fail closed: any predicted overflow
above `1e-7` returns Static, records resource-specific `ConstraintSlack`, and
does not increment certified or accepted. This correction changes the truth of
the historical certification label, not the already-negative candidate outcome.
"""


def _executive_summary() -> str:
    return f"""# Executive summary — post-audit v6

| Area | Corrected conclusion |
|---|---|
| Production | Retain Static; MPC and pre-drain are shadow/replay only |
| Overflow | Nonzero predicted slack now fails closed to Static and is never certified |
| Evidence inventory | {RUN_BREAKDOWN} |
| Stress gate | Combined severity-weighted unknown + mixed; mixed stress remains separately visible |
| Latency | 225–970 ms is saturated-campaign latency, not isolated production latency |
| Repository | Authoritative oracle artifact restored; 174/174 tests pass |
| Seed firewall | Validation 46201–46216, release 46301–46330 and forecast 46003 untouched |

Decision: **retain Static**. Do not advance an MPC/pre-drain candidate into
protected validation or release.
"""


def _build_pdf(output: Path, figures: list[Path]) -> None:
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    with PdfPages(temporary) as pdf:
        cover = plt.figure(figsize=(13.333, 7.5), facecolor=NAVY)
        cover.text(.07, .80, "C-DOT control science", color="white", fontsize=28,
                   fontweight="bold")
        cover.text(.07, .65, "Phase 2.1 → Phase 3.2 · v6", color="#8FD3FF",
                   fontsize=36, fontweight="bold")
        cover.text(.07, .53, "Post-audit safety and evidence corrections", color="white",
                   fontsize=19)
        cover.text(.07, .32, "Exact evidence scale", color=GOLD, fontsize=13,
                   fontweight="bold")
        cover.text(.07, .23, "516 candidate pairs  +  72 sensitivity pairs  =  588 total",
                   color="white", fontsize=18, fontweight="bold")
        cover.text(.07, .09, "Outcome: Static production · candidates guarded shadow/replay only",
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
        final.text(.07, .48, "MPC and pre-drain: guarded shadow/replay only.\n"
                   "Predicted overflow: fail closed, report slack, never certify.",
                   color=NAVY, fontsize=17, linespacing=1.5)
        final.text(.07, .28, "No protected validation or release seed was consumed.\n"
                   "No post-hoc blend interpolation was run.", color=CORAL,
                   fontsize=17, linespacing=1.5)
        final.text(.07, .10, "Next: C-DOT demo integration · Static control · 12-node/384-shard rehearsal",
                   color=NAVY, fontsize=13, fontweight="bold")
        plt.axis("off"); pdf.savefig(final, facecolor="white"); plt.close(final)
    os.replace(temporary, output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    if args.output_root.exists():
        raise FileExistsError(f"refusing to overwrite presentation package: {args.output_root}")
    archive_paths = sorted(ARCHIVE_ROOT.glob("source-*.tar.gz"))
    archive_manifests = sorted(ARCHIVE_ROOT.glob("source-*.manifest.json"))
    if not FREEZE.is_file() or len(archive_paths) != 1 or len(archive_manifests) != 1:
        raise FileNotFoundError("v6 interface freeze and single source archive must exist first")
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
    new_builders = (
        v5._distribution_blind_survival, v5._development_forest,
        v5._new_scenario_heatmap, _gate_scorecard, v5._predrain_frontier,
        v5._mpc_funnel, _latency_audit, v5._adaptive_mechanism,
        _campaign_scale, _worked_not_worked,
    )
    outputs: list[Path] = []
    figure_data: dict[str, Any] = {}
    for builder in (*old_builders, *new_builders):
        generated, payload = builder(
            historical if builder in old_builders else current, figures_root
        )
        outputs.extend(generated)
        figure_data[generated[0].stem] = payload
    args.output_root.mkdir(parents=True, exist_ok=True)
    base._atomic_json(args.output_root / "figure-data.json", figure_data)
    base._atomic_text(args.output_root / "REPORT.md", _report())
    base._atomic_text(args.output_root / "TALK_TRACK.md", _talk_track())
    base._atomic_text(args.output_root / "EXECUTIVE_SUMMARY.md", _executive_summary())
    pngs = sorted(path for path in outputs if path.suffix == ".png")
    pdf = args.output_root / "cdot_control_science_showcase_v6.pdf"
    _build_pdf(pdf, pngs)
    correction_sources = [
        ROOT / relative for relative in (
            "experiments/freeze_phase3_cdot_v6.py", "optimization/predrain_flow.py",
            "scripts/build_phase3_cdot_showcase.py", "scripts/build_phase3_cdot_showcase_v5.py",
            "scripts/build_phase3_cdot_showcase_v6.py", "scripts/run_phase31_candidate_matrix.py",
            "simulator/macro/controllers.py", "tests/test_control_science.py",
            "tests/test_simulator.py", "output/models/extreme-oracle-bound-evaluation-v1.json",
        )
    ]
    source_paths = (
        list(historical["paths"].values()) + list(historical["evaluation_paths"].values())
        + list(historical["survival_paths"].values())
        + list(historical["survival_bundle_paths"].values())
        + current["source_paths"] + correction_sources
        + [FREEZE, *archive_paths, *archive_manifests]
    )
    generated_files = sorted(
        path for path in args.output_root.rglob("*")
        if path.is_file() and path.name != "artifact-manifest.json"
    )
    manifest = {
        "schema_version": "phase3-cdot-showcase/1.3",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_artifacts": {
            str(path.relative_to(ROOT)): v5._sha256(path) for path in sorted(set(source_paths))
        },
        "outputs": {
            str(path.relative_to(args.output_root)): v5._sha256(path)
            for path in generated_files
        },
        "figures": len(pngs),
        "test_result": {"passed": 174, "failed": 0},
        "complete_experiment_inventory": {
            "declared_candidate_pairs": 516,
            "declared_candidate_configurations": 28,
            "survival_sensitivity_controller_pairs": 72,
            "controller_pairs_total": 588,
            "distribution_blind_survival_trials": 125,
        },
        "overflow_correction": {
            "tolerance": 1e-7,
            "fail_closed_controller": "static-capacity-v1",
            "promotion_gate": "zero_predicted_overflow",
            "historical_results_rescored": False,
        },
        "protected_seed_state": {
            "forecast_46003_status": "generated_and_sealed_but_never_evaluated_or_selected",
            "validation_46201_46216_consumed": False,
            "release_46301_46330_consumed": False,
        },
        "production_controller": "static-capacity-v1",
        "candidate_mode": "guarded_shadow_or_replay_only",
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
