# Can predictive UPF steering beat the static controller?

Status: research conclusion and next-experiment design  
Date: 2026-08-06

## Executive conclusion

Yes, a dynamic controller can beat the static controller. The evidence does
not support the stronger claim that static capacity weighting is unbeatable.
It supports a narrower and useful conclusion:

> The present one-window LP, acting only on new sessions, does not beat static
> capacity weighting on the current event-dense validation scenario.

The distinction matters for four reasons.

1. The repository's earlier 30-seed `demo-v1` campaign already found a large
   reduction in UL overload area versus static. It regressed DL overload, so it
   is not the final controller, but it is a direct counterexample to general
   impossibility.
2. More than 99% of extreme-validation overload area is residual load from
   established sessions. After the dominant outage begins, the current
   actuator can only affect the roughly 3% of offered traffic belonging to new
   sessions at a tick.
3. Both validation seeds replay exactly the same fault targets, fault times,
   topology, and group definitions. They replicate traffic noise, not fault
   uncertainty. The same near-total airport-edge outage dominates both days.
4. Several intended follow-up mechanisms were not experiments of the claimed
   mechanism. In particular, advance notice is stored but not exposed before
   an event, and the 25% per-group cap is less diverse than static on a
   six-UPF eligible set.

The best next step is not more parameter tuning and not reinforcement learning.
It is a set of offline oracle bounds followed by a cohort-state, multi-period,
risk-aware controller that uses static as its default action. These bounds can
first determine whether the 20% gate is achievable without session migration.

## What the completed experiments actually establish

### Results that are sound

- The trained forecaster is accurate in normal periods but cannot anticipate
  an unannounced step surge: normal WAPE is 6.51%, surge WAPE is 11.08%, and
  surge p90 coverage falls to 30.73%.
- The v1 and v2 candidate profiles consistently lost to static on the two
  predeclared validation days.
- Lifetime weighting is directionally beneficial: it recovers 1.95 percentage
  points of UL regression and 1.29 points of DL regression relative to the
  identical 10% reference blend.
- The degradation is not a solver-failure artifact. It is a closed-loop
  placement effect: the dynamic policies leave more persistent load on the UPF
  that later suffers the near-total outage.
- Preserving the unused test seeds was the correct decision.

### Results that should not be generalized

- Two traffic seeds do not constitute two independent fault regimes. A direct
  manifest comparison shows identical capacity, health, and latency events,
  including the same failed UPFs and times.
- The results do not estimate performance under randomized outage target,
  onset, duration, severity, or correlated failure domain.
- The scheduled-hint result does not measure two-hour-ahead steering.
  `known_at_step` is validated but `_events_by_step` is indexed by the event's
  activation `step`; the hint multiplier changes only inside `_apply_events`
  when that activation step is processed. The test suite explicitly expects
  this activation-time-only behavior.
- The existing `OracleHiGHSController` is not an upper bound. It peeks only at
  arrivals in the next 10-minute interval and sends them through the same
  one-window LP. It has no future capacity path, cohort transitions, or outage
  knowledge.

## Empirical reconstruction of the loss

The machine-readable v2 artifacts were re-analysed at tick level.

### The dominant event

| Seed | Static total UL area | Static airport-fault UL area | Share |
|---|---:|---:|---:|
| 20260810 | 104,061 s | 103,258 s | 99.23% |
| 20260811 | 115,268 s | 114,123 s | 99.01% |

The stadium and industrial faults together contribute less than 1% of static
UL overload area. Even eliminating all non-airport overload would therefore
fall far short of the current 20% acceptance gate.

### Exposure immediately before the airport outage

At step 1319, immediately before `upf-edge-airport-a` loses 99% of its
capacity, its share of airport-zone active load was:

| Profile | Seed 20260810 UL/DL share | Seed 20260811 UL/DL share |
|---|---:|---:|
| Static | 8.80% / 8.80% | 8.78% / 8.78% |
| 10% reference blend | 9.30% / 9.11% | 9.48% / 9.25% |
| Lifetime-aware 10% blend | 9.21% / 9.04% | 9.35% / 9.16% |
| Scheduled-hint 10% blend | 9.29% / 9.11% | 9.59% / 9.50% |
| Combined 25% blend | 10.86% / 10.71% | 10.78% / 10.63% |
| “Diversified” 25% cap | 15.50% / 14.21% | 14.36% / 13.75% |

The ordering of exposure closely matches the ordering of outage loss. This is
the causal mechanism behind the result: once the outage occurs, those cohorts
cannot be moved.

### Why the diversification experiment was not robust diversification

Every group has six eligible UPFs. Static assigns airport groups approximately
8.7%, 8.3%, 16.4%, 16.4%, 25.1%, and 25.1% according to capacity. A constraint
of `weight <= 25%` only requires four non-zero destinations. It permits zero
weight on two UPFs and up to nearly three times static's weight on an edge UPF.

Consequently, the constraint:

- does not preserve all-six support;
- does not constrain aggregate active cohorts on an UPF;
- does not distinguish edge, regional, central, rack, site, or power failure
  domains; and
- does not optimize loss under capacity-failure scenarios.

A capacity-normalized exposure constraint, an entropy/effective-destination
floor, or explicit failure-scenario objective would test actual resilience.

## Implementation audit

### 1. The optimizer is myopic, not multi-period

`solve_allocation` has one allocation variable `p[group, upf]` for one target
window. It combines current aggregate residual load with the next window's new
load. It has no cohort age, survival transition, future allocation, future
capacity, or terminal-state value.

The lifetime follow-up multiplies each group's one-window demand by a relative
integrated-occupancy scalar. This changes which groups look expensive, but it
does not represent the future UPF on which today's cohorts will remain. It is a
useful heuristic, not a state-transition optimizer.

The simulator has enough hidden state to demonstrate the mismatch: it keeps
departures by step and group/UPF active cohorts, while the controller receives
only an aggregate `ResidualObservation` per UPF.

### 2. Scheduled information is not a future trajectory

The manifest includes `known_at_step`, but the engine exposes only a single
current multiplier and updates it at the event activation step. A correct
scheduled-event input needs at least:

- publication/known time;
- start and end time;
- affected groups or capacities;
- a future multiplier/capacity trajectory; and
- target-window intersection logic.

Simply exposing the current scalar two hours early would also be wrong: it
would multiply demand in every preceding 10-minute window as though the surge
had already started.

No advance capacity-fault trajectory is provided. Therefore scheduled demand
hints cannot perform pre-draining from a known maintenance outage.

### 3. Objective terms are not normalized to a common scale

The LP minimizes roughly:

`1 * maximum_utilization + 0.001 * sum(latency_ms * weight) + 0.01 * total_change + overload_slack`.

There are 96 groups. The maximum-utilization variable spans only `[0, 1]`, but
the aggregate locality improvement can exceed one objective unit and maximum
group-wise L1 churn can approach 1.92 objective units. Locality is also not
weighted by group traffic, so a tiny group has the same locality importance as
a high-rate group. This is not a lexicographic “avoid overload, then optimize
latency” objective.

The load-only profile shows that objective scaling is not the only problem—it
also lost badly because of persistent placements—but normalization should be
fixed before interpreting smaller parameter changes.

### 4. The policy gate does not certify improvement over static

The gate compares a candidate's one-window projected maximum utilization with
the previously active predictive policy. It does not compare the candidate
against a freshly evaluated static policy over the same future scenarios. A
sequence of individually accepted changes can therefore drift into a state
that is worse than static over the day.

For a lifetime-weighted solve, the post-blend projection and gate call use the
plain one-window `_project` function, not the same lifetime multiplier used by
the solver. The optimization and acceptance models are therefore different.

### 5. Forecast-window load semantics are conservative but inconsistent with
cohort survival

All sessions arriving anywhere in a 10-minute bucket are summed as new load,
while all currently active sessions are treated as residual load for the
target. Sessions that depart inside the target window are not removed, and
short-lived new sessions that depart before its end are not discounted. This
can be repaired naturally with cohort survival curves.

### 6. The synthetic benchmark strongly favors a robust static policy

The executable generator has large Poisson populations, fixed per-session
rates, uniform holding times, no mobility, and no within-session rate changes.
At this scale, ordinary arrival noise is relatively smooth. Static already
spreads every group over all six eligible UPFs in proportion to capacity.

Meanwhile, overload is created mainly by a scripted abrupt failure independent
of observable traffic. This leaves little causal opportunity for prediction:
normal periods are mostly uncongested, and the congested period begins with an
unpredictable loss of capacity carrying hours of state.

This benchmark is legitimate as a resilience test, but it is not by itself a
balanced test of forecast-driven admission steering.

## What standards, research, and implementations say

### Standards permit load- and prediction-aware UPF selection

3GPP TS 23.501, clause 6.3.3, explicitly lists dynamic UPF load, NWDAF load
statistics or predictions, relative static capacity, UPF/UE location, latency
requirements, topology, and local policy as inputs that an SMF may consider
for UPF selection and reselection. The standard leaves the exact algorithm
deployment-specific. This directly supports the architectural legitimacy of a
dynamic controller:

- [3GPP TS 23.501 Release 17, UPF selection](https://www.etsi.org/deliver/etsi_ts/123500_123599/123501/17.15.00_60/ts_123501v171500p.pdf)

The same specification includes redundant UP handling and redundant transport
path support among selection inputs. 3GPP TS 23.502 defines anchor-change
procedures, but continuity depends on SSC mode and procedure: SSC mode 2
re-establishes a PDU session, while SSC mode 3 establishes a new session before
releasing the old one. Standards support is not proof that the target C-DOT
build exposes arbitrary load-driven migration:

- [3GPP TS 23.502 Release 16, 5GS procedures](https://www.etsi.org/deliver/etsi_ts/123500_123599/123502/16.14.00_60/ts_123502v161400p.pdf)

### UPF-specific optimization work includes the state changes absent here

Leyva-Pupo et al. formulate dynamic UPF placement with explicit migration and
PDU-session reassignment costs. Their work is not a drop-in controller for this
simulator, but it confirms an important modeling point: placement
reconfiguration is a stateful, multi-objective decision, and omitting session
reassignment changes the reachable solution space. They report fewer latency
violations using event-triggered reconfiguration than periodic baselines:

- [Dynamic Scheduling and Optimal Reconfiguration of UPF Placement in 5G Networks](https://eprints.gla.ac.uk/223172/7/223172.pdf)

Queueing work on threshold-based UPF scaling models the number of sessions in
service and session holding rate, rather than optimizing only arrival volume.
It also identifies scaling as a second actuator when routing alone is
insufficient:

- [A Queueing Model for Threshold-Based Scaling of UPF Instances in 5G Core](https://doi.org/10.1109/ACCESS.2021.3085955)
- [Experimental evaluation of automated UPF scaling](https://www.slices-ri.eu/wp-content/uploads/On_the_Automated_Scaling_of_User_Plane_Function_for_5G_An_Experimental_Evaluation.pdf)

An open-source 2026 NWDAF/free5GC implementation has already demonstrated a
prediction-to-SMF-to-UPF closed loop using standard PDU Session Modification,
although its controlled variable is URR threshold rather than UPF placement.
This is evidence that the analytics and control plumbing is feasible, not that
its particular control law solves this problem:

- [A Machine Learning-Driven NWDAF Architecture for Intelligent 5G Core Networks](https://doi.org/10.1145/3733814.3765498)

### Production load balancers explain why static hashing is strong—and how it
is beaten

Google's Maglev combines even spreading, consistent hashing, and connection
tracking to remain stable during backend changes and faults. It is a close
analogy for why capacity-weighted rendezvous hashing is a formidable baseline:

- [Maglev: A Fast and Reliable Software Network Load Balancer](https://research.google/pubs/maglev-a-fast-and-reliable-software-network-load-balancer/)

Production systems beat static path hashing when they add an actuator that can
respond to established-flow congestion. CONGA uses real-time congestion
feedback and flowlet switching; Google's PLB changes paths for connections
that experience congestion, preferring idle periods to limit disruption. PLB's
reported deployment reduced busy-switch utilization imbalance by 60% and
drops by 33%:

- [CONGA: Distributed Congestion-Aware Load Balancing](https://people.csail.mit.edu/alizadeh/papers/conga-sigcomm14.pdf)
- [PLB: Congestion Signals are Simple and Effective for Network Load Balancing](https://research.google/pubs/plb-congestion-signals-are-simple-and-effective-for-network-load-balancing/)

These are transport-path mechanisms, not 5G PSA relocation. Their transferable
lesson is narrower: feedback can outperform static hashing when the system can
move at least some established traffic safely. Without that actuator, reaction
is limited to future arrivals.

Open-source 5G cores also show the implementation gap. free5GC exposes an SMF
function to select a UPF and allocate a UE IP, while SD-Core documents
multi-UPF and slice-based selection. Neither public interface is evidence of a
ready-made predictive, load-aware, arbitrary established-session migration
controller:

- [free5GC SMF selection API](https://pkg.go.dev/github.com/free5gc/smf/internal/context#UserPlaneInformation.SelectUPFAndAllocUEIP)
- [SD-Core/OMEC SMF](https://github.com/omec-project/smf)

## Ranked paths to beating static

### 1. Cohort-state robust model-predictive control — best match to the current
scope

Keep the actuator new-session-only, but optimize what it actually changes over
time.

For group `g`, UPF `u`, cohort age `a`, direction `d`, and decision time `t`,
maintain or estimate active cohort state `x[g,u,a,t]`. Let `p[g,u,t]` be the
fraction of new sessions routed to `u`. Use survival probabilities derived
from holding-time distributions:

```text
x[g,u,0,t+1]   = forecast_arrivals[g,t] * p[g,u,t]
x[g,u,a+1,t+1] = survival_ratio[g,a] * x[g,u,a,t]
load[u,d,t]     = sum(g,a) rate[g,d] * x[g,u,a,t]
```

Optimize a 2–4 hour receding horizon initially, with a terminal exposure
penalty for longer-lived cohorts. Apply only the first 10-minute allocation and
re-solve. The objective should be lexicographic or explicitly normalized:

1. session failures and rejected traffic;
2. expected plus tail-risk (for example CVaR) UL/DL overload area and drops;
3. failure-domain exposure;
4. latency/locality;
5. policy change.

Use capacity trajectories for known maintenance and scenario samples for
unannounced faults. A deterministic point forecast alone is insufficient.

### 2. Static-anchored robust admission with a certificate — fastest safe
candidate

Before the full MPC, build a smaller controller that defaults exactly to
static and permits a deviation only when it improves a multi-period
counterfactual under all declared capacity scenarios (or under an expected +
CVaR threshold). Compare candidate and static from the same observed cohort
state; do not compare only with the previous predictive policy.

Useful constraints are:

- capacity-normalized deviation from static, not a universal 25% cap;
- aggregate active-cohort exposure per UPF and failure domain;
- a minimum effective number of destinations;
- traffic-weighted locality cost; and
- a terminal penalty for long-lived load.

This hybrid can equal static when there is no certified opportunity and depart
only during predictable ramps, planned maintenance, or measurable imbalance.

### 3. Correct scheduled draining — high value, narrow applicability

Represent future demand and capacity events as trajectories known from a
specific publication time. For a planned outage at `T`, reduce admissions to
the affected UPF before `T` according to the probability that a newly admitted
session would still be alive at `T`. This can beat static for scheduled
maintenance without migrating sessions, but it cannot solve the unannounced
airport outage used in the present benchmark.

### 4. Bounded established-session relocation — highest control authority,
deployment-dependent

If C-DOT confirms support, add a separately versioned actuator with migration
budgets, SSC/PDU-session constraints, interruption and loss cost, cooldowns,
rollback, and audit. This is the closest 5G analogue to flowlet/path changes in
production load balancers. It should not be assumed from generic 3GPP
possibility.

### 5. Autoscaling or standby UPF capacity — changes the capacity ceiling

If the platform can instantiate capacity faster than the congestion ramp,
joint routing and scaling can beat any controller limited to a fixed capacity
pool. Cold-start time, state synchronization, IP pools, PFCP association,
traffic ramp, and scale-in safety must be modeled. It will not rescue an
instantaneous outage unless spare capacity and failover paths already exist.

### 6. Redundant user-plane handling for a critical subset — resilience, not
general load balancing

1+1 paths or redundant UP handling can protect selected URLLC traffic, at the
cost of duplicate capacity. It is appropriate for reliability objectives but
is unlikely to reduce aggregate overload when applied indiscriminately.

### 7. Reinforcement learning — defer

RL does not repair missing actuator authority, a one-fault validation design,
or an uncalibrated simulator. It is more likely to learn the scripted failure
schedule than to discover a deployable policy. Revisit only after offline
oracle bounds, randomized fault regimes, and a state/action model are frozen.

## Decisive experiment sequence

All stages below use only current validation data or newly generated
development scenarios. Reserved release-test seeds remain untouched until a
profile passes the declared gate.

### Stage A: compute action-space upper bounds

Build a full-horizon continuous LP or min-cost-flow-style oracle from the
known simulator arrivals, lifetimes, rates, capacities, and events.

Run four bounds on both validation days:

1. **Arrival oracle, no fault knowledge, new sessions only.** Perfect arrivals,
   causal capacity observation.
2. **Scheduled-fault oracle, new sessions only.** Learns only declared planned
   faults at their publication times.
3. **Clairvoyant-fault oracle, new sessions only.** Knows the full capacity
   path from day start. This is non-deployable but gives the best possible
   result for the current actuator.
4. **Clairvoyant oracle with bounded migration.** Quantifies the incremental
   value of migration authority.

Decision rule: if bound 3 cannot reach 20% without guardrail regressions, the
20% gate is mathematically incompatible with new-session-only control on this
scenario. Change the product objective or actuator, not the optimizer tuning.

### Stage B: repair experiment semantics

- Make `known_at_step` publish a future trajectory without activating it.
- Add independently declared knowledge times for scheduled capacity events.
- Randomize fault target, start, duration, and severity as indivisible
  scenario-level draws.
- Stratify validation by normal, predictable ramp, scheduled maintenance,
  unannounced brownout, unannounced outage, and latency incident.
- Vary topology overlap and failure domains, not only traffic Poisson draws.
- Keep exactly paired controller randomness inside each scenario draw.

At least 20–30 fault-scenario draws are needed before claiming robustness. A
small aggregated/cohort simulator can screen policies cheaply before running
the full 30-second, 16-million-UE implementation.

### Stage C: establish stronger baselines

Compare against:

- current static capacity weighting;
- directional demand-aware static weighting;
- capacity-normalized least-loaded admission;
- power-of-two least-loaded admission at the group-policy level;
- static plus scheduled drain;
- robust static with declared UPF failure hazards; and
- the four offline oracle bounds.

If the advanced optimizer cannot beat these, forecasting sophistication is not
the bottleneck.

### Stage D: validate one frozen MPC profile

Tune horizon, terminal penalty, risk weight, and static-deviation budget only
on validation scenarios. Freeze one profile before using fresh test seeds.
Report:

- total and event-stratified UL/DL overload area;
- dropped, rejected, and carried bytes;
- establishment failures;
- pre-fault active exposure per UPF/failure domain;
- controllable and residual overload decomposition;
- latency/locality cost;
- policy variation and solver fallback rate; and
- expected, p95, and worst-scenario paired differences versus static.

The release gate should require no directional regression and a confidence
interval, not only a mean improvement. If 20% remains a product requirement,
the oracle must first demonstrate that 20% is reachable.

## Recommended immediate backlog

1. Implement the four oracle bounds before another predictive profile.
2. Correct scheduled-event semantics and add future capacity trajectories.
3. Expose group/UPF cohort-age state or survival summaries to controllers.
4. Add a multi-period HiGHS formulation with a terminal exposure penalty.
5. Add stochastic fault scenarios and aggregate failure-domain constraints.
6. Replace the policy gate with a same-state candidate-versus-static robust
   certificate.
7. Normalize objectives and weight locality/churn by carried demand and a
   declared unit value.
8. Re-run on randomized development faults; freeze only after a pass.
9. In parallel, obtain the build-specific C-DOT decision on session relocation
   and UPF scale/failover interfaces.

## Bottom line

Static wins here for understandable reasons: it is already capacity-aware,
uses all eligible UPFs, is stable, and is robust to stale or incorrect
forecasts. The dynamic controller gives up some of that diversification to
optimize a ten-minute picture, while its choices persist for hours and the
benchmark's loss comes almost entirely from a surprise failure.

That is not a dead end. It identifies the missing ingredients precisely:
multi-period cohort state, future capacity semantics, failure risk, a stronger
static-relative safety gate, and—if the required improvement exceeds the
new-session-only oracle bound—more actuator authority.

## Implementation update — 2026-08-07

The Stage A evaluator is now implemented and the decisive conditional has been
resolved. On both existing validation days, the clairvoyant-fault continuous
new-session-only relaxation exceeds the 20% UL gate without modeled DL/drop
regression; in fact, it reduces modeled UL overload to numerical zero. The gate
is therefore reachable in the current action space on these scenarios.

The causal and scheduled regimes remain unstable across traffic seeds because
the formulation has many pre-fault zero-loss optima with radically different
failure exposure. This strengthens the recommendation for cohort-state MPC,
terminal exposure, and robust failure-diversity tie-breaking. See
[Extreme validation oracle-bound results](extreme-oracle-bound-results.md) for
the method, approximation check, complete results, and reproduction command.
