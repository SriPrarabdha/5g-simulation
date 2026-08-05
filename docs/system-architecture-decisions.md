# System architecture decisions

Status: living architecture record

Applies to: synthetic corpus generation, forecasting, optimization, demo replay,
and future C-DOT integration

Last reviewed: 2026-08-05

## 1. System objective and boundary

The system demonstrates forecast-driven UPF traffic engineering while keeping
measurement claims, simulation claims, and production-control claims separate.
It has two execution environments:

1. The PBS cluster is an offline scenario and evidence factory.
2. The demo host is a deterministic, presentation-safe replay and control host.

The cluster is not designed to remain in the live presentation path. A complete
cluster run produces immutable data, evaluation, and model artifacts; those
artifacts are transferred to the demo host before rehearsal.

```text
OFFLINE EVIDENCE PLANE

traffic registry + scenario manifests
                │
                ▼
      PBS independent shards
                │
                ├── canonical Parquet telemetry
                ├── selection audits
                ├── reproducibility metadata
                └── campaign evaluation
                │
                ▼
     trained and calibrated model bundle     [implemented]
                │
       immutable/checksummed transfer
                ▼

PRESENTATION PLANE

browser ── REST/WebSocket ── FastAPI demo host
                                  │
                                  ├── replay/source adapter
                                  ├── forecasting and optimization
                                  ├── validated policy store
                                  ├── simulation actuator
                                  └── SQLite audit
```

Current implementation note: the demo host creates an incremental `Simulator`
and loads a checksum-verified model bundle. It advances exactly one 30-second
tick at a time. The shipped bundle is deliberately labeled as compact synthetic
demo calibration; an operator can replace it with a cluster-trained bundle via
`CDOT_FORECAST_BUNDLE`.

## 2. Stage map and ownership

| Stage | Primary host | Input | Output | Current status |
|---|---|---|---|---|
| Evidence and parameter freeze | Development/release host | Published sources | Frozen registry and cited specification | Implemented |
| Capability preflight | PBS login/compute nodes | Repository and environment | Capability records and logs | Implemented |
| Historical simulation | PBS compute nodes | Frozen scenario manifest and seed | JSONL, Parquet, audits, metadata | Implemented; 16-week production run not executed |
| Aggregation and paired evaluation | PBS or analysis host | Complete shards | Campaign summary and acceptance report | Implemented |
| Feature extraction | PBS/analysis host | Historical Parquet | In-memory leakage-safe chronological rows | Implemented in trainer |
| Model training and calibration | PBS/analysis host | Historical Parquet | Versioned forecasting bundle | Implemented; release campaign pending |
| Artifact freeze and transfer | Release host | Data/model/evaluation outputs | Immutable demo bundle | Target state |
| Accelerated causal demo | Demo host | Local scenario + frozen bundle | Ordered telemetry and decisions | Implemented |
| Browser visualization | Demo host + clients | REST and WebSocket events | Operator views | Implemented |
| Live Prometheus ingestion | Reachable telemetry network | Prometheus HTTP API | Canonical telemetry samples | Adapter implemented; runner wiring deferred |
| Real SMF/EMS actuation | Operator control network | Validated recommendation | Production policy change | External dependency |

## 3. Architecture decision records

### ADR-001 — Use the PBS cluster as an offline artifact factory

Decision: parallelize independent scenario/seed/controller shards across PBS
array jobs. Do not combine 160 nodes into one distributed mobile core.

Why:

- Independent shards scale with minimal coordination.
- Failures and retries remain isolated and deterministic.
- Common random numbers can match controller comparisons exactly.
- The stage demonstration does not depend on queue delay or cluster networking.

Consequence: a separate artifact-freeze and handoff step is required. The
cluster does not push live traffic into the dashboard during the normal demo.

Status: implemented for macro campaign shards. Production-scale capacity and
wall-time are not yet measured for a 16-week manifest.

### ADR-002 — Use immutable manifests and idempotent shard publication

Decision: identity is `(schema major, campaign, scenario, controller, seed)`.
Each shard writes data before atomically publishing metadata containing hashes,
Git commit, host, job ID, and summary.

Why:

- Retries cannot silently overwrite a different result.
- Results remain attributable to source, code, seed, and controller.
- Transfer verification can use SHA-256 rather than filenames alone.

Consequence: changing a manifest requires a new campaign ID or output root.
Existing validated shards may be reused only when every recorded hash matches.

Status: implemented in `experiments.run_campaign_shard`.

### ADR-003 — Keep simulation time at 30 seconds and control time at 10 minutes

Decision: source dynamics advance at 30-second ticks. Forecasting and policy
changes occur on half-open 10-minute buckets.

Why:

- Thirty seconds preserves counter faults, burst shape, and operational events.
- Ten minutes matches the intended traffic-engineering decision cadence.
- Half-open windows prevent double counting at exact boundaries.

Consequence: every bucket normally contains 20 source ticks. Completeness and
quality flags must accompany derived rates.

Status: implemented in the simulator and telemetry pipeline.

### ADR-004 — Simulate UEs as cohorts while retaining session state

Decision: maintain stateful cohorts with arrival time, lifetime, UPF placement,
and per-session demand. Persist aggregated telemetry rather than packets or a
record for every UE at every tick.

Why:

- A 16-week, 30,000-UE history would otherwise be unnecessarily large.
- Session anchoring and residual load still affect future control decisions.
- Cohorts preserve conservation and holding-time behavior at the required level.

Consequence: the macro simulator does not claim radio scheduling, GTP packet,
or packet-level latency fidelity.

Status: implemented.

### ADR-005 — Preserve all traffic accounting states

Decision: record offered, admitted, carried, queued, dropped, and rejected load
separately, in both UL and DL directions.

Why: overload must not disappear because a capacity-limited system carried less
traffic. Offered demand remains invariant under capacity changes when common
random numbers are used.

Consequence: acceptance reports review UL and DL separately. A combined average
cannot hide a directional failure.

Status: implemented and tested.

### ADR-006 — Keep 5QI as a forecast/QoS dimension, not an assumed selection key

Decision: demand is modeled by `(zone, DNN, S-NSSAI, 5QI)`. Production-relevant
UPF steering uses `(zone, DNN, S-NSSAI)` unless an operator interface explicitly
supports another key.

Why: 5QI affects demand and service requirements, but the project must not claim
that a C-DOT SMF selects UPFs directly by 5QI without interface evidence.

Consequence: policy schemas reject 5QI in the selection key, while telemetry and
forecast contracts retain it.

Status: implemented.

### ADR-007 — Use chronological and regime-level dataset separation

Decision: weeks 1–11 train, weeks 12–13 validate, and weeks 14–16 test by
default. Event templates, seeds, and fault regimes are indivisible holdout units.

Why:

- Random row splits leak adjacent time-series behavior.
- Variants of one event in both train and test exaggerate generalization.
- Validation, not test, must select ensembles and calibration parameters.

Consequence: every feature needs an `available_at` timestamp no later than the
forecast issue time.

Status: split metadata is emitted by the history-manifest builder. The bundle
trainer uses an ordered 70/15/15 split within each shard sequence and records it
in the artifact. Cross-seed event/fault holdout enforcement and the complete
release report remain pending.

### ADR-008 — Forecast per controllable group and horizon

Decision: forecast arrivals, offered UL/DL throughput, active sessions, and
residual load at p50, p90, and p95 for 10–80 minute horizons.

Why: the optimizer needs directional new-session demand and already-anchored
load, while scale-out needs enough horizon to cover spin-up delay.

Consequence: accuracy is reported by group, zone, class, regime, and horizon,
not only as a global average.

Status: seasonal-naive, moving-average, and LightGBM components remain
available. The implemented offline direct calendar-ridge trainer serializes all
eight horizons, split-conformal widths, ACI state, ordered split metadata, test
metrics, and a content checksum. A validation-selected multi-model ensemble is
still a later research extension.

### ADR-009 — Optimize safety-envelope risk, not raw throughput balance

Decision: minimize the maximum projected UPF operating index using directional
throughput, session capacity, health, locality/latency eligibility, slack, and
policy churn.

Why: equal Mbps does not mean equal risk on heterogeneous UPFs, and an otherwise
light UPF may be ineligible or too distant for a group.

Consequence: the optimization result must expose binding constraints and slack.
Structural infeasibility publishes no pretend normalized allocation.

Status: HiGHS uses the immediate p95 action forecast; the bundle and dashboard
expose all eight horizons for receding-horizon risk and future scale actions.
The full replica-cost MPC remains target state.

### ADR-010 — Steer new sessions with deterministic weighted rendezvous hashing

Decision: published weights influence only new-session placement. Selection is
deterministic for `(session identity, policy identity)` and limited to healthy,
eligible UPFs.

Why:

- Existing sessions are normally anchored.
- Rendezvous hashing provides repeatability and limited remapping.
- Requested and realized shares can be audited independently.

Consequence: effectiveness depends on the fraction of target-window load from
new sessions. The simulator may model bounded migration only when it is clearly
marked simulation-only.

Status: implemented for macro simulation. A real SMF hook is an external dependency.

### ADR-011 — Fail closed to the last safe policy

Decision: validate forecasts, state freshness, weights, health, eligibility,
latency, capacity, and churn before publication. On missing features, stale data,
solver failure, or infeasibility, retain the last safe policy and publish a reason.

Why: a missing recommendation is safer than routing to an invalid UPF.

Consequence: fallback is a first-class, operator-visible state and audit event.

Status: implemented in the controller/validator and demo actuator. The stateful
gate enforces minimum hold, objective hysteresis, per-group total-variation
churn, and a health/capacity emergency override; every decision is visible in
the dashboard.

### ADR-012 — Separate telemetry ingress and actuation egress

Decision: use `FlowSource.sample()` for telemetry and `ActuatorSink.apply()` for
recommendations. Supported source classes are synthetic, replay, and Prometheus;
supported sinks are simulation and advisory-file, with an explicit SMF/EMS
placeholder.

Why: replacing synthetic data must not require redesigning schemas, forecasting,
optimization, or the dashboard.

Consequence: adapters must translate external labels into canonical identifiers.
They cannot invent unsupported control semantics.

Status: interfaces and adapters exist. `DemoRun` currently bypasses `FlowSource`
and constructs a local simulator; runtime adapter selection is target state.

### ADR-013 — Use a single-process FastAPI orchestrator for the demo

Decision: one process owns run state, sequence numbers, simulation time, policy,
audit fan-out, and WebSocket subscribers.

Why:

- In-memory ordering is deterministic and easy to rehearse.
- It avoids distributed state during a stage demonstration.
- Snapshot-first reconnect is straightforward.

Consequence: horizontal scaling is out of scope. A process restart requires
reloading the deterministic replay or restoring run metadata.

Status: implemented.

### ADR-014 — Use REST for commands and snapshots; WebSocket for ordered deltas

Decision: presenter commands use authenticated REST. Each client first receives
a complete snapshot, followed by deltas carrying run ID, monotonic sequence,
simulated time, wall time, and schema version.

Why: commands need explicit responses and audit records; telemetry needs low
latency and ordering.

Consequence: reconnecting clients replace local state with the new snapshot
before applying further deltas.

Status: implemented.

### ADR-015 — Keep presenter and viewer authority separate

Decision: presenters can create runs, change controllers, inject events, and
start/pause/reset. Viewers can read state and subscribe but cannot mutate runs.

Why: audience clients must never alter a live presentation.

Consequence: presenter actions are written to SQLite audit records. Default
credentials are suitable only for a local rehearsal and must be overridden on a
networked host.

Status: implemented. Enterprise identity integration is external.

### ADR-016 — Use Parquet/DuckDB for analytics and SQLite for local control data

Decision: canonical offline data is Parquet. DuckDB provides root-scoped,
read-only analytics. SQLite stores local users/run metadata/audit records.

Why: these choices are embedded, offline-capable, and require no database
service for a demo host.

Consequence: large artifacts stay outside SQLite, and analytics paths cannot
escape the configured artifact root.

Status: Parquet output, DuckDB seam, and SQLite audit are implemented. Full
artifact catalog integration is target state.

### ADR-017 — Transfer bundles, not a live cluster stream

Decision: the normal demo-host handoff is an immutable directory containing the
curated replay, models, evaluation, schemas, manifest, and checksums. Transfer is
performed before rehearsal through an operator-approved mechanism such as
`rsync`, `scp`, or shared read-only storage.

Why:

- No PBS allocation is required during the presentation.
- The exact rehearsed artifact can be verified on the demo host.
- Network loss cannot interrupt simulated diversion.

Consequence: artifact identity must be pinned in demo configuration and checked
at startup. The runtime loads `CDOT_FORECAST_BUNDLE` or the shipped
`configs/demo_forecast_bundle.json` and rejects a checksum mismatch.

Status: model-bundle training and ingestion are implemented. Freezing the full
replay/evaluation/model release directory remains target state.

### ADR-018 — Treat live cluster streaming as a separate operating mode

Decision: if live telemetry is later required, the demo host queries a reachable
Prometheus HTTP API; PBS compute nodes do not open ad hoc dashboard connections.

Why: Prometheus provides bounded queries, buffering, authentication options,
and a known operational interface. PBS job stdout or shared-file polling is not
a telemetry protocol.

Consequence: network policy, DNS, TLS, credentials, query templates, retention,
and failure behavior must be approved. This mode remains advisory unless a real
actuator is also supplied.

Status: `PrometheusFlowSource` is implemented; configuration and runner wiring
are target state.

### ADR-019 — Make simulation and production actuation visibly different

Decision: local policy application may autonomously affect the simulator.
Production recommendations remain advisory until C-DOT supplies a supported,
authenticated SMF/EMS interface and rollback semantics.

Why: a Python placeholder is not evidence of a safe network-control API.

Consequence: `SmfEmsActuator.apply()` intentionally raises `NotImplementedError`.

Status: simulation/advisory sinks implemented; production sink external.

### ADR-020 — Never overstate synthetic evidence

Decision: all screens and artifacts disclose synthetic origin. Unrun campaigns,
uncalibrated forecast envelopes, and oracle results are labeled accordingly.

Why: a technically polished demonstration must not be mistaken for measured
C-DOT performance or accepted model evidence.

Consequence: the current demo reports zero accepted matched seeds until a real
campaign bundle is loaded.

Status: implemented in documentation and the dashboard.

## 4. Communication decisions for split-host deployment

### Normal presentation mode

There is no runtime communication with PBS. The cluster produces files; an
operator transfers a frozen bundle; FastAPI reads only the local copy. Browser
clients connect solely to the demo host.

Required network paths:

| Source | Destination | Protocol | Purpose |
|---|---|---|---|
| Operator workstation | Demo host | HTTPS/WSS, normally TCP 443 | Dashboard, REST, WebSocket |
| Release operator | Cluster/shared store and demo host | Approved SSH/rsync or mounted read-only storage | Pre-demo artifact transfer |
| Demo host | PBS compute nodes | None during presentation | Deliberately absent |

### Future live telemetry mode

| Source | Destination | Protocol | Purpose |
|---|---|---|---|
| Demo host | Prometheus endpoint | HTTPS | Instant/range queries |
| Demo host | SMF/EMS endpoint | Operator-defined authenticated API | Advisory or actuation |
| Browser | Demo host | HTTPS/WSS | Unchanged dashboard path |

The future live mode must not expose Prometheus or SMF credentials to the browser.

## 5. Current closed-loop semantics

The standalone demo is deterministic but not a packet-level online controller:

1. `DemoRun` owns one mutable simulator, cohort set, queue state, and seeded
   random-stream state.
2. A 30-second tick is generated only when the runner advances; realized steps
   are append-only.
3. At a 10-minute boundary the simulator closes the prior bucket before the
   forecaster can see it.
4. The frozen bundle issues group forecasts, HiGHS proposes immediate
   new-session weights, independent validation runs, and the stateful gate
   returns apply, hold, or emergency-apply.
5. Only the first policy action is committed. It changes rendezvous selection
   for future sessions; existing cohorts remain anchored.
6. Presenter surge/fault commands inject events at the next unrealized tick.
   Past steps, random draws, queues, and cohorts are never regenerated.
7. `SimulationActuator` records the current recommendation and fallback state.

Thus the displayed policy causally affects subsequent local telemetry, while
the runtime remains independent of PBS and sends no command to a C-DOT core.

### ADR-021 — Make the presentation loop causal and append-only

Decision: the demo runtime must never precompute a future result or rebuild the
past in response to an operator action.

Why:

- A policy animation is meaningful only if later session placement consumes it.
- Injected failures must preserve already-observed telemetry and random draws.
- Forecast features must become available only after their bucket closes.

Consequence: reset is the only operation that creates a new simulator. Surge,
fault, controller, speed, and gate changes mutate future behavior at the current
clock. Dashboard policy records include `applies_from_step` and
`history_recomputed: false`.

Status: implemented and covered by causal fault-injection and runtime tests.

## 6. Release gates

No cluster/model bundle is releasable until all of the following are recorded:

- immutable registry, scenario, Git commit, schema versions, and checksums;
- complete chronological train/validation/test assignments;
- leakage and telemetry-quality tests;
- forecast metrics by group, regime, and horizon;
- p90 interval coverage between the declared release limits;
- at least 30 exactly matched seeds for headline controller comparisons;
- separate UL and DL capacity results;
- eligibility, health, latency, and churn invariants;
- successful demo-host checksum and schema preflight;
- deterministic fallback replay; and
- explicit confirmation that any real actuator remains advisory or has an
  operator-approved rollback path.

The [end-to-end runbook](end-to-end-runbook.md) turns these decisions into
operator steps and clearly marks the currently unavailable stages.
