# C-DOT presentation guide

## Recommended seven-minute story

### 1. Start with the decision, not the algorithm

Show [`01_campaign_verdict.svg`](figures/01_campaign_verdict.svg).

> We ran 160 controller profiles across 20,000 paired mixed-stress scenarios.
> The safety system worked, and declared-maintenance gains are real, but zero
> profiles cleared every release gate. Static remains the production choice.

### 2. Establish that this is a scientific progression

Show [`06_experiment_journey.svg`](figures/06_experiment_journey.svg).

> We first verified the twin, forecasting and lifecycle estimation, then used
> oracle bounds to establish headroom. Only after that did we test controllers.
> Every negative result narrowed the mechanism we need next.

### 3. Show why average gain is insufficient

Show [`02_gain_vs_tail_risk.svg`](figures/02_gain_vs_tail_risk.svg).

> Fifty-six arms exceeded the 10% mean-gain threshold, but only 64 stayed inside
> the tail threshold—and no arm occupied the joint promotion region. The best
> headline gains carried the largest independent-outage tail.

### 4. Show where the mechanism genuinely works

Show [`03_family_heatmap.svg`](figures/03_family_heatmap.svg).

> Declared maintenance is the strong positive family. Maintenance plus a later
> stadium surge also benefits because pre-drain acts before both stresses. An
> independent destination outage is different: previously committed sessions
> persist, so later Static fallback cannot undo their placement.

### 5. Close the faster-cadence question

Show [`04_cadence_comparison.svg`](figures/04_cadence_comparison.svg).

> Holding the observation window fixed, 2-minute pre-drain adds only 0.084
> percentage points of mean gain and creates 3.59 times the routing churn.
> Two-minute MPC times out on every known-event solve. Ten minutes remains the
> defensible cadence for the current mechanism.

### 6. End on engineered safety and the next mechanism

Show [`05_guard_action_funnel.svg`](figures/05_guard_action_funnel.svg).

> The guard is selective by design, and pure surprises return exact Static in
> every arm. The remaining research problem is not another blend setting. It is
> reversible recourse: short-lived commitments, bounded migration, or an
> explicit reserve that survives independent destination failure.

## Questions likely from C-DOT

**Are we beating Static reliably?**  
Only within the declared-maintenance and maintenance-plus-stadium discovery
families. We are not reliably better across the complete mixed-stress contract.

**Which configuration is best?**  
There is no promotable best. Arm 60 is the clearest mechanism showcase, not a
deployment candidate. Arm 3 is tail-safe but delivers only 0.99% overall mean
gain. Arm 4 reaches 10.16% mean but has a −47.46% worst pair.

**Did the guard fail?**  
It passed its exact-Static and capacity-slack invariants. Its continuation model
did not provide a universal guarantee against a later outage after persistent
sessions had already been admitted. That is a control-authority limitation, not
future peeking or an unsafe fallback.

**Why not validate the best-looking arm?**  
Validation is for a frozen profile that first passes development gates. None
did. Spending protected seeds after a failed development gate would weaken the
scientific result.

**Why not run faster?**  
The measured 2-minute effect is negligible for pre-drain and operationally
unsuccessful for MPC. Faster decisions do not migrate already established
sessions.

**What do you need from C-DOT next?**  
Canonical UPF identity, SMF weight semantics, per-class session create/delete
counters, 5QI, bytes and packets, lifecycle/age buckets, calibrated UL/DL/session
capacity, and explicit maintenance-notice metadata.

## Language discipline

Use “synthetic shadow-controller discovery,” “mechanism evidence,” and
“paired with Static.” Avoid “production improvement,” “release candidate,” or
“optimizer winner.”
