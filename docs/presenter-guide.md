# C-DOT pilot presenter guide

This guide supports a five-minute, presenter-paced walkthrough of the frozen
cohort-MPC demo. The browser uses synthetic data and a deterministic local
simulation. It does not publish policy to a live C-DOT SMF or migrate an
established session.

## Before the room joins

1. Start the demo with `scripts/start-demo.sh` and open the printed local URL.
2. Sign in with the rehearsal account. The frozen cohort-MPC controller is the
   guided default; controller selection is intentionally under Expert Mode.
3. Confirm the story overview shows `SYNTHETIC DATA` and the header says the
   browser is connected.
4. Leave the run on the overview. Do not inject an event for the guided story;
   all required demand and capacity events are already in the frozen scenario.

## Five-minute run of show

| Time | Checkpoint | Presenter action | Speaker note |
| --- | --- | --- | --- |
| 0:00–0:35 | Step 0 — The normal network | Select **Start guided demo**. | “The three UPFs begin inside their safe operating envelopes. Established sessions remain anchored to the UPF selected at arrival.” |
| 0:35–1:20 | Step 10 — The problem appears | Wait for the automatic pause. | “Stadium demand is rising. UPF-A also has a known uplink-capacity reduction at step 21. Static placement can concentrate persistent cohorts on capacity that is about to shrink.” |
| 1:20–2:35 | Step 20 — Predict and compare | Select **Continue to Predict and compare**. | “The causal MA6 forecast estimates demand from history available at issue time. It does not choose a route. MPC chooses weights over a two-hour cohort horizon, then its first action is certified against static routing from the same state and known events.” |
| 2:35–3:40 | Step 30 — Divert new sessions | Select **Continue to Divert new sessions**. | “The muted line is the previous/static route; blue is the certified route. Width encodes future-session allocation, not total traffic. Existing sessions stay in place.” |
| 3:40–5:00 | Step 60 — Result and evidence | Select **Continue to Result and evidence**, then **Open frozen evidence**. | “The defensible live conclusion is reduced modeled exposure, not overload prevented. Actual overload and loss remain visible when capacity is still exceeded.” |

The runner checks a requested `pause_at_step` only after it realizes the tick.
The checkpoints therefore do not depend on browser timers: steps 10, 20, 30,
and 60 are deterministic even if the browser is briefly disconnected.

## Exact defensible evidence claims

- The frozen campaign contains 30 matched synthetic static/MPC pairs.
- Mean-pair UL overload-area improvement is **10.52%**.
- Its bootstrap 95% interval is **4.81% to 16.93%**.
- Severity-weighted aggregate UL overload-area improvement is **2.84%**.
- All frozen aggregate guardrails pass.
- Material pair-level regressions remain in fault-heavy tails; the worst pair
  is negative. This is a controlled demonstration candidate, not a production release.
- “Divert” means changing weighted rendezvous placement for sessions that
  arrive after the accepted policy epoch. It does not mean session migration.
- The provisional extreme trained forecaster does not drive this guided MPC
  profile. The frozen profile uses the causal six-window moving average.

Avoid “the optimizer prevented overload,” “traffic was migrated,” “the model
controls the network,” and “production ready.” Prefer “the same-state model
projects less exposure,” “future sessions were redirected,” and “the candidate
passed the frozen aggregate gates while tail risk remains.”

## Reading the per-UPF routing proof

- Each UPF card shows the active class's previous and candidate weight, followed
  by the sessions and Mbps that actually arrived there. The iteration ledger
  retains the same values after the 10-minute bucket closes.
- After each bucket closes, use **Completed surge analysis** in **Live Dashboard**.
- Open **Technical Detail → Telemetry** to select any of the four surge windows.
  The inspector shows class-level arrivals, offered new-session demand,
  admissions and rejections, plus admitted placement by UPF for all six modeled
  traffic classes. Network loss remains explicitly labeled as aggregate because
  the simulator does not attribute carried or dropped bytes to an individual class.
  It selects the latest completed episode automatically and explains the event,
  forecast error, normalized same-state exposure reduction, applied or held route, canonical
  admissions, peak observed loss, and per-UPF peak operating index. Earlier
  completed episodes remain selectable from the same panel.
- The forecast and actual values are new-session UL + DL demand for the active
  class. The error is against p50; p90 coverage is reported separately.
- The audience view normalizes static routing to **100% modeled overload
  exposure** and shows the percentage remaining under MPC. Say “lower is
  better; both policies were evaluated from the same starting state.” Raw
  objective values are intentionally confined to Technical Detail. A positive
  weight delta means that UPF receives more future sessions; it does not mean
  existing sessions moved.
- UPF-B receives additional stadium and metro sessions when it is eligible. In
  the residential episode it is explicitly marked **not eligible** because the
  configured service eligibility list is UPF-A/UPF-C only, regardless of B's
  spare headroom.
- UPF-A or UPF-C may remain overloaded after diversion because their established
  cohorts stay attached. State this as reduced exposure, not eliminated load.

## Reconnection and recovery

- **Browser reload or short disconnect:** wait for the Connected indicator.
  The run ID and presenter token are restored from session storage, and the
  current server-side chapter/checkpoint is reloaded. Continue normally.
- **Runner still moving:** do not double-click Continue. The button reads the
  next server checkpoint and is disabled while the runner advances.
- **Unexpected manual event/controller:** enable Expert Mode, select **Reset**,
  return to the story overview from the C-DOT mark, and restart the guided demo.
- **Held policy:** say that the certificate or fallback retained the safe
  policy. Open Technical Detail → Optimizer or Trace; do not imply actuation.
- **Telemetry gap:** Technical Detail labels the degraded samples. Reset for the
  canonical guided story; realized history is never backfilled or rewritten.
- **API restart:** sign in again and start a new deterministic run. A process
  restart does not persist an in-memory run.

## Closing boundary

End on Evidence or Technical Detail → Boundary. State that the pilot already
demonstrates deterministic telemetry, causal forecast input, same-state
certification, new-session-only simulated actuation, and frozen evidence. Live
telemetry mapping, authenticated SMF/EMS publication, testbed calibration,
established-session migration, and a production tail gate require future C-DOT
integration work.
