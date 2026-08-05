# C-DOT 5G Predictive UPF Steering: Simulation and Demo Architecture

**Version:** 2.0
**Date:** 5 August 2026
**Status:** Implementation baseline for Demo v1

## 1. Objective and scope

Demo v1 has one testable objective:

> Predict demand for the immediately upcoming 10-minute window and steer newly established PDU sessions across eligible UPFs, demonstrating reduced overload against static and reactive baselines.

The first release is a closed-loop engineering proof, not a production controller. It uses three UPFs, two zones, two DNNs, two slices, and at least two independently controllable groups. It does not migrate existing sessions or load-balance individual packets.

The C-DOT cluster is a scenario factory. Its 160 nodes run independent simulation, forecasting, optimization, and, where privileges permit, isolated high-fidelity replicas. The nodes are not combined into one distributed 5G network.

### 1.1 Success claim

For paired runs with identical scenario seeds, the predictive controller must:

- reduce overload duration or overload area by at least 20% versus static placement;
- show no regression versus the reactive baseline on the selected primary overload metric;
- cause no increase in failed or rejected session establishments; and
- publish only policies that pass all safety and validity checks.

Results must also identify regimes in which low session churn makes new-session steering ineffective. Synthetic results demonstrate engineering feasibility only. Production claims require calibration and shadow evaluation against C-DOT telemetry.

### 1.2 Non-goals for v1

- migration or re-anchoring of active PDU sessions;
- packet-by-packet balancing across stateful UPFs;
- treating CPU or memory utilization as a substitute for calibrated capacity;
- assuming that 5QI is available to the SMF before UPF selection;
- treating free5GC Traffic Influence as a general weighted load-balancing API;
- treating 5G-LENA as the multi-UPF 5GC under test; or
- joining cluster nodes into a single emulated mobile network.

## 2. Architecture

The system has two execution planes joined by versioned data contracts.

~~~text
HIGH-FIDELITY PLANE

PacketRusher ----> free5GC control plane ----> UPF-1 / UPF-2 / UPF-3 ----> DNs
                       ^                         |
UERANSIM smoke tests   |                         +--> N3/N6 telemetry
                       |
                 SMF selection hook
                       ^
                       |
                  atomic policy

MACRO/HPC PLANE

scenario manifest --> 30 s session-cohort simulator --> canonical Parquet
                                                        |
                             bucket aggregation <--------+
                                      |
                                  forecast
                                      |
                                  HiGHS LP
                                      |
                               policy validator
                                      |
                          controller evaluation/audit
~~~

The same Forecast, UPFState, Policy, and SelectionAudit contracts are used in both planes. Only telemetry and enforcement adapters differ.

## 3. Decision timing

### 3.1 Normal one-step operation

Let T be a UTC-aligned 10-minute boundary. At T:

1. Close the completed bucket [T−10 min, T).
2. Use only observations with event time earlier than T.
3. Forecast demand for [T, T+10 min).
4. Read a fresh UPFState, solve the allocation problem, and validate the result.
5. Atomically activate the policy for [T, T+10 min).

Steps 1–5 must complete within 60 seconds of T. The evaluation records each component's latency and total telemetry-to-policy latency. A late policy is rejected rather than silently applied to the wrong window.

Prometheus scrapes every 30 seconds. The aggregator may use a short, fixed watermark allowance inside the 60-second budget, but it must never incorporate data from the target window into its forecast features.

~~~text
... [T-10 min, T) ................. [T, T+10 min) ...
        completed bucket       forecast, solve, activate
                                      <= 60 s
~~~

### 3.2 Deployments requiring a full-window actuation lead

If the enforcement path needs ten minutes of lead time, the controller must explicitly use a two-step target:

- at T, close [T−10 min, T);
- forecast [T+10 min, T+20 min); and
- publish the result with valid_from = T+10 min.

The already published policy remains active for [T, T+10 min). Configuration must state forecast_horizon_steps = 2. The system must never label a two-step forecast as “next window” or silently skip [T, T+10 min).

## 4. Demand, traffic, and session semantics

The simulator and schemas keep the following quantities distinct:

| Quantity | Meaning |
|---|---|
| Offered UL/DL demand | Bytes or bits applications attempt to send before UPF congestion |
| Carried N3/N6 traffic | Traffic successfully forwarded across the measured interface |
| Queued traffic | Offered work retained for later service |
| Dropped traffic | Offered work discarded after admission or while queued |
| Rejected traffic | Demand not admitted, including failed new sessions |
| Active sessions | Sessions alive during the sample or bucket |
| New sessions | Sessions established during the bucket |
| Existing residual load | Next-window load from sessions anchored before the decision |

Carried throughput is congestion-limited and is not a demand label. Offered demand must come from the traffic generator, application/session model, or an equivalent source-side measurement. The high-fidelity plane records both generator-side offered bytes and UPF-side carried bytes.

For every controllable group g, the forecaster produces:

- new-session arrivals Âg for the target window;
- UL and DL bandwidth generated by those arrivals, D̂new,UL,g and D̂new,DL,g; and
- UL/DL residual load plus surviving-session count for sessions already anchored to each UPF.

The next-window load controllable by v1 is:

\[
\kappa =
\frac{\sum_g(\hat D^{new,UL}_g+\hat D^{new,DL}_g)}
{\sum_u(\hat R^{UL}_u+\hat R^{DL}_u)+
 \sum_g(\hat D^{new,UL}_g+\hat D^{new,DL}_g)}
\]

Report κ overall and per group. It is a workload property, not a controller achievement.

### 4.1 Session model

Each generated session has:

- stable, pseudonymous session identifier;
- arrival time and lifetime;
- zone/site, DNN, and S-NSSAI;
- one or more QoS attributes, including 5QI when known;
- time-varying offered UL/DL demand;
- anchored UPF and establishment outcome; and
- departure or failure time.

The macro simulator advances in deterministic 30-second steps and represents sessions as cohorts when individual simulation is unnecessary. Cohorts preserve arrival time, lifetime distribution, group, anchored UPF, UL/DL demand distribution, and random-stream state. Congestion is applied only after offered demand is generated.

Workloads include daily and weekly seasonality, stochastic bursts, crowd-event ramps, churn, long-lived sessions, UPF failures/capacity degradation, link degradation, and telemetry faults. Showcase crowd events must generate enough new sessions to make steering observable. Long-lived-session scenarios deliberately expose the v1 control limitation.

## 5. Controllability and metadata

### 5.1 Selection key

The v1 UPF-selection group is:

~~~text
(zone/site, DNN, S-NSSAI)
~~~

This key is limited to information the selected SMF integration can observe before choosing a UPF. 5QI remains a traffic/QoS attribute and may be a forecasting dimension when reliable. It becomes part of the selection key only after an integration test proves that it is available at selection time for all relevant session paths.

Topology configuration is the source of truth for group-to-UPF eligibility, locality, and latency limits. A group with no healthy eligible UPF is a structural infeasibility, not a zero-weight policy.

### 5.2 Session and TEID metadata

Maintain a time-versioned mapping from TEID and PDU-session identifiers to:

- pseudonymous session identifier;
- selected UPF and interfaces;
- zone/site and gNB;
- DNN and S-NSSAI;
- QoS-flow attributes, including 5QI where observed;
- mapping validity interval; and
- policy ID used at establishment.

Prometheus series use only bounded labels such as UPF, interface, direction, and health state. Do not export every session or every possible DNN/slice/zone/5QI combination. Detailed session, TEID, selection, and per-group records are written to partitioned Parquet and joined offline.

## 6. Telemetry pipeline

### 6.1 Raw collection

Collect, at minimum:

- cumulative N3/N6 UL/DL byte and packet counters;
- generator-side offered UL/DL bytes;
- carried, queued, dropped, and rejected bytes/packets;
- active and newly established sessions;
- session-establishment failures;
- UPF health and restart identity;
- directional effective capacity and session capacity; and
- diagnostic CPU, memory, queue, and path-latency measurements.

External interface or TC/eBPF telemetry is preferred where core-specific metric semantics are incomplete. CPU, memory, and queue metrics are model features and diagnostics until saturation testing establishes their relation to capacity.

### 6.2 Counter reconstruction

For two valid samples of a cumulative counter C at times ti−1 and ti:

\[
r_i = \frac{8(C_i-C_{i-1})}{t_i-t_{i-1}}
\]

The implementation must:

1. sort and deduplicate by source event time and sample identity;
2. verify matching source, unit, interface, counter identity, and reset_epoch;
3. compute a rate only when the delta is non-negative and elapsed time is valid;
4. invalidate an interval spanning a reset, restart, or unknown discontinuity;
5. mark uncovered expected scrape time as missing; and
6. retain late samples for audit while honoring the bucket watermark.

Never average raw byte or packet counters. Never interpret a negative delta as traffic. A restart or reset begins a new reset_epoch; it must not create an artificial spike.

### 6.3 Ten-minute bucket

Rates are assigned by covered event-time duration to half-open buckets [start, end). Each completed DemandBucket preserves, for every metric:

- time-weighted mean;
- p95 and maximum of valid 30-second rates;
- covered and expected duration;
- missing fraction;
- reset count; and
- restart status/count.

Traffic fields separately store offered, carried, queued, dropped, and rejected UL/DL quantities. Session fields store mean/max active sessions, new sessions, surviving sessions, departures, and establishment failures. Missing values remain missing; they are not silently converted to zero.

Prometheus is the online collection system. Versioned Parquet is the canonical offline corpus.

## 7. Versioned data contracts

All records carry schema_version, scenario_id or deployment_id, event/measurement time, producer version, and traceable units. Schema changes follow backward-compatible minor versions and breaking major versions.

### 7.1 TelemetrySample v1

~~~yaml
schema_version: "telemetry-sample/1.0"
sample_id: string
event_time: timestamp_utc
received_time: timestamp_utc
source: {type: string, id: string}
metric: string
dimensions:
  upf_id: string|null
  interface: n3|n6|null
  direction: ul|dl|null
value: number|null
unit: bytes_total|packets_total|bytes|packets|sessions|ratio|milliseconds
is_counter: boolean
reset_epoch: int64|null
valid: boolean
validity_flags: [string]
restart_id: string|null
~~~

### 7.2 DemandBucket v1

~~~yaml
schema_version: "demand-bucket/1.0"
window: {start: timestamp_utc, end: timestamp_utc}
group: {zone: string, dnn: string, snssai: string, five_qi: int|null}
upf_id: string|null
traffic:
  offered_ul_bytes: int64|null
  offered_dl_bytes: int64|null
  carried_ul_bytes: int64|null
  carried_dl_bytes: int64|null
  queued_ul_bytes: int64|null
  queued_dl_bytes: int64|null
  dropped_ul_bytes: int64|null
  dropped_dl_bytes: int64|null
  rejected_ul_bytes: int64|null
  rejected_dl_bytes: int64|null
sessions:
  active_mean: number|null
  active_max: int64|null
  new: int64|null
  surviving: int64|null
  departed: int64|null
  establishment_failures: int64|null
qos:
  latency_p95_ms: number|null
  latency_max_ms: number|null
rate_statistics:
  # mean, p95, and max for each reconstructed directional rate
data_quality:
  missing_fraction: number
  reset_count: int64
  restart_count: int64
  restarted: boolean
  late_sample_count: int64
  validity_flags: [string]
~~~

### 7.3 Forecast v1

~~~yaml
schema_version: "forecast/1.0"
forecast_id: string
issued_at: timestamp_utc
source_window_end: timestamp_utc
target_window: {start: timestamp_utc, end: timestamp_utc}
horizon_steps: 1|2
group: {zone: string, dnn: string, snssai: string, five_qi: int|null}
new_session_count: {p50: number, p90: number, p95: number}
new_load_mbps:
  ul: {p50: number, p90: number, p95: number}
  dl: {p50: number, p90: number, p95: number}
existing_load_by_upf:
  - upf_id: string
    surviving_sessions: {p50: number, p95: number}
    ul_mbps: {p50: number, p95: number}
    dl_mbps: {p50: number, p95: number}
model_version: string
quality_flags: [string]
~~~

### 7.4 UPFState v1

~~~yaml
schema_version: "upf-state/1.0"
measurement_time: timestamp_utc
upf_id: string
capacity_mbps: {ul: number, dl: number}
safe_utilization: {ul: number, dl: number}
session_capacity: int64
session_safe_utilization: number
health: healthy|degraded|unavailable|unknown
zone: string
eligible_groups: [string]
path_latency_ms_by_zone: {zone_id: number}
state_ttl_seconds: int
calibration_version: string
~~~

### 7.5 Policy v1

~~~yaml
schema_version: "policy/1.0"
policy_id: string
policy_version: int64
created_at: timestamp_utc
validity: {from: timestamp_utc, until: timestamp_utc}
forecast_id: string
upf_state_time: timestamp_utc
solver:
  name: highs
  status: optimal|feasible_with_slack|infeasible|timeout|error
  runtime_ms: int64
constraint_slack:
  ul_mbps_by_upf: {upf_id: number}
  dl_mbps_by_upf: {upf_id: number}
  sessions_by_upf: {upf_id: number}
groups:
  - key: {zone: string, dnn: string, snssai: string}
    weights: {upf_id: number}
fallback: {used: boolean, reason: string|null, source_policy_id: string|null}
validator_version: string
~~~

### 7.6 SelectionAudit v1

~~~yaml
schema_version: "selection-audit/1.0"
timestamp: timestamp_utc
session_id_hash: string
session_hash_value: string
group: {zone: string, dnn: string, snssai: string}
eligible_upfs: [string]
requested_weights: {upf_id: number}
selected_upf: string|null
policy_id: string|null
reason: optimizer_weighted|fallback_last_safe|fallback_static|no_eligible_upf
~~~

Parquet datasets are partitioned by schema major version, campaign/deployment, date, scenario, and seed where appropriate. Every experiment shard also records git commit, component versions, configuration hashes, host/job ID, and random seed.

## 8. Forecasting

Use chronological feature generation and splits. A feature at T may depend only on records with event time earlier than T. Hold out scenario templates, seeds, demand regimes, and selected topologies in addition to time ranges so a model cannot memorize event shapes.

Required baselines:

1. seasonal naive;
2. moving average; and
3. LightGBM quantile models for p50, p90, and p95.

Useful inputs include lagged offered demand, new-session arrivals, surviving sessions, time-of-day/week, event indicators available before T, mobility/zone transitions, recent growth, and data-quality flags. Carried traffic may be a feature but never replaces the offered-demand target.

Evaluate arrival and UL/DL load forecasts separately using MAE/WAPE where defined, pinball loss, and empirical quantile coverage. Report performance by ordinary, crowd-event, failure, missing-telemetry, and long-lived-session regimes.

## 9. Capacity model and calibration

Each UPF has independent:

- UL throughput capacity;
- DL throughput capacity;
- active-session capacity;
- health state;
- supported DNN/S-NSSAI combinations; and
- zone-to-UPF locality/latency limits.

N3 and N6 measurements are retained separately for diagnosis. The optimization capacity definition must name the bottleneck it represents and must not add N3 and N6 byte counts as if they were independent demand.

PacketRusher saturation sweeps vary session count, offered UL/DL rates, packet-size distribution, CPU allocation, and traffic mix. A calibrated UPFState identifies the sustained operating envelope before unacceptable loss, latency, or session failures. Calibration produces separate directional and session safe limits with a version and confidence range.

The macro simulator applies:

\[
Q^d_u(t+\Delta t)=
\max(0,Q^d_u(t)+O^d_u(t)-C^d_u(t)\Delta t)
\]

for direction d ∈ {UL, DL}, followed by configured queue limits, drops, and carried traffic. Session admission has an independent capacity and failure rule. Capacity degradation and failure events change effective capacity and health before service is calculated.

## 10. Linear optimizer

### 10.1 Decision and inputs

\[
p_{g,u}\in[0,1]
\]

is the probability that a new session in selection group g is assigned to eligible UPF u.

Inputs for the target window are:

- forecast new-session count Âg;
- forecast new UL/DL load D̂d,g from those sessions;
- residual UL/DL load R̂d,u and surviving sessions Ŝu from existing sessions;
- directional safe capacities Kd,u;
- safe active-session capacity Nu;
- eligibility, health, and locality/latency constraints; and
- previous policy pprev,g,u.

Use a declared planning quantile, initially p95 for showcase safety evaluation. The same quantile choice applies consistently to demand and residual forecasts.

### 10.2 Projected load

\[
L^d_u=\hat R^d_u+\sum_g p_{g,u}\hat D^d_g
\]

\[
N^{active}_u=\hat S_u+\sum_g p_{g,u}\hat A_g
\]

The implementation may replace the second expression with a calibrated expected number of arrivals still active at window end, but it must document that survival factor.

### 10.3 Constraints

\[
\sum_{u\in E(g)}p_{g,u}=1 \quad \forall g
\]

\[
p_{g,u}=0
\quad\text{when u is ineligible, unavailable, or violates a hard latency limit}
\]

Introduce z ∈ [0,1] as maximum safe-envelope utilization and non-negative overload slacks sUL,u, sDL,u, and sN,u:

\[
L^{UL}_u \le zK^{UL}_u+s^{UL}_u
\]

\[
L^{DL}_u \le zK^{DL}_u+s^{DL}_u
\]

\[
N^{active}_u \le zN_u+s^N_u
\]

Absolute policy changes are linearized with auxiliary variables $c_{g,u}$ satisfying $c_{g,u} ≥ p_{g,u}-p^{prev}_{g,u}$ and $c_{g,u} ≥ p^{prev}_{g,u}-p_{g,u}$.

### 10.4 Objective and status

The first implementation uses the HiGHS linear-programming solver:

\[
\min
M\sum_u\left(
\frac{s^{UL}_u}{K^{UL}_u}+
\frac{s^{DL}_u}{K^{DL}_u}+
\frac{s^N_u}{N_u}\right)
+\lambda_z z
+\lambda_l\sum_{g,u}\ell_{g,u}p_{g,u}
+\lambda_c\sum_{g,u}c_{g,u}
\]

M is chosen and tested to dominate maximum-utilization, locality, and churn costs. Locality cost ℓ is finite only for allowed paths; hard eligibility and latency limits remain constraints.

The result status is:

- optimal when all capacity slacks are zero within tolerance;
- feasible_with_slack when the LP solves but projected overload remains;
- infeasible for missing eligible/healthy paths or solver-proven infeasibility;
- timeout or error otherwise.

Capacity slack is surfaced in the Policy and dashboards. A feasible_with_slack policy may be published only under an explicitly configured degraded-mode rule. An infeasible, timed-out, or malformed result is never renormalized into an apparently valid policy.

## 11. Policy publication, fallback, and enforcement

### 11.1 Validator

Before publication, independently verify:

- supported schema and monotonic policy version;
- exact target validity interval and activation lead;
- no stale, expired, or overlapping policy;
- finite weights in [0,1] summing to 1 within tolerance;
- weights only on eligible, healthy UPFs;
- current UPFState within its TTL;
- directional and session projections reproduced from policy inputs;
- solver status and allowed slack; and
- configured churn/hysteresis limits.

Publish atomically with compare-and-swap on policy_version. Readers see either the complete old policy or complete new policy, never a partial update.

Fallback order is:

1. the last known safe policy, if still valid for the group and current health/eligibility state;
2. static capacity-weighted placement across currently healthy eligible UPFs; or
3. explicit session rejection when no eligible UPF exists.

Every fallback is recorded in Policy and SelectionAudit.

### 11.2 Deterministic weighted rendezvous selection

For a stable session key k and every eligible UPF with weight wu > 0:

1. compute a cryptographic hash of (k, policy_id, upf_id);
2. map it deterministically to Uu in the open interval (0,1);
3. compute scoreu = −ln(Uu)/wu; and
4. select the UPF with the smallest score, using upf_id as the deterministic tie-breaker.

The session key is built from stable, pseudonymized session data such as SUPI hash, PDU-session ID, DNN, and S-NSSAI. Existing sessions remain anchored when a policy changes.

For each group/window report requested weights, realized session counts/shares, realized offered load shares, and sampling error. With at least 100 new sessions in a group/window, realized session share must be within 10 percentage points of the requested share for every destination.

### 11.3 free5GC integration boundary

free5GC documents multiple UPFs, ULCL, multiple slices/DNNs, and Traffic Influence. Its Traffic Influence examples use traffic filters and DNAI/application routes and may influence UPF (re)selection; they do not establish a general per-group weighted session-balancing API. Demo v1 therefore implements a custom new-session selection hook at the SMF integration point. See [free5GC features](https://free5gc.org/guide/features/) and [Traffic Influence](https://free5gc.org/guide/8-traffic-influence/).

Traffic Influence, ULCL, application-path steering, and selected existing-session modification remain post-v1 experiments.

## 12. Simulation stack

### 12.1 High-fidelity plane

- **free5GC:** one control plane and three UPFs.
- **PacketRusher:** session and user-plane load, crowd-event generation, and saturation sweeps.
- **UERANSIM:** small functional smoke tests for registration, PDU-session establishment, DNN/slice configuration, and end-to-end reachability.
- **Prometheus:** 30-second collection from generator, UPF interfaces, session state, and host diagnostics.
- **Custom SMF hook:** deterministic weighted selection for new sessions.

Bring-up order is one UPF first, then three. Do not begin weighted steering until registration, PDU sessions, N3/N6 traffic, teardown, and independent UPF counters pass.

### 12.2 Macro plane

Implement a deterministic Python/NumPy discrete-time simulator with:

- 30-second steps and half-open time intervals;
- explicit random seeds and independent random streams;
- session arrivals, lifetimes, cohorts, and churn;
- offered UL/DL demand before congestion;
- directional capacity, queues, carried load, drops, and rejects;
- active-session capacity;
- health, capacity, topology, and latency events;
- scrape gaps, late samples, counter resets, and restarts;
- controller plug-ins using the same Policy contract; and
- canonical Parquet plus experiment metadata.

Re-running the same manifest, seed, and component versions must reproduce the same outputs byte-for-byte where library/runtime determinism permits, otherwise within documented numeric tolerances.

### 12.3 Optional RAN calibration

5G-LENA is post-v1 and may calibrate traffic, QoS, and RAN effects. Its feature matrix states that EPC/5GC integration is via the LTE-EPC model, so it is not the multi-UPF 5GC being controlled here. See the [5G-LENA feature matrix](https://5g-lena.cttc.es/features/).

## 13. Cluster capability gate

Before scheduling any high-fidelity replica, Stage 0 records a pass/fail/unknown matrix for:

- PBS Pro version and job-array behavior;
- Docker, Podman, or Apptainer/Singularity support;
- TUN/TAP creation and network namespaces;
- SCTP availability;
- GTP5G kernel/module availability;
- CAP_NET_ADMIN;
- eBPF load/attach permissions;
- inter-node UDP/TCP reachability on allocated nodes;
- shared scratch capacity/quotas; and
- job-local SSD/NVMe or temporary storage.

The probe must be a reproducible PBS Pro job and must clean up only resources it created.

Deployment choice:

- **Privileged pass:** run isolated free5GC replicas only on suitable allocated nodes. Each replica has its own namespaces, ports, addresses, storage, and scenario ID.
- **Privilege failure or uncertainty:** keep free5GC, UPFs, and load generators on a dedicated privileged machine. Use all cluster nodes for macro simulation, forecasting, optimization, and optional ns-3 campaigns.

The cluster never acts as one distributed 5G core for this experiment.

## 14. Experiment design

### 14.1 Controllers

Run all controllers against identical manifests and random streams:

1. static deterministic hash;
2. reactive threshold placement;
3. forecast plus capacity-proportional heuristic;
4. forecast plus constrained HiGHS optimizer; and
5. oracle-demand optimizer as an upper bound, never as a deployable result.

Paired runs share session arrivals, lifetimes, offered demand, failures, and telemetry-fault schedules. Controller-specific random choices are eliminated by deterministic hashing.

### 14.2 Showcase scenarios

At minimum:

- a Zone-A crowd event with at least 100 new sessions per affected group/window;
- the same event with a directional UL bottleneck;
- a UPF capacity degradation during a demand ramp;
- missing scrapes plus counter reset/restart;
- low churn with long-lived high-bandwidth sessions; and
- a topology/locality constraint that removes a tempting remote UPF.

Run at least 30 paired seeds per showcase scenario. Report paired effect sizes and bootstrap 95% confidence intervals.

### 14.3 Metrics

Primary control metrics:

- overload duration above the calibrated safe envelope;
- overload area, the time integral of utilization beyond the safe envelope;
- dropped/queued/rejected UL and DL demand;
- session-establishment failure rate; and
- p95/p99 latency where the model is calibrated.

Control and operational metrics:

- controllable-load fraction κ;
- requested versus realized placement shares;
- policy changes and total-variation churn;
- locality/latency cost;
- solver status, capacity slack, and runtime;
- aggregation, forecast, validation, publication, and total latency; and
- fallback and stale-policy counts.

Forecast metrics are reported separately and never substituted for closed-loop outcomes.

### 14.4 Leakage controls

Train/validation/test splits are chronological. Test data also holds out selected:

- scenario templates;
- random seeds;
- demand regimes and event magnitudes; and
- topology/capacity configurations.

Preprocessing statistics and model selection use training data only. Oracle demand is isolated in the evaluator and cannot enter deployable controller features.

## 15. Verification and acceptance

### 15.1 Unit and contract tests

- Counter-to-rate reconstruction error is below 1% in fault-free synthetic tests.
- Resets, restarts, missing/late/duplicate samples, and bucket boundaries never create artificial spikes.
- Every bucket retains mean, p95, maximum, missing fraction, reset count, and restart status.
- Offered demand remains unchanged when capacity is reduced; carried, queued, dropped, and rejected quantities change consistently.
- Schema fixtures round-trip without unit or timestamp ambiguity.
- The simulator reproduces a fixed golden manifest and seed.

### 15.2 Optimizer and policy tests

- Hand-solvable topologies match expected allocations and load projections.
- UL, DL, session, health, eligibility, and hard-latency constraints are independently exercised.
- Deliberately impossible eligibility returns infeasible and publishes no normalized solver policy.
- Deliberate capacity shortage returns explicit slack and degraded status.
- Every published policy passes independent normalization, eligibility, health, capacity, freshness, overlap, and expiry validation.
- Concurrent readers never observe a partially published policy.
- Small three-UPF/two-zone optimization completes in under one second.

### 15.3 Integration and statistical acceptance

- Full bucket-close-to-policy latency is under 60 seconds.
- With at least 100 new sessions per group/window, realized shares meet the 10-percentage-point allocation tolerance.
- Thirty or more paired seeds are used for every showcase scenario.
- Demo v1 reduces overload duration or area by at least 20% versus static placement, does not regress versus reactive placement, and does not increase session failures.
- Low-churn scenarios explicitly report low κ and show when new-session steering cannot remove residual overload.

No acceptance statement may combine UL and DL into a single passing average when either directional constraint fails.

## 16. Implementation sequence and gates

1. **Architecture and contracts:** approve this v2, freeze v1 schema fixtures, and choose the primary overload metric.
2. **Capability probe:** select privileged or split deployment.
3. **Macro simulator:** deterministic sessions/cohorts, offered and carried traffic, directional/session capacity, and Parquet output.
4. **Fault/event library:** crowd events, long-lived sessions, UPF/link failures, and telemetry faults.
5. **Forecasting:** seasonal-naive, moving-average, and LightGBM quantile baselines with leakage tests.
6. **Optimization/control:** HiGHS LP, validator, atomic publication, fallback, rendezvous hashing, and audit records.
7. **One-UPF smoke test:** registration, PDU session, N3/N6 traffic, and telemetry.
8. **Three-UPF calibration:** PacketRusher saturation sweeps for directional and session limits.
9. **SMF hook:** weighted deterministic selection and requested/realized share logging.
10. **Closed-loop evaluation:** paired controllers and 30-seed showcase campaigns.
11. **Cluster scale-out:** independent scenario arrays and reproducible aggregation.
12. **Post-v1:** 5G-LENA calibration, ULCL/Traffic Influence, and research on existing-session changes.

Each step produces versioned artifacts and must pass its tests before the dependent step begins.

## 17. Recommended repository layout

~~~text
configs/               topology, capacities, traffic, events
schemas/               six versioned contracts and fixtures
simulator/macro/       deterministic 30 s simulator
telemetry/             exporters, adapters, aggregation
forecasting/           baselines, features, inference
optimization/          HiGHS model and solution reports
steering/              validator, policy store, SMF hook, hashing
core/free5gc/          isolated high-fidelity deployment
generators/            PacketRusher and UERANSIM configurations
experiments/           manifests, PBS Pro jobs, paired evaluator
calibration/           saturation sweeps and fitted envelopes
output/                immutable partitioned Parquet results
~~~

The topology IDs and group keys in configs are canonical. Adapters may translate external names but no subsystem invents its own identifiers.

## 18. Demo handoff

The demo presents static, reactive, and predictive runs side-by-side using the same seed. It shows:

- offered versus carried UL/DL demand;
- residual versus new-session load;
- κ, active sessions, new sessions, and failures;
- directional capacity and overload area;
- forecast quantiles and actual target-window demand;
- requested and realized UPF shares;
- solver status, slack, active/fallback policy, and latency; and
- confidence intervals across the paired campaign.

The correct v1 conclusion is conditional:

> Forecast-driven new-session steering reduces overload when enough target-window load comes from newly arriving sessions and eligible UPF headroom exists. It cannot rebalance load already anchored in long-lived sessions.

## References

1. [free5GC feature support](https://free5gc.org/guide/features/)
2. [free5GC Traffic Influence documentation](https://free5gc.org/guide/8-traffic-influence/)
3. [5G-LENA feature matrix](https://5g-lena.cttc.es/features/)
