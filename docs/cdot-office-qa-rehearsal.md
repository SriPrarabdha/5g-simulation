# C-DOT technical review · Q&A rehearsal sheet

Use the first sentence as the direct answer. Add the rest only if the questioner wants detail. Never turn a missing C-DOT fact into a synthetic claim.

## Scope and architecture

### 1. What exactly can the controller change?

Only the routing weights used when a new session is established. The SMF can use those weights to select an eligible UPF for the new session. Established sessions remain anchored in the current contract, so most load at any instant is already committed.

### 2. Why are you not steering packets directly?

This project studies UPF selection, which is a session-level control decision. Packet routing, transport engineering and per-flow forwarding are different control planes with different interfaces and timescales. Adding them would change the problem rather than improve this controller.

### 3. Is moving an established session impossible in 5G?

We are not claiming it is impossible; we are treating it as an unresolved C-DOT integration question. The evidence is deliberately conservative and assumes no migration. If C-DOT supports a safe migration mechanism, the simulator should model its signalling, latency, packet-loss and state-transfer costs before using it.

## Traffic-model foundations

### 4. Why simulate sessions rather than individual packets?

Because the steering decision and its memory are both session-level. The simulator needs to know when sessions start, where they are anchored, how much bandwidth they request and when they finish. Packet-level simulation for 16 million synthetic users would be vastly more expensive without answering the UPF-placement question more accurately.

### 5. Why use a Poisson distribution for arrivals?

Poisson is a transparent starting model for counts arriving in a short interval. It is not the whole traffic model: time-of-day curves, correlated drift, bursts, weekend effects and events modify the rate. Real C-DOT arrival traces should be used to test over-dispersion and replace this assumption where necessary.

### 6. Why use heavy-tailed session durations?

Because communication workloads normally contain many short sessions and a smaller number of very long sessions. Those long sessions are operationally important: once placed, they can occupy a UPF through a later capacity loss. A light-tailed or fixed-duration model would make steering look more reversible than it is.

## Traffic realism and telemetry

### 7. Are the capacities and subscriber numbers real C-DOT values?

No. They are explicit synthetic assumptions used to test the engineering and evidence machinery. The deck must not be described as calibrated to C-DOT. Real per-UPF throughput limits, safe utilization, session limits and subscriber/group populations are required for calibration.

### 8. How do you validate a synthetic traffic model?

At two levels. Internal validity checks traffic conservation, deterministic reproduction, population conservation, eligibility, fitted drift, duration and bandwidth distributions, and memory scaling. External validity requires comparison with operator telemetry; that part has not happened yet.

### 9. Why does the rural area have the largest scale factor?

It is a known artifact of the current synthetic arithmetic ramp, not a claim that rural traffic exceeds urban traffic. Area labels currently drive service mix and daily profile; a simple index drives population scale. Replacing that ramp with real per-area subscriber counts is an explicit calibration task.

### 10. What is the difference between offered and carried traffic?

Offered traffic is what users request before capacity is applied. Carried traffic is what the network serves. The difference becomes queued, dropped or rejected traffic. Training demand forecasts on carried traffic alone would teach the model the network bottleneck rather than the underlying demand.

### 11. Why inject missing data, stale readings and resets?

Because a controller trained and tested on perfect counters is not operational evidence. The cleanup layer must detect missing buckets, reject stale values and never compute a rate across a counter reset. When freshness or counter semantics are uncertain, the recommendation must fall back to static.

## Forecasting

### 12. Why retain ridge when LightGBM is more accurate?

Because the predeclared release rule required every gate, not only average accuracy. LightGBM improved average error and peak detection but failed a worst-slice requirement. Ridge remained simple, auditable and had useful uncertainty coverage, so retaining it followed the rule rather than personal preference.

### 13. What does WAPE mean?

Weighted absolute percentage error is total absolute forecast error divided by total actual demand. A value of 7.63% means aggregate error is about 7.63% of aggregate actual volume on that evaluation set. It does not mean every group or event is within 7.63%, so worst-slice and event-regime checks remain necessary.

### 14. What is a conformal prediction bound?

It is an empirically calibrated safety margin derived from past forecast errors without assuming a particular error distribution. A p90 upper bound aims to cover approximately nine out of ten comparable outcomes. Coverage must be measured on unseen data; the name “p90” alone is not a guarantee.

### 15. How do you prevent forecast leakage?

Every telemetry bucket and derived feature carries an availability time. A forecast issued at time t may use only buckets that closed and were available by t. Training, baseline comparison and simulation enforce the same rule, and the notebook includes a direct causality assertion.

## Optimizer mathematics

### 16. What are the optimizer's decision variables?

The main variables are routing weights for every eligible traffic-group/UPF pair. For a group they are non-negative and sum to one. Some formulations add activation variables or explicit slack variables so infeasibility and capacity excess are represented rather than hidden.

### 17. What exactly is the optimizer minimizing?

The primary cost is overload relative to each UPF's safe envelope. Smaller terms discourage general utilization, cross-zone delay and abrupt changes in routing weights. Penalty weights express priorities, while hard constraints and the independent validator enforce eligibility, capacity, health, session and causality rules.

### 18. What is the difference between an LP and MPC?

The LP optimizes one decision step using the current state and forecast. MPC repeats a similar optimization over several future steps while tracking how existing cohorts of sessions survive. It applies only the first action, receives new telemetry and solves again; the future plan is provisional.

### 19. Why use an optimizer if static is already strong?

Because static is the reference that any more complex method must beat, and the oracle proves conditional headroom exists. Experiment 7 shows predictive methods can exploit declared capacity loss. Complexity is justified only in that bounded scope and only after all safety and evidence gates pass.

## Controller evidence

### 20. How can the oracle remove all overload?

The oracle sees the exact future arrivals and failures for the whole simulated day and solves jointly. That lets it avoid placing long-lived sessions on UPFs that will later lose capacity. It establishes an upper bound on what the steering lever could achieve; it is not a deployable controller.

### 21. Why does pre-drain help planned maintenance?

Advance notice allows it to reduce new placements on the affected UPF before its capacity falls. Existing sessions then finish naturally during the notice window. More notice gives more long-lived load time to drain, which is why maintenance scheduling can be more valuable than a more complicated forecast model.

### 22. Why can pre-drain make a mixed event worse?

It can move new sessions away from the announced failure and onto a UPF that later suffers an unannounced brownout or surge. Those sessions are already anchored when the surprise arrives. Cohort MPC reduces this exposure by spreading commitments more conservatively, but it does not eliminate it.

## Experiment 7 and safety

### 23. Why did Experiment 7 reverse the earlier conclusion?

An audit found three defects in the evaluator and scenario machinery: inconsistent overload units, non-finite scoring of exact ties at zero capacity, and an MPC horizon sized in windows rather than hours. The controllers themselves were not rewritten to manufacture a win. After corrections, the campaign was repeated with frozen rules and fresh validation seeds.

### 24. Does “0 regressions in 473 pairs” prove the controller is always safe?

No. It means zero regressions across the informative declared-maintenance and maintenance-plus-stadium pairs in the stated seed pools. The mixed maintenance-then-surprise-brownout family still contains regressions. Scope and stress family must always accompany the zero.

### 25. What does the headline +24.0% mean?

It is the held-out reduction in the predeclared uplink-overload measure relative to matched static runs, aggregated over the validation scope. It is not a 24% throughput increase, a 24% latency reduction, or a live-network result. The traffic, failures and capacities are synthetic.

## Operations and deployment

### 26. Why use a 10-minute cadence rather than two minutes?

In the matched comparison, two-minute control improved overload removal by only 0.02 percentage points while causing about 3.7 times the routing churn. Faster control is not free: it increases policy change, operational noise and compute pressure. Ten minutes was the better trade in this campaign.

### 27. Are you asking C-DOT to deploy this controller?

No. The recommendation is a bounded shadow-advisory replay using real C-DOT telemetry, with no live policy publication. The next inputs are the SMF steering key, maintenance-notice workflow, real UPF safe envelopes and telemetry/reset semantics. A separate review should decide whether evidence from shadow mode justifies any later pilot.

## Additional short answers

- **Why not reinforcement learning?** It has the same limited lever and is harder to validate. Resolve controllability, telemetry and safety first.
- **How does the cluster scale?** Across independent matched scenario/seed shards, not by spreading one normal simulation across 160 nodes.
- **What if the solver times out?** Reject the recommendation and retain the last safe static policy.
- **What if telemetry is stale?** Do not optimize from it; retain static and record the fallback reason.
- **Is reactive steering a safe alternative?** Not in these experiments. It reacted after load was committed and was substantially worse than static.
- **Why keep the negative experiments?** They are the audit trail showing which mechanisms failed and why the final conditional claim is credible.
