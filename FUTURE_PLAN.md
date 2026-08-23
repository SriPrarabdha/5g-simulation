# Future plan: decision cadence

Status: **proposed experiment; not run, not gated, no seeds consumed**

Date raised: 2026-08-23

## The question

Telemetry is collected every 30 seconds, but the system forecasts, optimises and
publishes a policy only every 10 minutes. Would running that loop every 1–2
minutes instead improve the forecaster, the optimizer, or the handling of
unannounced surges?

Short answer: **it would help the optimizer measurably on scheduled capacity
events, it would actively damage the forecaster if done naively, and it does not
touch the constraint that actually bounds this system.** The rest of this
document works through why, and specifies the one experiment worth running.

## 1. Three different designs, only one of which is cheap

"Run it faster" is ambiguous, because the current build uses a single parameter
for two different things (see section 5). Separating them gives three designs.

| | Observation window | Controller cadence | Forecast noise | Cost |
|---|---|---|---|---|
| **A — decouple, keep the forecaster** | 10 min, unchanged | 1–2 min | unchanged | small code change |
| **B — rolling window** | 10 min, sliding | 1–2 min | unchanged | + conformal recalibration |
| **C — shorten everything** | 1–2 min | 1–2 min | **3× worse** | forecaster fails its own gates |

**Design A is the right first experiment.** The forecast is still produced on the
existing 10-minute tumbling window, exactly as it is today; only the controller
runs more often, consuming the latest available forecast plus *fresh* UPF state —
current load, health, and declared capacity events.

The value in Design A comes from the fresh state, not from a fresher forecast.
That is not a weakness, because of a fact confirmed in the code:

> `optimization/predrain_flow.py` and the MPC branch trigger on
> `_known_future_capacity_event(config, current_step)` in
> `simulator/macro/controllers.py:1436` — an event whose `known_at_step` has
> already passed and whose `step` is still in the future. **They fire on a
> declared capacity event, not on a forecast.**

So the entire scheduled-fault benefit quantified in section 3 is available with
**zero change to the frozen forecast bundle**, and therefore with no risk to any
forecast result already recorded.

Design B is the follow-on if, and only if, Design A shows benefit and the
residual gap is demonstrably demand-driven rather than capacity-driven.

Design C should not be run. Section 2 explains why.

## 2. Why shortening the observation window breaks the forecaster

Session arrivals are Poisson. The relative standard error of a bucket estimate
falls as `1/sqrt(n)`, so a shorter bucket is not a higher-resolution measurement
of demand — it is a noisier one.

Measured mean arrivals are 39,156,949 sessions/day across 96 groups, or about
283 per group per minute.

| Bucket | Arrivals per group | Irreducible Poisson noise floor |
|---:|---:|---:|
| 10 min (current) | 2,833 | **1.88%** |
| 5 min | 1,416 | 2.66% |
| 2 min | 567 | 4.20% |
| 1 min | 283 | 5.94% |
| 30 s | 142 | 8.40% |

The frozen v1 forecaster achieves **4.48% macro WAPE at the 10-minute horizon**.
At a 1-minute bucket the noise floor alone (5.94%) exceeds today's total error.
At 2 minutes the floor (4.20%) sits directly on top of it. There is no model that
recovers this; the information is not present in the data.

It is worse for sparse groups. Cloud backup in the lowest-scale zone generates
22.4 arrivals per 30 s:

| Bucket | Arrivals | Noise floor |
|---:|---:|---:|
| 10 min | 448 | 4.72% |
| 2 min | 90 | 10.56% |
| 1 min | 45 | **14.94%** |

The frozen Phase-2 selection gate requires that no regime or horizon slice
regress by more than 5%. Sparse groups would fail that gate on arithmetic alone,
before any modelling decision is made.

There is a second-order effect worth stating. The p90/p95 bounds are
split-conformal, derived from held-out residuals. Noisier residuals produce wider
bounds, the LP plans against a wider bound, it spreads allocation more, and its
behaviour converges toward Static. Design C would most likely not produce a
visibly broken controller — it would produce one that quietly stops contributing.

## 3. What faster decisions actually buy

### 3.1 The controllability surface is exactly linear in lead time

Re-deriving from `output/delhi/traffic-realism-v2-evaluation.json`, the
controllable fraction divided by lead time is constant to five decimal places:

```
lifetime 240 min : 0.00164  0.00164  0.00164  0.00164  0.00164
lifetime 120 min : 0.00527  0.00527  0.00527  0.00527  0.00527
lifetime  60 min : 0.01441  0.01441  0.01441  0.01441  (saturates)
```

Controllable load is therefore proportional to notice time. Cadence does not
change how much notice an event gives — but it does change how much of that
notice is usable, because the controller can only act on an epoch boundary.

### 3.2 Recovered notice on a scheduled capacity event

At the 4-hour mean session lifetime and a 2-hour declared warning:

| Cadence | Worst-case usable notice | Controllable load |
|---:|---:|---:|
| 10 min (current) | 110 min | 18.03% |
| 2 min | 118 min | **19.35%** |
| 1 min | 119 min | 19.51% |

That is **+1.3 to +1.5 percentage points, or about 8% more steerable load**, on
precisely the scenario where pre-drain already works (+43.89% on scheduled faults
for the 50% blend).

This matters because the 50% blend passed **ten of eleven** development gates and
failed only the worst-pair tail limit, by **0.37 percentage points**. An 8%
improvement in the pre-event window is the right order of magnitude to close a
gap that small. It is the strongest single argument in this document.

### 3.3 Reaction latency to an unannounced surge

| Cadence | Mean detect-to-act delay | Worst case |
|---:|---:|---:|
| 10 min | 10.0 min | 20 min |
| 2 min | 2.0 min | 4 min |
| 1 min | 1.0 min | 2 min |

The benefit scales **inversely with event duration**, because once a surge is
detected the remainder of the surge is effectively notice:

| Event | Response window lost at 10 min | at 1 min |
|---|---:|---:|
| 4-hour surge | 4% | 0.4% |
| 30-minute half-time spike (×3.2) | **33%** | 3% |

For long surges this is negligible. For the short sharp spike — which is where
the overload actually concentrates in the stadium lifecycle — it is substantial.
This is the second real argument, and it is weaker than section 3.2 because it is
capped by the two facts in section 4.

## 4. Why this does not touch the binding constraint

Two results bound how much any cadence change can deliver.

**At zero notice, the controllable fraction is 0.0%.** A genuine surprise offers
nothing to steer, no matter how fast it is sensed. Faster sensing only shortens
the unmanaged prefix of an event; it cannot create leverage before the event
starts.

**The oracle ladder says demand knowledge is the wrong axis.** Perfect arrival
knowledge with causal fault observation recovers about 55% of UL overload.
Clairvoyant *fault* knowledge, still new-session-only, reaches **100%**. Cadence
improves demand sensing, which lives on the 55% axis.

Reinforcing this: in the one-day pilot, a near-total edge-UPF outage accounted for
**103,965 of 105,181** UL overload-area seconds — 98.8% of the total. That event
was read directly from UPF health state, not from a bucketed forecast, so no
cadence change would have altered it at all.

## 5. Where the two concepts are welded together in code

`decision_interval_steps` (default 20 = 10 minutes) currently controls four
things at once:

| Location | What it controls |
|---|---|
| `simulator/macro/engine.py:545` | `decision_due` — when the controller runs |
| `simulator/macro/engine.py:683` | `duration` — the width of the demand `TimeWindow` |
| `simulator/macro/engine.py:687` | `bucket_start_step` — the accumulation window |
| `simulator/macro/engine.py:658` | `state_ttl_seconds` on every published `UPFState` |

`self._interval_arrivals` is also cleared at every decision, so the observation
bucket is a non-overlapping tumbling window.

Enabling Design A therefore requires a small, well-scoped change:

1. Split the parameter into `decision_interval_steps` (controller cadence) and
   `observation_window_steps` (bucket width), with the latter defaulting to the
   former so every existing manifest and every frozen result is unaffected.
2. Replace the running `_interval_arrivals` total with a bounded ring buffer of
   per-step counts, so a 20-step window can be summed at a 4-step cadence.
3. Keep `state_ttl_seconds` bound to `observation_window_steps`, not to the
   cadence, so freshness semantics do not silently change.
4. Reject any manifest where `observation_window_steps` is not an integer
   multiple of `decision_interval_steps`.

Points 1 and 4 preserve exact reproducibility of every frozen campaign, which is
a hard requirement — no existing result may move.

## 6. Costs and risks

**The churn gate is cadence-normalised and would stop meaning what it means
today.** The frozen gate is normalised churn ≤ 0.05 L1 *per group-decision*. At
5× the cadence, a candidate holding the same per-decision churn passes the gate
unchanged while producing 5× the total daily routing change. Measured values sit
at 0.0140 (blend 25%) to 0.0640 (full strength). **Before running this
experiment, the churn gate must be restated per unit time, not per decision.**
Changing a gate to accommodate an experiment is normally forbidden; this is the
narrow exception where the existing gate becomes ill-defined once cadence is a
variable, and the restatement must be pre-registered and applied to the existing
candidates as well so the comparison stays honest.

**Campaign compute scales linearly.** Each 120-pair development campaign
currently takes about 1 h 10 m on one exclusive 125-CPU node. At a 2-minute
cadence that becomes roughly 6 hours. This is a research-loop cost, not a
production cost.

**Per-decision latency is not a problem, except for MPC.** The measured budget at
a 2-minute cadence is 120 s. Pre-drain solves in 27–28 ms typical and 118–127 ms
worst; the LP is capped at 1 s. Cohort MPC at 4.1–4.2 s average and 6.18 s
maximum fits, but consumes 5× more compute overall and should be excluded from
the first matrix.

**Telemetry robustness is unaffected under Design A and degrades under C.** With
a missing-scrape probability of 0.008, a 20-step window has a 14.8% chance of
containing at least one bad scrape but loses only 5% of its data when it does. A
4-step window is affected only 3.2% of the time but loses 25% of its data, which
would raise the fail-closed-to-Static rate. Design A keeps the 20-step window and
avoids this entirely.

**Design B additionally breaks conformal independence.** Overlapping windows
produce correlated residuals, which weakens the split-conformal coverage
guarantee. If Design B is ever attempted, coverage must be re-measured on held-out
data before any control result is accepted; the existing 94.21% / 96.69% figures
would not carry over.

## 7. Proposed experiment

**Pre-registered, one matrix, Design A only.**

| Item | Value |
|---|---|
| Hypothesis | Reducing controller cadence from 10 min to 2 min, with the observation window held at 10 min, increases mean-pair UL overload-area improvement on scheduled-fault days by ≥ 5% relative, with no worst-pair regression |
| Candidates | Pre-drain 50% blend and 25% blend, each at 10-min (control) and 2-min cadence — 4 arms |
| Scenario | Scheduled-fault days only for the primary test; mixed stress reported as a guardrail |
| Seeds | 46473–46496 (24 fresh development seeds; next free block after Phase 3.2's 46449–46472) |
| Pairs | 24 per arm, 96 total, each exactly paired against Static |
| Forecaster | Frozen bundle unchanged; no retraining, no recalibration |
| MPC | Excluded from this matrix |
| Protected seeds | 46003, 46201–46216, 46301–46330 remain untouched |

**Gates.** The existing eleven development gates apply unchanged, with two
amendments pre-registered *before* the run:

- normalised churn restated as L1 per group per hour, with the threshold set so
  the existing 10-min candidates score identically to today;
- end-to-end decision latency measured against the 120 s cadence budget rather
  than the 500 ms figure, which was chosen for a different cadence.

**Primary comparison.** 2-min arm against 10-min arm of the *same* candidate on
the *same* seeds — not against Static. Static remains the guardrail baseline.

## 8. Decision rule

- If the 2-minute arm improves scheduled-fault mean-pair UL by ≥ 5% relative
  **and** does not worsen the worst pair, escalate the 50% blend to a full
  eleven-gate development matrix at the new cadence.
- If it improves the mean but worsens the tail, the finding is that cadence buys
  aggression rather than precision. Stop; the tail problem is mechanism-shaped,
  not timing-shaped, and Phase 3.2 already closed the fixed/interpolated
  hypothesis.
- If it is neutral, cadence is not a lever in this system. Record the negative
  result and close the question — do not proceed to Design B.
- Under no outcome does this authorise validation or release seeds.

## 9. Related backlog items

These are already identified elsewhere and are not part of this plan; they are
listed so this document is not read as the complete forward view.

- **Session migration.** The single highest-value open question, but the oracle
  does *not* establish its value: the clairvoyant new-session-only row already
  reaches 100%, so the migration row had no headroom left to demonstrate.
  Migration has never been evaluated under realistic causal information. The
  case for it rests on mechanism — it is the only lever that reaches
  already-attached load. Note also that "migration" covers four operations with
  very different costs (new-session placement, I-UPF relocation, ULCL/branching
  point, and PSA relocation under SSC mode 2/3); only the last is disruptive, and
  it is the one operators avoid. Blocked on a C-DOT capability answer.
- **Deduplicate the Static baseline.** Each campaign re-simulates its own paired
  baseline; computing each unique baseline once frees roughly half the compute.
- **Re-open the worker packing ladder.** 16 and 32 workers failed on CPU
  efficiency, not memory (15.8% of node RAM at 32 workers). Something serialises.
- **Regenerate the training corpus with traffic-model/2.0.** Every throughput
  figure currently comes from the simpler generator with fixed per-class rates.
- **Forecast evaluation correctness.** Implement the manifest's declared week
  split and report event-stratified slices as standard.
- **Real telemetry contract from C-DOT.** All calibration work is blocked on it.

## Boundary

Nothing in this document has been run. The quantities in sections 2 and 3 are
derived arithmetically from already-published measurements — the calibration-day
arrival counts, the controllability surface, and the frozen forecast metrics — and
not from any new simulation. No seed was consumed to produce it.
