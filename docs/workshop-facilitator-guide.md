# Interactive C-DOT UPF optimization workshop

This is the facilitator runbook for the 28th, 11:30–13:00 slot. It is an
interactive closed-loop engineering lab for 15–35 mixed telecom and software
participants. It complements the professor's theory session on the 27th; do
not reteach the mathematical derivation.

The sentence to keep visible is:

> Observe → predict → certify → steer future sessions → measure → discuss
> production integration.

All displayed traffic is synthetic and deterministic. The lab does not connect
to a live C-DOT network, publish to an SMF, or migrate established sessions.

## Success checks

By the end, every table should be able to:

- complete one end-to-end modeled control cycle;
- explain why established sessions stay on the UPF selected at arrival;
- distinguish offered demand, carried traffic, overload, and loss;
- defend p50 or p90 planning and a safe routing decision; and
- name at least three C-DOT integration requirements.

At least one table must run the required unsafe-policy cell and show the
last-safe/static fallback. If a table is delayed, use the hint directly below
each TODO; after two minutes, open the collapsed solution or switch to the
frozen notebook.

## 90-minute run of show

| Time | Screen and action | Discussion checkpoint |
| --- | --- | --- |
| 0–5 | Workshop slide 1. Ask teams to vote: static, reactive, or predictive. Do not reveal. | “You are the controller. What would you do before the stadium surge?” |
| 5–12 | Slide 2, then the dashboard story overview. State the synthetic/testbed boundary. | Trace generator → simulation → forecast → controller → gate → steering → telemetry. |
| 12–20 | Dashboard, static controller in Expert Mode. | Predict which UPF constrains first and which metric exposes it. |
| 20–34 | Notebook stages Traffic and Simulate; TODO 1. | Plot offered versus carried. Carried traffic is an outcome, not unconstrained demand. |
| 34–47 | Notebook Forecast; TODO 2. | Confirm source history ends before the target. Compare p50 with p90. |
| 47–63 | Notebook Certify/Optimize; TODO 3 and safety drill. | Check eligibility, health, capacity, normalized weights, and anchoring. |
| 63–72 | Collect one `WorkshopDecision.json`. Presenter applies controller/surge settings and runs the dashboard. | Inspect telemetry, forecast, policy, placement, overload area, and loss in causal order. |
| 72–79 | Slide 6 and dashboard Evidence. Revisit opening vote. | Compare matched static, reactive, and frozen cohort-MPC behavior; point out tail regressions. |
| 79–88 | Slide 7 and decision canvas integration prompts. | Map contracts to Prometheus, capacity calibration, SMF selection, auth, publication, and rollback. |
| 88–90 | Keep slide 7 visible. One sentence per table. | “We would deploy this in advisory mode only after ___.” |

Questions are welcome throughout, but cap each lab checkpoint discussion at two
minutes. The core run must finish by minute 79.

## Table roles

Offer four roles without scoring or a leaderboard:

- **Traffic engineer** chooses the controllable group and surge.
- **Forecasting engineer** checks causality and chooses p50 or p90.
- **Policy/safety engineer** checks weights, eligibility, health, and fallback.
- **Operator/reporter** records the explanation and hands off the decision.

For 4–6 tables, create copies before participants arrive:

```bash
env/bin/python -m workshop.build_notebooks
env/bin/python -m workshop.prepare_teams --teams 6
```

Each `output/workshop/team-XX/` directory contains a separate participant
notebook and configuration. The notebook writes only its own
`WorkshopDecision.json`. It contains no dashboard username, password, token, or
actuation interface.

On the venue LAN, bind the services to all host interfaces but advertise the
presenter machine's real LAN address:

```bash
CDOT_WORKSHOP_HOST=0.0.0.0 \
CDOT_WORKSHOP_PUBLIC_HOST=192.0.2.10 \
./scripts/start-workshop.sh
```

Replace `192.0.2.10` with the rehearsed host address. The launcher writes
`output/workshop/materials-qr.svg` and a matching `.txt` link for the closing
slide/screen. The QR contains only the participant Jupyter token, never the
dashboard username or presenter password.

## Presenter control translation

Read a team's decision record and translate only these fields:

| Decision field | Dashboard action |
| --- | --- |
| `selected_event.surge_multiplier` | Expert Mode → surge injection |
| `controller` | Expert Mode → controller (`static`, `reactive`, or cohort MPC) |
| `forecast_risk` | Explain planning stance; the frozen guided profile remains MA6 with conservative planning |
| `explanation` | Read the team's hypothesis before starting the run |

Do not give participant notebooks presenter credentials. Do not imply that the
notebook policy was published to the simulated runner. The presenter controls
the authoritative runtime and the audit trail records that action.

Use guided checkpoints for the canonical story. Use Expert Mode only for
controller selection, surge injection, a UPF failure, or a telemetry gap. After
an intervention, say explicitly that earlier telemetry was not regenerated.

## Exact evidence language

- Frozen campaign: 30 matched synthetic static/MPC pairs.
- Mean-pair UL overload-area improvement: **10.52%**.
- Bootstrap 95% interval: **4.81% to 16.93%**.
- Severity-weighted improvement: **2.84%**.
- Material pair-level regressions remain in fault-heavy tails; the worst pair
  is negative.
- Steering affects sessions that arrive after the accepted policy epoch.
  Established sessions remain anchored.
- Claim **reduced modeled exposure**, not guaranteed overload prevention or
  production readiness.

## C-DOT co-design prompts

Ask participants to place repository contracts against real sources and
targets:

1. Which Prometheus metrics, labels, counter/reset semantics, freshness bounds,
   and telemetry quality flags can populate the canonical telemetry contracts?
2. How will C-DOT calibrate directional Mbps and session safe envelopes by UPF?
3. Where do group eligibility, locality, slice/DNN rules, and health state live?
4. What supported SMF/EMS hook selects a UPF for a **new** session?
5. How is a versioned policy authenticated, atomically published, audited,
   expired, and rolled back?
6. Which shadow-mode gates and untouched scenarios are required before a
   bounded pilot?

## Compute and PBS

The workstation runs the interactive simulation. Present the cluster as an
offline scenario factory: PBS array jobs execute independent
seed/scenario/controller shards; one simulation is not distributed across all
nodes.

Only if account and queue access are confirmed, submit this bonus smoke job
near the beginning:

```bash
qsub pbs/check_build.pbs
```

The workshop never waits for it. The comparison source is the frozen artifact
`demo_api/data/cohort_mpc_full_campaign_evidence_v1.json`, including campaign
ID, profile hash, source-artifact checksum, seeds/pair count, and metrics. The
current architecture transfers completed artifacts to the presenter host; it
does not stream PBS output into the dashboard.

## Four fallback levels

1. **Central live** — `scripts/start-workshop.sh`, team notebooks, and live
   dashboard on the venue LAN.
2. **Offline notebook bundle** — repository checkout, prepared team folders,
   local Jupyter, and no internet dependency.
3. **Frozen run-all** —
   `workshop/CDOT_UPF_Closed_Loop_Lab_Frozen.ipynb` or the standalone
   `workshop/fallback/CDOT_UPF_Closed_Loop_Lab_Frozen.html`.
4. **Recorded reveal** — current dashboard screenshots under
   `frontend/tests/demo.spec.ts-snapshots/` plus the video produced before
   travel by `scripts/capture-workshop-fallback.sh`.

If the browser reconnects, reload it: the dashboard fetches the current
snapshot and resumes from the simulator state. If the notebook server fails,
open the standalone frozen HTML directly.

## Rehearsal gate

Before travel, run:

```bash
env/bin/python -m unittest discover -s tests -v
npm --prefix frontend run build
env/bin/python scripts/preflight.py
env/bin/python -m workshop.build_notebooks
env/bin/python -m jupyter nbconvert --to notebook --execute \
  --ExecutePreprocessor.timeout=120 \
  --output /tmp/CDOT_UPF_Closed_Loop_Lab_restart_run_all.ipynb \
  workshop/CDOT_UPF_Closed_Loop_Lab_Frozen.ipynb
npm --prefix frontend run test:e2e
```

Then rehearse the full 90 minutes with a successful policy, the rejected safety
drill, browser reconnect, telemetry gap, and delayed PBS job. Capture the
fallback video after the same build. Confirm the projector at its actual
resolution and keep the canvas printable on one A4 page.

## Acceptance checklist

- [ ] All teams finish all three TODOs or reach the same outputs via hints.
- [ ] At least one unsafe recommendation visibly falls back.
- [ ] A later policy affects later telemetry without changing prior history.
- [ ] Every table names three C-DOT integration requirements.
- [ ] Core activity ends by minute 79.
- [ ] Integration discussion and closing sentence are preserved.
