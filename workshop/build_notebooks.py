#!/usr/bin/env python3
"""Build the six-stage individual C-DOT digital-twin workshop notebook."""
from __future__ import annotations
import html, json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]; OUT = ROOT / "workshop"; FALLBACK = OUT / "fallback"
def md(value: str, ident: str, tags: list[str] | None = None): return {"cell_type":"markdown","id":ident,"metadata":{"tags":tags or []},"source":value.splitlines(True)}
def stream(value: str): return {"name":"stdout","output_type":"stream","text":value.splitlines(True)}
def code(value: str, ident: str, *, tags: list[str] | None=None, frozen=False, output=""):
    metadata={"tags":tags or []}
    if "solution" in (tags or []): metadata |= {"collapsed":True,"jupyter":{"source_hidden":True}}
    return {"cell_type":"code","execution_count":1 if frozen else None,"id":ident,"metadata":metadata,
            "outputs":[stream(output)] if frozen and output else [],"source":value.splitlines(True)}

SETUP="""from pathlib import Path
import json, os, sys
ROOT = next(path for path in (Path.cwd(), *Path.cwd().parents) if (path / 'pyproject.toml').is_file())
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from IPython.display import HTML, IFrame, Javascript, display
from workshop import runtime
from workshop.solver import teaching_problem, solve_teaching_lp, compare_lp_solvers
from workshop.replay import export_replay
from workshop.report import export_report
display(HTML('<style>.lab{border-left:6px solid #18a3a7;padding:16px;background:#edf8f8}.hint{border:1px solid #d8b55b;padding:10px}.jp-Notebook{max-width:1120px;margin:auto}</style><div class="lab"><b>SYNTHETIC · INDIVIDUAL WORKSPACE</b><h2>Optimize → parallel solve → simulate → analyze → experience</h2>No dashboard credentials. No policy-publication access. No established-session migration.</div>'))
"""

def stage(number: int, title: str, body: str, hint: str, solution: str, run: str, frozen: bool, output: str):
    return [md(f"## {number:02d} · {title}\n\n{body}",f"stage-{number}"), code(run,f"run-{number}",tags=["todo"],frozen=frozen,output=output),
            md(f"<div class='hint'><b>Two-minute hint</b> · {hint}</div>",f"hint-{number}"),
            code(solution,f"solution-{number}",tags=["solution"],frozen=False)]

def cells(frozen: bool):
    result=[md("# C-DOT 5G Digital Twin Workshop\n\n**90 minutes · individual JupyterHub lab**\n\nAll topology, traffic, telemetry, forecasts and outcomes are synthetic.","title"),
            code(SETUP,"setup",tags=["hide-input"],frozen=frozen,output="Workshop runtime loaded · participant scope only\n")]
    result += stage(1,"PREFLIGHT","Verify identity, PBS, private storage, solver commands/imports and WebGL2. A missing ParaSCIP capability is a blocking readiness failure, even though archived evidence remains viewable.",
      "`solver_readiness` must be `ready` for the live solver track. The browser check is independent of Python.",
      "checks = runtime.preflight(); display(checks)",
      "checks = runtime.preflight(); display(checks)\ndisplay(Javascript(\"document.body.dataset.webgl2 = !!document.createElement('canvas').getContext('webgl2'); console.log('WebGL2', document.body.dataset.webgl2)\"))",
      frozen,"solver_readiness: blocking-readiness-failure (frozen fallback); personal root writable: true\n")
    result += stage(2,"OPTIMIZE","Formulate continuous allocation, capacity, eligibility and overload-slack variables. Solve exactly the same LP with HiGHS and SCIP; weights are normalized allocations.",
      "Increase `demand_mbps` or reduce one residual capacity. HiGHS is for the tiny LP; SCIP equivalence is checked within tolerance.",
      "problem = teaching_problem(demand_mbps=260)\nhighs = solve_teaching_lp(problem, solver='highs'); display(highs.to_dict())\ntry:\n    comparison = compare_lp_solvers(problem); display(comparison)\nexcept RuntimeError as error:\n    print(error); print('LIVE SCIP TRACK BLOCKED — no solver substitution')",
      "problem = teaching_problem(demand_mbps=260)\nhighs = solve_teaching_lp(problem, solver='highs'); display(highs.to_dict())\ntry:\n    comparison = compare_lp_solvers(problem); display(comparison)\nexcept RuntimeError as error:\n    print(error); print('LIVE SCIP TRACK BLOCKED — no solver substitution')",
      frozen,"HiGHS objective=11540.000 · normalized weights sum=1.000000\nSCIP result supplied only for frozen fallback; live absence is never hidden\n")
    result += stage(3,"PARALLEL SOLVER","Load the frozen 24-UPF/96-group binary assignment/activation MIP. Submit one participant SCIP job. Observe—not submit—the presenter’s reserved two-node ParaSCIP job.",
      "ParaSCIP is inappropriate for the three-variable LP. Compare incumbent, dual bound, gap and fixed seed on the larger frozen MIP.",
      "scip_job = runtime.submit_pbs(ROOT/'pbs/workshop_solver.pbs', variables={'WORKSHOP_OUTPUT_ROOT':str(checks['personal_root']),'WORKSHOP_SEED':'20260822'}); display(scip_job)",
      "mip = json.loads((ROOT/'workshop/data/national_assignment_mip.json').read_text()); print(len(mip['upfs']), len(mip['groups']))\nscip_job = runtime.submit_pbs(ROOT/'pbs/workshop_solver.pbs', variables={'WORKSHOP_OUTPUT_ROOT':checks['personal_root'],'WORKSHOP_SEED':'20260822'}); display(scip_job)\nprint('Presenter ParaSCIP status:', os.environ.get('CDOT_PARASCIP_STATUS_JSON','archived fallback — presenter controls submission'))",
      frozen,"Frozen MIP loaded: 24 UPFs · 96 groups · participant SCIP job fallback selected\nPresenter ParaSCIP: archived fallback, clearly labeled\n")
    result += stage(4,"SIMULATE","Choose one bounded controller and deterministic seed, then submit a private five-simulated-minute shard as one one-node PBS job. The cluster provides scenario breadth; a simulation is not spread across 160 nodes.",
      "Use one of `static`, `reactive`, or `predictive`. Keep the seed integer and your output root private.",
      "controller='static'; seed=20260822\nsim_job=runtime.submit_pbs(ROOT/'pbs/workshop_simulator.pbs',variables={'WORKSHOP_OUTPUT_ROOT':checks['personal_root'],'WORKSHOP_SEED':str(seed),'WORKSHOP_CONTROLLER':controller}); display(sim_job)",
      "controller='static'; seed=20260822\nsim_job=runtime.submit_pbs(ROOT/'pbs/workshop_simulator.pbs',variables={'WORKSHOP_OUTPUT_ROOT':checks['personal_root'],'WORKSHOP_SEED':str(seed),'WORKSHOP_CONTROLLER':controller}); display(sim_job)",
      frozen,"PBS unavailable in frozen notebook · supplied deterministic run.parquet selected\n")
    result += stage(5,"ANALYZE","Load the actual Parquet contract and inspect offered/carried traffic, overload, loss, utilization, policy IDs, solver status and decision latency. Missing decision-trace fields remain explicitly unavailable.",
      "Offered demand is independent of carriage. Do not train demand forecasts on constrained carried traffic.",
      "run_path=runtime.supplied_result('workshop-run.parquet'); analysis=runtime.analyze_parquet(run_path); display(HTML(runtime.metric_svg(analysis))); display({k:v for k,v in analysis.items() if k!='series'})",
      "run_path=runtime.supplied_result('workshop-run.parquet')\nanalysis=runtime.analyze_parquet(run_path); display(HTML(runtime.metric_svg(analysis))); display({k:v for k,v in analysis.items() if k!='series'})",
      frozen,"10 steps · offered/carried/loss/utilization analyzed from canonical nested Parquet\nsolver status: recorded in decision trace · decision latency: unavailable in run.parquet\n")
    result += stage(6,"EXPERIENCE + REPORT","Export `twin-replay/1.0`, open the full Three.js replay, run the unsafe-policy drill, and produce `WorkshopReport.json` plus compact HTML.",
      "The replay URL is `/twin?replay=...`. Policy changes affect future-session particles; established particles remain anchored.",
      "replay_path=Path(checks['personal_root'])/'twin-replay.json'; export_replay(run_path,ROOT/'configs/workshop_short_scenario.json',replay_path,max_frames=120)\nadvisory='We would deploy this in advisory mode only after cluster solver, SMF/EMS hook, security, and matched-evidence gates pass.'\nreport=export_report(Path(checks['personal_root'])/'reports',participant_id=checks['user'],solver=highs.to_dict(),simulation=analysis,replay_path=str(replay_path),advisory_pilot_sentence=advisory); display(report)\nprefix=os.environ.get('JUPYTERHUB_SERVICE_PREFIX','/')\nreplay_url=f'{prefix}files/{replay_path.relative_to(ROOT).as_posix()}'\ntwin_url=os.environ.get('CDOT_TWIN_URL',f'{prefix}proxy/8010/twin')\ndisplay(IFrame(src=f'{twin_url}?replay={replay_url}',width='100%',height=680))",
      "replay_path=Path(checks['personal_root'])/'twin-replay.json'; replay=export_replay(run_path,ROOT/'configs/workshop_short_scenario.json',replay_path,max_frames=120)\nassert all(frame['causality']['existing_sessions_anchored'] for frame in replay['frames'])\nadvisory='We would deploy this in advisory mode only after cluster solver, SMF/EMS hook, security, and matched-evidence gates pass.'\nreport=export_report(Path(checks['personal_root'])/'reports',participant_id=checks['user'],solver=highs.to_dict(),simulation={k:v for k,v in analysis.items() if k!='series'},replay_path=str(replay_path),advisory_pilot_sentence=advisory); display(report)",
      frozen,"twin-replay/1.0 exported · established sessions anchored · WorkshopReport.json + HTML exported\n")
    result.append(md("## Evidence boundary\n\nThe guided 30-pair story reports **+10.52%** in its matched scope. Later national-scale control-science evidence did **not** promote MPC; **Static remains production-safe**. Neither result is live C-DOT evidence.\n\nComplete: **We would deploy this in advisory mode only after ___**.","close"))
    return result

def notebook(frozen=False): return {"cells":cells(frozen),"metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},"language_info":{"name":"python","version":"3.10"},"workshop":{"schema_version":"cdot-workshop-notebook/2.0","synthetic":True,"participant_has_presenter_credentials":False,"policy_publication_available":False,"visible_stages":["Preflight","Optimize","Parallel solver","Simulate","Analyze","Experience"]}},"nbformat":4,"nbformat_minor":5}
def fallback_html():
    return "<!doctype html><meta charset='utf-8'><title>C-DOT Workshop Frozen</title><style>body{font:17px system-ui;max-width:1000px;margin:auto;padding:3rem;color:#17343f}h2{color:#087f8c}.b{border-left:6px solid #8b55c4;padding:1rem;background:#f3eef9}</style><h1>C-DOT 5G Digital Twin Workshop · Frozen</h1><p class='b'>Synthetic fallback. No credentials, policy publication, live jobs, or live actuation.</p>"+''.join(f"<h2>{i:02d} · {name}</h2><p>Pre-executed canonical outputs remain available in the frozen notebook.</p>" for i,name in enumerate(notebook(True)['metadata']['workshop']['visible_stages'],1))+"<p class='b'>Guided +10.52% scope is separate from national non-promotion evidence. Static remains production-safe.</p>"
def main():
    OUT.mkdir(exist_ok=True); FALLBACK.mkdir(exist_ok=True)
    (OUT/'CDOT_UPF_Closed_Loop_Lab.ipynb').write_text(json.dumps(notebook(False),indent=1)+'\n')
    (OUT/'CDOT_UPF_Closed_Loop_Lab_Frozen.ipynb').write_text(json.dumps(notebook(True),indent=1)+'\n')
    (FALLBACK/'CDOT_UPF_Closed_Loop_Lab_Frozen.html').write_text(fallback_html())
    return 0
if __name__=='__main__': raise SystemExit(main())
