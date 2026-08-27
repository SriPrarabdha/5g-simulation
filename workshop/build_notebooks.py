#!/usr/bin/env python3
"""Build the C-DOT office lab: a live PBS track and a run-anywhere track."""
from __future__ import annotations

import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workshop"
FALLBACK = OUT / "fallback"

STAGES = [
    "Mission control",
    "Make the bottleneck visible",
    "Forecast without peeking",
    "Prove the safety gate",
    "Scale out on PBS",
    "Make the operator call",
]


def md(value: str, ident: str, tags: list[str] | None = None) -> dict:
    return {
        "cell_type": "markdown",
        "id": ident,
        "metadata": {"tags": tags or []},
        "source": value.splitlines(True),
    }


def stream(value: str) -> dict:
    return {"name": "stdout", "output_type": "stream", "text": value.splitlines(True)}


def code(
    value: str,
    ident: str,
    *,
    tags: list[str] | None = None,
    frozen: bool = False,
    output: str = "",
) -> dict:
    metadata = {"tags": tags or []}
    if "solution" in (tags or []):
        metadata |= {"collapsed": True, "jupyter": {"source_hidden": True}}
    return {
        "cell_type": "code",
        "execution_count": 1 if frozen else None,
        "id": ident,
        "metadata": metadata,
        "outputs": [stream(output)] if frozen and output else [],
        "source": value.splitlines(True),
    }


SETUP = r"""from pathlib import Path
import json, os, sys
ROOT = next(path for path in (Path.cwd(), *Path.cwd().parents) if (path / 'pyproject.toml').is_file())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from IPython.display import HTML, display
from workshop import runtime
from workshop.lab import (
    build_decision, causal_ma_forecast, certify_recommendation, close_loop,
    create_traffic_event, save_decision, simulate_event, traffic_plot,
)
from workshop.solver import teaching_problem, solve_teaching_lp

display(HTML('''
<style>
.jp-Notebook{max-width:1120px;margin:auto}.hero{padding:22px 25px;border-radius:10px;
background:linear-gradient(125deg,#062d38,#0f5962);color:white;box-shadow:0 8px 30px #07313b22}
.hero b,.pulse{color:#58e0d3}.checkpoint{border-left:6px solid #ef9d32;padding:12px 16px;
background:#fff6e8}.truth{border:1px solid #b9d9d7;padding:12px 16px;background:#effafa}
.choice{border:1px solid #d7dde2;border-radius:8px;padding:12px}.small{color:#63727a;font-size:92%}
</style>
<div class="hero"><div class="pulse"><b>MISSION · KEEP THE USER PLANE INSIDE ITS SAFE ENVELOPE</b></div>
<h2>See the event. Predict the margin. Challenge the policy. Prove the fallback.</h2>
<div>Synthetic shadow mode · future-session placement only · no live actuation</div></div>'''))
"""


def stage(
    number: int,
    title: str,
    body: str,
    run: str,
    solution: str,
    checkpoint: str,
    *,
    frozen: bool,
    output: str,
) -> list[dict]:
    return [
        md(f"## {number:02d} · {title}\n\n{body}", f"stage-{number}"),
        code(run, f"run-{number}", tags=["todo"], frozen=frozen, output=output),
        md(
            f"<div class='checkpoint'><b>Talk it through</b> · {checkpoint}</div>",
            f"checkpoint-{number}",
        ),
        code(solution, f"solution-{number}", tags=["solution"]),
    ]


def cells(frozen: bool) -> list[dict]:
    track = "RUN ANYWHERE · NO CLUSTER REQUIRED" if frozen else "LIVE PBS + LOCAL FALLBACK"
    duration = "20-minute office companion" if frozen else "35-minute evidence lab"
    result = [
        md(
            "# C-DOT UPF Steering · From Claim to Decision\n\n"
            f"**{duration} · {track}**\n\n"
            "You are not here to admire a model. You are here to decide whether a proposed "
            "routing policy deserves to reach an SMF—even in advisory mode. All traffic, topology, "
            "capacity, failures and outcomes in this notebook are synthetic.",
            "title",
        ),
        md(
            "<div class='truth'><b>The control contract</b><br>We may steer only <b>new sessions</b>. "
            "Established sessions remain anchored. A forecast is useful only if it was made from "
            "closed history, and an optimizer is useful only if an independent validator can reject it.</div>",
            "contract",
        ),
        code(
            SETUP,
            "setup",
            tags=["hide-input"],
            frozen=frozen,
            output=f"C-DOT office lab ready · {track.lower()}\n",
        ),
    ]

    result += stage(
        1,
        "MISSION CONTROL",
        "Pick one traffic group and decide how hard the synthetic event should hit. This is the "
        "audience's first vote: conservative **2.5×**, match-day **4×**, or extreme **7×**.",
        "group_id = 'stadium|social-live|1-010204'\n"
        "surge_multiplier = 4.0\n"
        "event = create_traffic_event(group_id, surge_multiplier)\n"
        "checks = runtime.preflight()\n"
        "display({'event': event.to_dict(), 'execution': runtime.execution_summary(checks)})",
        "event = create_traffic_event('stadium|social-live|1-010204', 4.0)\n"
        "checks = runtime.preflight()\n"
        "display({'event': event.to_dict(), 'execution': runtime.execution_summary(checks)})",
        "Ask: what is the one fact that would make this scenario believable for a real C-DOT deployment? "
        "Capture it—we turn that answer into a pilot gate at the end.",
        frozen=frozen,
        output="Scenario armed: Stadium · social-live · 4.0x synthetic surge\nExecution: local path ready; PBS optional\n",
    )

    result += stage(
        2,
        "MAKE THE BOTTLENECK VISIBLE",
        "Run a deterministic 20-window teaching trace. Violet is what users ask for; teal is what "
        "the network carries. The gap is not 'bad forecast'—it is traffic the current envelope cannot carry.",
        "rows = simulate_event(event)\n"
        "display(HTML(traffic_plot(rows)))\n"
        "peak = max(rows, key=lambda row: row['offered_ul_mbps'])\n"
        "display({'peak_offered_ul_mbps': peak['offered_ul_mbps'], "
        "'peak_loss_ul_mbps': peak['loss_ul_mbps'], 'event_window': peak['window']})",
        "rows = simulate_event(event)\n"
        "display(HTML(traffic_plot(rows)))\n"
        "peak = max(rows, key=lambda row: row['offered_ul_mbps'])\n"
        "display(peak)",
        "Point to the first place violet separates from teal. Offered demand must stay independent of "
        "carried traffic, or the model learns the bottleneck instead of the demand.",
        frozen=frozen,
        output="20 closed windows generated · offered demand separated from carried traffic · loss becomes visible during surge\n",
    )

    result += stage(
        3,
        "FORECAST WITHOUT PEEKING",
        "The forecast sees exactly six closed windows and targets the next one. Run the causality "
        "assertion before looking at the values; this is the notebook's anti-cheating checkpoint.",
        "planning_risk = 'p90'\n"
        "forecast = causal_ma_forecast(rows, event, planning_risk=planning_risk)\n"
        "assert forecast.source_window_end <= forecast.target_window.start\n"
        "display({'model': forecast.model_version, 'source_window_end': forecast.source_window_end, "
        "'target_start': forecast.target_window.start, 'p50_ul_mbps': forecast.new_load_ul_mbps.p50, "
        "'p90_ul_mbps': forecast.new_load_ul_mbps.p90})",
        "forecast = causal_ma_forecast(rows, event, planning_risk='p90')\n"
        "assert forecast.source_window_end <= forecast.target_window.start\n"
        "forecast.validate(); display(forecast.to_dict())",
        "Ask the room to predict whether p50 or p90 should drive an advisory policy. The right answer "
        "depends on the cost of overload versus unnecessary routing churn—not on model accuracy alone.",
        frozen=frozen,
        output="CAUSALITY PASS · six closed windows only · p90 exceeds p50 · forecast/1.0 valid\n",
    )

    result += stage(
        4,
        "PROVE THE SAFETY GATE",
        "First certify a cohort-MPC recommendation. Then attack it with a deliberately invalid policy. "
        "The dramatic moment is not that the smart policy passes; it is that the unsafe one cannot escape.",
        "controller = 'cohort-mpc'\n"
        "certification = certify_recommendation(forecast, event, controller=controller, planning_risk=planning_risk)\n"
        "attack = certify_recommendation(forecast, event, controller=controller, planning_risk=planning_risk, "
        "weights={'upf-a': 0.55, 'upf-z': 0.55})\n"
        "display({'candidate': certification.to_dict(), 'red_team_attack': attack.to_dict()})\n"
        "assert certification.accepted and attack.fallback_used and attack.existing_sessions_anchored",
        "certification = certify_recommendation(forecast, event, controller='cohort-mpc', planning_risk='p90')\n"
        "attack = certify_recommendation(forecast, event, controller='cohort-mpc', planning_risk='p90', "
        "migrate_existing=True)\n"
        "assert certification.accepted and attack.fallback_used\n"
        "display(certification.to_dict()); display(attack.to_dict())",
        "Invite someone to change the attack: make weights sum above one, name an ineligible UPF, or "
        "request established-session migration. Every path must retain the last safe static policy.",
        frozen=frozen,
        output="SAFE TO RECOMMEND · candidate passed\nRED-TEAM REJECTED · last-safe static retained · established sessions anchored\n",
    )

    if frozen:
        cluster_body = (
            "This notebook does not pretend a laptop is a cluster. Inspect the exact bounded PBS jobs, "
            "then run the same tiny LP locally with HiGHS. The local result teaches the formulation; the "
            "cluster supplies breadth and independent shards."
        )
        cluster_run = (
            "for name in ('workshop_solver.pbs', 'workshop_simulator.pbs'):\n"
            "    print(f'--- {name} ---')\n"
            "    print((ROOT/'pbs'/name).read_text().split('set -euo pipefail')[0].strip())\n"
            "problem = teaching_problem(demand_mbps=260)\n"
            "local_solution = solve_teaching_lp(problem, solver='highs')\n"
            "display(local_solution.to_dict())"
        )
        cluster_solution = cluster_run
        cluster_output = (
            "PBS design inspected: solver 1 CPU / 4 GB / 5 min; simulator 1 CPU / 6 GB / 10 min\n"
            "Local HiGHS teaching LP: optimal · routing weights normalized to 1.0\n"
        )
    else:
        cluster_body = (
            "Submit two real, bounded jobs: a 24-UPF/96-group SCIP assignment MIP and one deterministic "
            "simulation shard. If PBS is unavailable, submission returns a labelled fallback; it never "
            "passes a local solve off as cluster evidence."
        )
        cluster_run = (
            "pbs_root = str(Path(checks['personal_root']).parent)\n"
            "solver_job = runtime.submit_pbs(ROOT/'pbs/workshop_solver.pbs', variables={\n"
            "    'WORKSHOP_OUTPUT_ROOT': pbs_root, 'WORKSHOP_SEED': '20260822'})\n"
            "sim_job = runtime.submit_pbs(ROOT/'pbs/workshop_simulator.pbs', variables={\n"
            "    'WORKSHOP_OUTPUT_ROOT': pbs_root, 'WORKSHOP_SEED': '20260822', "
            "'WORKSHOP_CONTROLLER': 'predictive'})\n"
            "display({'assignment_mip': solver_job, 'simulation_shard': sim_job})\n"
            "for job in (solver_job, sim_job):\n"
            "    if job.get('job_id'): display(runtime.pbs_status(job['job_id']))"
        )
        cluster_solution = cluster_run
        cluster_output = ""

    result += stage(
        5,
        "SCALE OUT ON PBS",
        cluster_body,
        cluster_run,
        cluster_solution,
        "One simulation is not spread across 160 nodes. The campaign scales by running matched, "
        "independent scenario/seed pairs. This distinction is worth saying aloud.",
        frozen=frozen,
        output=cluster_output,
    )

    result += stage(
        6,
        "MAKE THE OPERATOR CALL",
        "Compare the accepted policy with static on the same synthetic window, then create a pilot-readiness "
        "card. The card is intentionally incomplete until C-DOT supplies the four facts that simulation cannot.",
        "outcome = close_loop(rows, event, certification)\n"
        "decision = build_decision(event, certification, outcome, controller=controller, "
        "planning_risk=planning_risk, explanation='Use uncertainty, validate independently, and preserve anchored sessions.')\n"
        "decision_path = save_decision(decision, Path(checks['personal_root'])/'office-evidence')\n"
        "pilot_readiness = {\n"
        "  'schema_version': 'cdot-pilot-readiness/1.0', 'synthetic_evidence_only': True,\n"
        "  'smf_future_session_steering_key_confirmed': None,\n"
        "  'declared_maintenance_notice_minutes': None,\n"
        "  'upf_safe_capacity_and_session_envelopes_available': None,\n"
        "  'telemetry_freshness_and_counter_semantics_confirmed': None,\n"
        "  'recommended_mode': 'shadow advisory', 'live_actuation_authorized': False}\n"
        "readiness_path = Path(checks['personal_root'])/'office-evidence'/'CDOT_Pilot_Readiness.json'\n"
        "readiness_path.write_text(json.dumps(pilot_readiness, indent=2)+'\\n')\n"
        "display({'matched_window': outcome, 'decision': str(decision_path), 'pilot_readiness': str(readiness_path)})",
        "outcome = close_loop(rows, event, certification)\n"
        "decision = build_decision(event, certification, outcome, controller='cohort-mpc', planning_risk='p90', "
        "explanation='Shadow first; publish only after the four operator gates are answered.')\n"
        "decision_path = save_decision(decision, Path(checks['personal_root'])/'office-evidence')\n"
        "display(outcome); print(decision_path)",
        "Do not end on the percentage. End on the four blank operator fields. Those are the bridge from a "
        "synthetic evidence system to a C-DOT shadow pilot.",
        frozen=frozen,
        output="Matched synthetic window scored · WorkshopDecision.json exported\nPilot readiness: 4 operator facts still required · recommended mode: shadow advisory\n",
    )

    result.append(
        md(
            "## The sentence to leave on screen\n\n"
            "<div class='hero'><h2>The controller earns the right to advise—not the right to actuate.</h2>"
            "<p><b>Latest v4 boundary:</b> predictive steering cleared the declared-maintenance simulation "
            "gates and tied static bit-for-bit on pure surprises. Static remains the default outside declared "
            "events. The +24.0% held-out result is synthetic; it is not live C-DOT evidence.</p>"
            "<p>Next move: fill the four operator fields, replay real telemetry in shadow mode, and let the "
            "independent gate decide whether any recommendation is publishable.</p></div>",
            "close",
        )
    )
    return result


def notebook(frozen: bool = False) -> dict:
    return {
        "cells": cells(frozen),
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.10"},
            "workshop": {
                "schema_version": "cdot-office-lab/3.0",
                "synthetic": True,
                "track": "run-anywhere" if frozen else "live-pbs-with-local-fallback",
                "participant_has_presenter_credentials": False,
                "policy_publication_available": False,
                "visible_stages": STAGES,
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def fallback_html() -> str:
    stages = "".join(
        f"<section><b>{i:02d}</b><h2>{html.escape(name)}</h2><p>Canonical synthetic checkpoint available.</p></section>"
        for i, name in enumerate(STAGES, 1)
    )
    return (
        "<!doctype html><meta charset='utf-8'><title>C-DOT UPF Steering · Run Anywhere</title>"
        "<style>body{font:17px system-ui;max-width:1000px;margin:auto;padding:3rem;color:#17343f}"
        "header{padding:2rem;background:#073642;color:white;border-radius:12px}section{border-left:5px solid #0f8b8d;"
        "padding:.4rem 1.2rem;margin:1.3rem 0}b{color:#e9992e}.truth{background:#edf8f8;padding:1.2rem}</style>"
        "<header><h1>C-DOT UPF Steering · From Claim to Decision</h1><p>Run-anywhere synthetic fallback. "
        "No credentials, cluster claims, policy publication, or live actuation.</p></header>"
        + stages
        + "<p class='truth'><b>Evidence boundary:</b> v4 predictive steering passes for declared maintenance; "
        "pure surprises retain static bit-for-bit. The +24.0% result remains synthetic.</p>"
    )


def main() -> int:
    OUT.mkdir(exist_ok=True)
    FALLBACK.mkdir(exist_ok=True)
    (OUT / "CDOT_UPF_Closed_Loop_Lab.ipynb").write_text(
        json.dumps(notebook(False), indent=1) + "\n", encoding="utf-8"
    )
    (OUT / "CDOT_UPF_Closed_Loop_Lab_Frozen.ipynb").write_text(
        json.dumps(notebook(True), indent=1) + "\n", encoding="utf-8"
    )
    (FALLBACK / "CDOT_UPF_Closed_Loop_Lab_Frozen.html").write_text(
        fallback_html(), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
