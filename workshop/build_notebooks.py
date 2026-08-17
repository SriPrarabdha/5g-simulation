#!/usr/bin/env python3
"""Build the participant, frozen, and browser-only workshop notebooks.

The builder intentionally uses only the standard library so the offline bundle
can be regenerated before Jupyter is installed.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from workshop import lab


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workshop"
FALLBACK = OUT / "fallback"


def markdown(source: str, cell_id: str) -> dict[str, Any]:
    return {
        "cell_type": "markdown",
        "id": cell_id,
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


def code(
    source: str,
    cell_id: str,
    *,
    tags: list[str] | None = None,
    collapsed: bool = False,
    outputs: list[dict[str, Any]] | None = None,
    execution_count: int | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if tags:
        metadata["tags"] = tags
    if collapsed:
        metadata["collapsed"] = True
        metadata["jupyter"] = {"source_hidden": True}
    return {
        "cell_type": "code",
        "execution_count": execution_count,
        "id": cell_id,
        "metadata": metadata,
        "outputs": outputs or [],
        "source": source.splitlines(keepends=True),
    }


def stream(value: str) -> dict[str, Any]:
    return {"name": "stdout", "output_type": "stream", "text": value.splitlines(keepends=True)}


def display_html(value: str) -> dict[str, Any]:
    return {"data": {"text/html": value.splitlines(keepends=True)}, "metadata": {}, "output_type": "display_data"}


SETUP = """from pathlib import Path
import json, sys

ROOT = next(path for path in (Path.cwd(), *Path.cwd().parents) if (path / "pyproject.toml").is_file())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from IPython.display import HTML, display
from workshop import lab

display(HTML('''<style>
:root { --lab-ink:#17343f; --lab-teal:#087f8c; --lab-violet:#753bbd; --lab-amber:#b77b10; }
.jp-Notebook { max-width: 1120px; margin:auto; }
.lab-banner { border-left:6px solid var(--lab-teal); padding:18px 22px; background:#eef7f7; color:var(--lab-ink); }
.lab-stage { font:600 12px/1.2 "IBM Plex Mono",monospace; letter-spacing:.12em; color:var(--lab-teal); }
.lab-check { padding:12px 15px; border:1px solid #c8d6da; background:#f7fafb; color:var(--lab-ink); }
.lab-safe { border-left:5px solid #14835b; } .lab-warn { border-left:5px solid #d2931d; }
</style><div class="lab-banner"><div class="lab-stage">SYNTHETIC · DETERMINISTIC · PARTICIPANT WORKSPACE</div>
<h2>Observe → predict → certify → steer future sessions → measure</h2>
<p>This notebook creates a recommendation record. It has no presenter credentials and cannot publish to the live dashboard.</p></div>'''))
"""


TODO1 = """# TODO 1 — choose a traffic group and surge multiplier, then run this cell.
selected_group = "stadium|social-live|1-010204"
surge_multiplier = 4.0

event = lab.create_traffic_event(selected_group, surge_multiplier)
traffic = lab.simulate_event(event)
display(HTML(lab.traffic_plot(traffic)))
print(f"VALID EVENT · {event.group_label} · ×{event.surge_multiplier:.1f}")
"""


TODO2 = """# TODO 2 — choose p50 or p90. The forecast always uses the six windows before the target.
planning_risk = "p90"

forecast = lab.causal_ma_forecast(traffic, event, planning_risk=planning_risk, history_windows=6)
planned = getattr(forecast.new_load_ul_mbps, planning_risk)
actual = traffic[event.start_window]["offered_ul_mbps"]
print(f"CAUSAL FORECAST · source ends {forecast.source_window_end.isoformat()}")
print(f"p50={forecast.new_load_ul_mbps.p50:.1f} · p90={forecast.new_load_ul_mbps.p90:.1f} · actual={actual:.1f} UL Mbps")
print(f"PLAN ON {planning_risk.upper()} · {planned:.1f} UL Mbps")
"""


TODO3 = """# TODO 3 — choose static, reactive, or cohort-mpc; optionally edit the normalized weights.
controller = "cohort-mpc"
weights = lab.recommended_weights(controller, event, planning_risk)

certification = lab.certify_recommendation(
    forecast, event, controller=controller, planning_risk=planning_risk, weights=weights
)
print(certification.message)
print("requested:", certification.requested_weights)
print("applied:  ", certification.applied_weights)
print("existing sessions anchored:", certification.existing_sessions_anchored)
"""


SAFETY_DRILL = """# Safety drill — deliberately invalid: UPF-Z is unknown and weights do not normalize.
unsafe = lab.certify_recommendation(
    forecast,
    event,
    controller=controller,
    planning_risk=planning_risk,
    weights={"upf-a": 0.55, "upf-z": 0.55},
)
print(unsafe.message)
print("fallback applied:", unsafe.applied_weights)
"""


EVALUATE = """outcome = lab.close_loop(traffic, event, certification)
explanation = (
    f"We chose {controller} with {planning_risk} planning because uncertainty and residual capacity "
    "matter; only newly arriving sessions may follow the accepted weights."
)
decision = lab.build_decision(
    event, certification, outcome,
    controller=controller, planning_risk=planning_risk, explanation=explanation,
)
decision_path = lab.save_decision(decision)
print(json.dumps(outcome, indent=2))
print(f"\\nDECISION SAVED · {decision_path}")
"""


def cells(*, frozen: bool) -> list[dict[str, Any]]:
    event = lab.create_traffic_event("stadium|social-live|1-010204", 4.0)
    rows = lab.simulate_event(event)
    forecast = lab.causal_ma_forecast(rows, event, planning_risk="p90")
    certification = lab.certify_recommendation(
        forecast, event, controller="cohort-mpc", planning_risk="p90"
    )
    unsafe = lab.certify_recommendation(
        forecast,
        event,
        controller="cohort-mpc",
        planning_risk="p90",
        weights={"upf-a": 0.55, "upf-z": 0.55},
    )
    outcome = lab.close_loop(rows, event, certification)
    todo1_outputs = [
        display_html(lab.traffic_plot(rows)),
        stream(f"VALID EVENT · {event.group_label} · ×{event.surge_multiplier:.1f}\n"),
    ] if frozen else []
    todo2_outputs = [stream(
        f"CAUSAL FORECAST · source ends {forecast.source_window_end.isoformat()}\n"
        f"p50={forecast.new_load_ul_mbps.p50:.1f} · p90={forecast.new_load_ul_mbps.p90:.1f} · "
        f"actual={rows[event.start_window]['offered_ul_mbps']:.1f} UL Mbps\n"
        f"PLAN ON P90 · {forecast.new_load_ul_mbps.p90:.1f} UL Mbps\n"
    )] if frozen else []
    todo3_outputs = [stream(
        f"{certification.message}\nrequested: {certification.requested_weights}\n"
        f"applied:   {certification.applied_weights}\nexisting sessions anchored: True\n"
    )] if frozen else []
    unsafe_outputs = [stream(
        f"{unsafe.message}\nfallback applied: {unsafe.applied_weights}\n"
    )] if frozen else []
    evaluation_outputs = [stream(
        json.dumps(outcome, indent=2) + "\n\nDECISION SAVED · output/workshop/team-XX/WorkshopDecision.json\n"
    )] if frozen else []
    count = iter(range(1, 8)) if frozen else None

    def execution() -> int | None:
        return next(count) if count is not None else None

    return [
        markdown("# C-DOT UPF Closed-Loop Lab\n\n**90-minute control-room workshop · 28th · 11:30–13:00**\n\nSynthetic, deterministic simulation—not a live C-DOT network. The goal is one defensible control cycle, not a production claim.", "title"),
        code(SETUP, "setup", tags=["hide-input"], collapsed=True, execution_count=execution()),
        markdown("## Table roles (optional)\n\nPick any four: **traffic engineer**, **forecasting engineer**, **policy/safety engineer**, and **operator/reporter**. Roles guide the conversation; nothing is scored.", "roles"),
        markdown("<div class='lab-stage'>01 / TRAFFIC</div>\n\n## Create the event\n\nChoose one controllable group and a surge from ×1.25 to ×8. Offered demand is generated independently of what the UPFs can carry.", "traffic-stage"),
        code(TODO1, "todo-1", tags=["todo"], outputs=todo1_outputs, execution_count=execution()),
        markdown("**Hint 1** · Keep the stadium group for the canonical story, or run `lab.group_options()` to see all stable group IDs. If offered and carried separate during the surge, the difference is overload/loss—not hidden demand.", "hint-1"),
        code("# Collapsed solution 1\n# selected_group = 'stadium|social-live|1-010204'\n# surge_multiplier = 4.0", "solution-1", tags=["solution"], collapsed=True),
        markdown("<div class='lab-stage'>02 / SIMULATE</div>\n\n## Read the traffic correctly\n\n- **Offered demand**: what sessions attempted to send.\n- **Carried traffic**: what the network actually transported.\n- **Overload**: offered load above the available service envelope.\n- **Loss**: offered load not carried after queue/admission effects.\n\n<div class='lab-check lab-warn'><strong>Checkpoint:</strong> carried throughput is an outcome. It is not a clean demand-training label when the network is constrained.</div>", "simulate-stage"),
        markdown("<div class='lab-stage'>03 / FORECAST</div>\n\n## Forecast before the event\n\nIssue a causal six-window moving average. The feature window must end at or before the target starts. Compare the median with a conservative p90 planning choice.", "forecast-stage"),
        code(TODO2, "todo-2", tags=["todo"], outputs=todo2_outputs, execution_count=execution()),
        markdown("**Hint 2** · Use `planning_risk = 'p50'` for the central estimate or `'p90'` for a conservative load projection. Neither quantile guarantees that an unannounced flash crowd will be covered.", "hint-2"),
        code("# Collapsed solution 2\n# planning_risk = 'p90'\n# forecast = lab.causal_ma_forecast(traffic, event, planning_risk=planning_risk, history_windows=6)", "solution-2", tags=["solution"], collapsed=True),
        markdown("<div class='lab-stage'>04 / CERTIFY + OPTIMIZE</div>\n\n## Make a safe recommendation\n\nThe independent gate checks causality, health, group eligibility, locality, finite `[0,1]` weights, normalization, directional/session capacity, and new-session-only scope.", "certify-stage"),
        code(TODO3, "todo-3", tags=["todo"], outputs=todo3_outputs, execution_count=execution()),
        markdown("**Hint 3** · Start with `lab.recommended_weights(controller, event, planning_risk)`. Try editing one destination or setting `migrate_existing=True`; any unsafe recommendation must retain the last safe/static policy.", "hint-3"),
        code("# Collapsed solution 3\n# controller = 'cohort-mpc'\n# weights = lab.recommended_weights(controller, event, planning_risk)", "solution-3", tags=["solution"], collapsed=True),
        markdown("### Required safety drill\n\nRun the invalid recommendation below once. The visible rejection is a feature: the live presenter should show at least one team fallback.", "drill-title"),
        code(SAFETY_DRILL, "safety-drill", tags=["safety-drill"], outputs=unsafe_outputs, execution_count=execution()),
        markdown("<div class='lab-stage'>05 / EVALUATE</div>\n\n## Close the modeled loop\n\nMeasure the later consequence without rewriting earlier telemetry, then emit the small `WorkshopDecision` handoff. The presenter—not this notebook—translates one team recommendation into dashboard controls.", "evaluate-stage"),
        code(EVALUATE, "evaluate", tags=["decision-output"], outputs=evaluation_outputs, execution_count=execution()),
        markdown("<div class='lab-check lab-safe'><strong>Say it precisely:</strong> the selected policy changed placement for future sessions and reduced modeled exposure in this synthetic trace. Established sessions were not migrated. This is not guaranteed overload prevention or production readiness.</div>\n\n## Table close\n\nComplete one sentence: **“We would deploy this in advisory mode only after ___.”**", "close"),
    ]


def notebook(*, frozen: bool) -> dict[str, Any]:
    return {
        "cells": cells(frozen=frozen),
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.10"},
            "workshop": {
                "schema_version": "cdot-workshop-notebook/1.0",
                "synthetic": True,
                "participant_has_presenter_credentials": False,
                "visible_stages": ["Traffic", "Simulate", "Forecast", "Certify/Optimize", "Evaluate"],
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def fallback_html() -> str:
    event = lab.create_traffic_event("stadium|social-live|1-010204", 4.0)
    rows = lab.simulate_event(event)
    forecast = lab.causal_ma_forecast(rows, event, planning_risk="p90")
    certification = lab.certify_recommendation(forecast, event, controller="cohort-mpc", planning_risk="p90")
    unsafe = lab.certify_recommendation(
        forecast, event, controller="cohort-mpc", planning_risk="p90", weights={"upf-a": .55, "upf-z": .55}
    )
    outcome = lab.close_loop(rows, event, certification)
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>C-DOT UPF Closed-Loop Lab · Frozen</title><style>
body{{margin:0;background:#102a34;color:#ecf4f6;font:16px/1.55 Arial,sans-serif}}main{{max-width:1060px;margin:auto;padding:40px 28px 80px}}
h1{{font-size:42px;margin:.2em 0}}h2{{color:#72d4d8;margin-top:52px}}.eyebrow{{font:12px monospace;letter-spacing:.14em;color:#72d4d8}}
.boundary{{border-left:5px solid #e2a62c;padding:14px 18px;background:#183a45}}pre{{white-space:pre-wrap;background:#0a2028;padding:18px;border:1px solid #31515c;color:#dce9ec}}
svg{{background:#f7fafb!important}}.safe{{color:#72d69e}}.warn{{color:#f1be54}}@media print{{body{{background:white;color:#17343f}}}}
</style><main><div class="eyebrow">FROZEN FALLBACK · SYNTHETIC · NO CREDENTIALS</div><h1>C-DOT UPF Closed-Loop Lab</h1>
<p class="boundary">Pre-executed fallback. It demonstrates the same contracts and canonical outputs but does not replace the presenter-controlled causal dashboard.</p>
<h2>01 · Traffic → 02 · Simulate</h2><p>{html.escape(event.group_label)} · surge ×{event.surge_multiplier:.1f}</p>{lab.traffic_plot(rows)}
<p>Offered demand separates from carried traffic under constraint. The gap is overload/loss; carried throughput is not a clean demand label.</p>
<h2>03 · Forecast</h2><pre>source_window_end = {forecast.source_window_end.isoformat()}
target_window_start = {forecast.target_window.start.isoformat()}
p50 = {forecast.new_load_ul_mbps.p50:.1f} UL Mbps
p90 = {forecast.new_load_ul_mbps.p90:.1f} UL Mbps
actual = {rows[event.start_window]['offered_ul_mbps']:.1f} UL Mbps</pre>
<h2>04 · Certify / Optimize</h2><p class="safe">{html.escape(certification.message)}</p><pre>{html.escape(json.dumps(certification.to_dict(), indent=2))}</pre>
<p class="warn">Safety drill: {html.escape(unsafe.message)}</p>
<h2>05 · Evaluate</h2><pre>{html.escape(json.dumps(outcome, indent=2))}</pre>
<p class="boundary">Future sessions were redirected; established sessions were not migrated. Claim reduced modeled exposure—not guaranteed prevention or production readiness.</p>
</main></html>"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    FALLBACK.mkdir(parents=True, exist_ok=True)
    (OUT / "CDOT_UPF_Closed_Loop_Lab.ipynb").write_text(
        json.dumps(notebook(frozen=False), indent=1) + "\n", encoding="utf-8"
    )
    (OUT / "CDOT_UPF_Closed_Loop_Lab_Frozen.ipynb").write_text(
        json.dumps(notebook(frozen=True), indent=1) + "\n", encoding="utf-8"
    )
    (FALLBACK / "CDOT_UPF_Closed_Loop_Lab_Frozen.html").write_text(fallback_html(), encoding="utf-8")
    print(OUT / "CDOT_UPF_Closed_Loop_Lab.ipynb")
    print(OUT / "CDOT_UPF_Closed_Loop_Lab_Frozen.ipynb")
    print(FALLBACK / "CDOT_UPF_Closed_Loop_Lab_Frozen.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
