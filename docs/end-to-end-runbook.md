# End-to-end runbook

Status: operational guide with explicit implementation boundaries

Applies to: data generation, campaign evaluation, model training,
artifact handoff, and demo operation

Last reviewed: 2026-08-05

## 1. Read this first

The repository currently supports three executable workflows:

1. Run the deterministic simulator and controller campaigns locally or as
   independent PBS jobs.
2. Run the self-contained FastAPI and React demo on one presentation host.
3. Train a checksum-verified 10–80 minute forecast bundle from campaign Parquet
   and load it into the causal demo runtime.

The following release-packaging workflow is **not yet end-to-end executable**:

`accepted campaign + trained model + curated replay -> frozen release directory`

Commands in this document are marked as either **Implemented** or **Target
state**. Never run a target-state command expecting it to exist. The current
demo creates a local causal simulation in the FastAPI process; it does not
retrieve a live simulation from PBS. It loads either a copied cluster-trained
bundle or the explicitly labeled compact demo bundle.

See [system architecture decisions](system-architecture-decisions.md) for the
rationale and trust boundaries behind these procedures.

## 2. Host roles

| Host | Responsibility | Required during presentation? |
|---|---|---|
| Development/release workstation | Build, test, inspect, and freeze a release | No |
| PBS login node | Submit jobs and inspect immutable outputs | No |
| PBS compute node | Run one scenario/controller/seed shard per job | No |
| Demo host | Serve FastAPI, the built frontend, causal simulation, model, and audit data | Yes |
| Operator/audience browser | Use HTTPS/REST/WebSocket through the demo host | Yes |
| C-DOT Prometheus | Future source of production telemetry | No; external dependency |
| C-DOT SMF/EMS | Future target of a validated recommendation | No; external dependency |

Normal release rule: the cluster finishes first. A small, immutable artifact
bundle is then copied to the demo host. No queue, compute node, or cluster
network connection should be on the critical path of a presentation.

## 3. Compute sizing and when to use the cluster

One 16-week scenario contains:

- 112 simulated days;
- 322,560 ticks at 30-second resolution;
- 16,128 ten-minute decision boundaries; and
- about 8.5 million selection-audit records with the current arrival rates.

The Python macro engine is primarily single-shard and CPU-bound. Assigning 128
cores to one shard does not make the current engine 128 times faster. Parallel
capacity should be used across independent seeds, scenarios, and controllers.

Local measurements on 2026-08-05 used a Ryzen 9 9950X3D workstation after the
aggregate-cohort, indexed-event, bounded-Parquet, and hashing optimizations:

| Profile | Artifact scope | Simulated duration | Wall time | Peak RSS |
|---|---|---:|---:|---:|
| Standard three-UPF | In-memory static simulation | 1 week | 5.26 s | 352.3 MiB |
| Standard three-UPF | Complete campaign shard | 1 week | 20.20 s | 507.9 MiB |
| Extreme 16-million-UE with joint group/UPF buckets | Complete campaign shard | 1 day | 5:06.31 | 588.0 MiB |

A linear projection for one standard 16-week shard is about 1:24 in memory or
5:23 with artifacts. The calibrated extreme profile projects to 9:31:46 with
artifacts and is documented in
[extreme-training-runbook.md](extreme-training-runbook.md). JSONL and Parquet
serialization add time and disk, while retained step results make full-run
memory scale with duration. The joint-bucket calibration projects to 9:31:46
and about 64.3 GiB peak RSS, so reserve 12 hours and 96 GiB RAM on this
workstation. Run the capacity pilot in section 7 before fixing PBS requests.

Use the workstation for:

- development and correctness tests;
- one 16-week seed after a successful capacity pilot;
- a small number of scenario variants; and
- the presentation demo itself.

Use the cluster for:

- 30 or more matched seeds per controller;
- multiple event/fault/topology families;
- hyperparameter or model-candidate searches; and
- producing evidence quickly by running independent shards concurrently.

The 160-node, 128-physical-core-per-node cluster is therefore capacity for
campaign breadth, not a minimum requirement. A sensible first campaign uses
one job per shard and a small number of cores per job. Increase concurrency
only after confirming scheduler policy, storage throughput, quotas, and measured
per-shard resources.

The eventual forecasting table is also workstation-scale for this topology:
16,128 decision windows multiplied by six traffic groups is 96,768 group-window
rows before expanding targets and horizons. Conventional statistical models and
LightGBM should not require the full cluster. Use PBS for candidate/seed sweeps
only after a local training benchmark shows that parallelism is worthwhile.

Important scale limitation: `build_history_manifest` records
`nominal_ue_population: 30000`, but the current engine does not create or enforce
30,000 concurrent UEs. It generates session cohorts from each group's arrival
rate and holding time; the supplied steady-state means imply roughly 2,300
active sessions before event multipliers and capacity rejection. Treat 30,000
as target scenario metadata until a population-conservation model and a
statistical release test enforce it.

## 4. Repository and artifact layout

Run all commands from the repository root unless a command explicitly changes
directory.

```text
configs/                  frozen scenario and traffic-model inputs
demo_api/                 FastAPI service and built static UI
docs/                     architecture, traffic specification, and this runbook
experiments/              manifest, shard, aggregation, and evaluation CLIs
frontend/                 React/TypeScript/Vite source
output/manifests/         generated immutable scenario manifests
output/macro/             partitioned campaign outputs
pbs/                      PBS scripts and cluster preflight
scripts/                  local demo startup and preflight
tests/                    backend, schema, simulator, and controller tests
```

A campaign shard is published under:

```text
output/macro/schema_major=1/
  campaign=<campaign-id>/
    scenario=<scenario-id>/
      controller=<controller-version>/
        seed=<six-digit-seed>/
          run.jsonl
          run.parquet
          selection-audits.parquet
          metadata.json
```

`metadata.json` is the publication marker. It records the manifest hash, output
hashes, source commit, component versions, host, PBS identity, and summary.

## 5. Stage 0 — prepare and verify a release checkout

Status: **Implemented**

Record the source identity before generating artifacts:

```bash
git rev-parse HEAD
git status --short
```

Do not generate a release from an unexplained dirty worktree. Install backend
dependencies and run tests:

```bash
python3 -m venv env
env/bin/pip install -e .
env/bin/python -m unittest discover -s tests -v
```

Build the offline frontend:

```bash
cd frontend
npm ci
npm run build
cd ..
```

Run the demo preflight:

```bash
./scripts/preflight.py
```

Gate: tests and preflight pass, the Git identity is recorded, and the traffic
registry remains explicitly marked synthetic.

## 6. Stage 1 — freeze a 16-week scenario manifest

Status: **Implemented**

Review the input assumptions first:

- [traffic-model-spec.md](traffic-model-spec.md)
- [`configs/traffic_model_registry.json`](../configs/traffic_model_registry.json)
- [`configs/demo_scenario.json`](../configs/demo_scenario.json)

Create the history manifest atomically:

```bash
mkdir -p output/manifests logs/pbs output/macro
env/bin/python -m experiments.build_history_manifest \
  --template configs/demo_scenario.json \
  --output output/manifests/history-s20260805.json \
  --seed 20260805 \
  --start 2026-01-05T00:00:00Z
```

Validate the key invariants:

```bash
env/bin/python -c 'import json; p=json.load(open("output/manifests/history-s20260805.json")); print({"scenario":p["scenario_id"],"steps":p["steps"],"synthetic":p["corpus"]["synthetic"],"split":p["corpus"]["split"],"sha256":p["corpus"]["manifest_sha256"]})'
sha256sum output/manifests/history-s20260805.json
```

Expected `steps` is `322560`. Weeks 1–11 are training metadata, weeks 12–13
validation metadata, and weeks 14–16 test metadata. This command freezes a
scenario configuration; it does not generate telemetry yet.

Gate: retain the exact manifest and its external SHA-256 with the release
record. Changing seed, start time, template, or code creates a new artifact.

## 7. Stage 2 — run PBS capability and capacity preflight

Status: **Implemented**, but must be executed on the target cluster

Follow [`pbs/README.md`](../pbs/README.md) and submit the supplied probes:

```bash
qsub pbs/check_dependencies.pbs
qsub pbs/check_nodes.pbs
qsub pbs/check_build.pbs
qsub pbs/capability_probe_2node.pbs
qstat -u "$USER"
```

Review `logs/pbs/` and the generated capability records. Confirm:

- the queue and project/account convention;
- the Python environment and required packages on compute nodes;
- shared visibility of the repository and output root;
- scratch and project quota;
- allowed array width and queued-job limits;
- physical cores, memory, and wall-time policy; and
- whether atomic rename semantics are available on the output filesystem.

Before a full 16-week campaign, run one representative shard while recording
wall time, maximum RSS, and output size. The checked-in PBS shard currently asks
for only `1:ncpus=1:mem=4gb` and 30 minutes. The workstation benchmark indicates
that 4 GiB is likely too small for a 16-week result retained in memory. Adjust
the PBS resource request only after the pilot; do not launch the full array
with an unvalidated request.

Gate: a full 16-week shard completes, publishes `metadata.json`, passes hash
checks, and fits comfortably inside the selected resources. Target at most
70–80% of wall time and memory to absorb event and serialization variance.

## 8. Stage 3 — generate simulation data

### 8.1 Workstation smoke test

Status: **Implemented**

Run the short, 30-minute demonstration scenario:

```bash
env/bin/python -m simulator.macro.cli configs/demo_scenario.json \
  --controller predictive \
  --output output/demo/predictive.jsonl
```

Run one canonical campaign shard:

```bash
env/bin/python -m experiments.run_campaign_shard \
  --manifest configs/demo_scenario.json \
  --output-root output/macro \
  --campaign-id smoke-v1 \
  --controller predictive \
  --seed 1000
```

Inspect its `metadata.json` and confirm all four files exist before continuing.

### 8.2 One 16-week workstation shard

Status: **Implemented**, subject to the capacity gate

```bash
env/bin/python -m experiments.run_campaign_shard \
  --manifest output/manifests/history-s20260805.json \
  --output-root output/macro \
  --campaign-id history-16w-s20260805 \
  --controller static \
  --seed 20260805
```

This is an ordinary foreground process. Run it inside the site's approved job
or session manager if disconnect resilience is required. Do not use the same
campaign ID for a changed manifest.

### 8.3 PBS array campaign

Status: **Implemented for one controller per submission**

After updating the PBS resources from the capacity pilot, submit independent
seed shards:

```bash
MANIFEST=output/manifests/history-s20260805.json \
CAMPAIGN_ID=history-16w-static-v1 \
OUTPUT_ROOT=output/macro \
CONTROLLER=static \
SEED_START=1000 \
SHARD_COUNT=30 \
bash pbs/submit_campaign.sh
```

Monitor without modifying the outputs:

```bash
qstat -u "$USER"
find output/macro/schema_major=1/campaign=history-16w-static-v1 \
  -name metadata.json -type f | wc -l
```

The current submit script launches a dependent aggregation job after all array
members succeed. It accepts only one controller per submission. Do not launch
several controller submissions into the same campaign directory at the same
time: their dependent aggregation jobs do not coordinate the full controller
matrix. Use separate campaign IDs, or use the local paired runner in section 9,
until a paired PBS orchestrator is implemented.

Seed note: `run_campaign_shard` replaces the scenario seed with the shard seed.
The event schedule embedded in the frozen history manifest remains fixed. The
shard seed changes the stochastic arrival and lifetime streams, not that fixed
event schedule.

## 9. Stage 4 — aggregate and evaluate matched controllers

Status: **Implemented locally**

For the short demo scenario, run 30 exactly paired seeds across static,
reactive, and predictive controllers:

```bash
env/bin/python -m experiments.run_local_paired \
  --manifest configs/demo_scenario.json \
  --output-root output/macro \
  --campaign-id demo-v1 \
  --seed-start 1000 \
  --seed-count 30 \
  --skip-existing
```

Evaluate an existing complete campaign:

```bash
env/bin/python -m experiments.evaluate_paired \
  --root output/macro/schema_major=1/campaign=demo-v1 \
  --output output/macro/schema_major=1/campaign=demo-v1/paired-evaluation.json
```

The evaluator requires exactly paired seeds and exits successfully only when
its acceptance gates pass. Review UL and DL independently; the selected primary
metric does not authorize hiding regression in the other direction.

The repository does not yet provide a safe PBS command that schedules all
controller families into one shared paired campaign and publishes one final
evaluation after all families complete. For a 16-week, 30-seed, three-controller
campaign, this missing orchestrator is a release blocker—not a reason to run
competing aggregate jobs in one directory.

## 10. Stage 5 — train and freeze the forecaster

Status: **Implemented for the deterministic calendar-ridge bundle; full release
evaluation remains pending**

The trainer reads completed campaign `run.parquet` shards, closes canonical
10-minute observations per selection group, preserves shard chronology, and
fits direct models for arrivals, UL Mbps, and DL Mbps at every 10–80 minute
horizon. It freezes the coefficients, 70/15/15 ordered split, held-out metrics,
split-conformal widths, ACI parameters, source metadata, and SHA-256 in one JSON
artifact. It never uses a target window as an input feature.

Train from a completed static-controller history campaign:

```bash
env/bin/python -m experiments.train_forecaster \
  --campaign-root output/macro/schema_major=1/campaign=history-16w-static-v1 \
  --manifest output/manifests/history-s20260805.json \
  --controller static-capacity-v1 \
  --model-version calendar-ridge-conformal/1.0 \
  --output output/models/forecaster-v1.json
```

Verify that the artifact loads and its checksum matches its content:

```bash
env/bin/python -c 'from forecasting import TrainedForecastBundle as B; b=B.load("output/models/forecaster-v1.json"); print(b.metadata)'
```

The compact bundle shipped for rehearsals can be rebuilt deterministically with
`env/bin/python -m experiments.bootstrap_demo_forecaster`. It is labeled
`demo_calibrated_not_campaign_release` and must not be presented as the
16-week release result.

Training release gate:

- no feature has `available_at` later than forecast issue time;
- selection uses validation only; test stays untouched until final evaluation;
- p90 interval coverage is 85–95% on the declared release slice;
- non-event WAPE improves at least 10% over seasonal naive;
- held-out event performance is not materially degraded;
- every model, schema, metric, source manifest, code commit, and dependency is
  covered by the frozen manifest and checksums.

Stop here for an artifact-backed release until the declared campaign passes the
coverage, baseline, and held-out-event gates. The shipped compact bundle proves
the interface and closed loop, not those release claims.

## 11. Stage 6 — freeze the demo artifact bundle

Status: **Target state**

The intended immutable bundle is:

```text
demo-bundle-v1/
  manifest.json
  CHECKSUMS.sha256
  configs/
  schemas/
  replay/
  models/
  evaluation/
```

The bundle contains only curated replay windows, the accepted forecasting
bundle, comparison evidence, schemas, and scenario metadata. It should not
contain the entire research corpus.

The future freeze command should have an explicit input manifest and refuse
untracked or failed artifacts:

```bash
# TARGET INTERFACE — NOT RUNNABLE IN THE CURRENT REPOSITORY
env/bin/python -m experiments.freeze_demo_bundle \
  --campaign-root output/macro/schema_major=1/campaign=history-16w-v1 \
  --model output/models/forecaster-v1 \
  --replay-selection configs/demo_replays.json \
  --output output/bundles/demo-bundle-v1
```

Gate: the bundle is read-only, checksum-complete, and passes the release
preflight on a clean demo host without access to the full corpus.

## 12. Stage 7 — transfer cluster artifacts to another demo host

Status: **Operational pattern; bundle production is target state**

The normal architecture is a pull or release-controlled copy after cluster
jobs finish. Substitute site-approved hostnames and absolute paths:

```bash
rsync -a --partial --checksum \
  <cluster-login>:<absolute-path>/demo-bundle-v1/ \
  <demo-host-staging>/demo-bundle-v1/
```

On both ends, from the bundle root:

```bash
sha256sum -c CHECKSUMS.sha256
```

Then atomically change the demo host's configured bundle version or release
symlink using the site's deployment mechanism. Never copy into the directory
being served by an active demo process.

The demo host does not need to open a connection to PBS during the show. This
eliminates queue delay, firewall complexity, shared-filesystem dependency, and
partial-result races from the presentation path.

## 13. Stage 8 — run the current standalone demo

Status: **Implemented**

Build the frontend if it was not built in stage 0:

```bash
cd frontend
npm ci
npm run build
cd ..
```

For anything except a loopback-only rehearsal, set unique credentials and a
random signing secret:

```bash
export CDOT_DEMO_USER='<presenter-user>'
export CDOT_DEMO_PASSWORD='<strong-presenter-password>'
export CDOT_DEMO_SECRET='<random-secret-from-approved-secret-store>'
export CDOT_FORECAST_BUNDLE='<absolute-path>/forecaster-v1.json'
export CDOT_DEMO_HOST='0.0.0.0'
export CDOT_DEMO_PORT='8000'
./scripts/start-demo.sh
```

For a single-host rehearsal, omit the host/port variables and open:

```text
http://127.0.0.1:8000
```

Check service health and synthetic Prometheus exposition:

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health
curl -fsS http://127.0.0.1:8000/metrics | head
```

If browsers are on other machines, put the service behind the site's TLS
reverse proxy, permit only the required ingress port, and browse to the proxy's
HTTPS URL. The frontend uses same-origin REST and WebSocket requests; no
separate frontend-to-cluster rule is needed.

Current runtime behavior:

1. The presenter logs in and creates a run with a seed and controller.
2. FastAPI constructs a local incremental `Simulator`; no future tick exists yet.
3. Each 30-second tick is realized once with the policy valid for that tick.
4. After every 20 ticks, the just-closed bucket is added to model history; the
   frozen bundle forecasts 10–80 minutes, HiGHS proposes weights, and the policy
   gate applies, holds, or emergency-applies the candidate.
5. The accepted weights affect only subsequently arriving simulated sessions.
6. REST commands append events at the current simulator clock; past telemetry,
   cohorts, queues, and random streams are not recomputed. WebSocket deltas update all
   connected viewers in sequence.
7. Policy and diversion shown by the dashboard affect the local simulated
   result; they do not alter traffic on the PBS cluster or a C-DOT network.

Default `presenter` / `demo` credentials are for loopback rehearsal only.

## 14. How separate machines communicate

### 14.1 Current and recommended demo mode

```text
PBS cluster -- completed files/checksums --> release staging -- copy --> demo host
                                                                      ^
                                                                      |
browser ------------------- HTTPS REST + WebSocket --------------------+
```

There is no live cluster-to-dashboard stream. “Divert traffic” means the
validated policy changes subsequent session placement inside the deterministic
simulation or replay hosted with FastAPI. This is the correct presentation
mode because it remains deterministic and independent of cluster availability.

### 14.2 Future live telemetry mode

Status: **Adapter exists; runtime wiring is target state**

```text
C-DOT UPFs/exporters --> Prometheus <-- HTTPS queries -- demo/service host
                                                     |
                                                     v
                                  canonical FlowSource telemetry
                                                     |
                                      forecast -> optimizer -> validation
```

The `PrometheusFlowSource` adapter accepts configuration-driven queries, but
`DemoRun` does not yet select or poll it. A production deployment must add:

- adapter selection and label-to-canonical-ID mapping;
- TLS and credential handling through a secret store;
- firewall/routing from the service host to Prometheus;
- timeout, staleness, reset, restart, and missing-sample tests;
- bounded polling and backpressure; and
- an operator-visible fallback to the last safe policy.

The browser still communicates only with FastAPI. It must never query
Prometheus or the PBS cluster directly.

### 14.3 Future real traffic diversion

Status: **External dependency**

```text
validated recommendation --> advisory file --> human/operator       [available]
validated recommendation --> C-DOT SMF/EMS adapter --> live policy  [not available]
```

`SimulationActuator` and `AdvisoryFileSink` are implemented seams.
`SmfEmsActuator` is deliberately a placeholder and raises `NotImplementedError`.
Real new-session steering requires a supported, authenticated C-DOT SMF/EMS
interface with atomic publication, acknowledgement, read-back, audit, rollback,
and policy-version semantics. No documentation or dashboard animation should
be interpreted as proof that such actuation exists.

## 15. Presenter rehearsal

Perform at least one rehearsal using the exact release checkout and host:

1. Run backend tests, frontend build, and `./scripts/preflight.py`.
2. Start the service with non-default credentials.
3. Verify health, login, presenter token renewal, and viewer access.
4. Create the selected deterministic seed and predictive controller run.
5. Verify snapshot-first WebSocket connection and increasing sequence numbers.
6. Start, pause, resume, change speed, and reset.
7. Inject the stadium surge, capacity degradation, UPF failure, and telemetry
   gap one at a time.
8. Confirm the decision rail shows bucket, forecast, risk, optimization,
   validation, actuation, and realized outcome in order.
9. Confirm no policy routes new sessions to an unavailable or ineligible UPF.
10. Confirm an audience viewer cannot call presenter controls.
11. Disconnect and reconnect a viewer; it must receive a full current snapshot.
12. Rehearse the deterministic fallback with cluster networking disabled.

Presentation gate: the demo must work with no cluster dependency. Preserve the
accepted seed, browser route, credentials delivery method, and fallback steps in
the release record.

## 16. Troubleshooting

### A shard exceeds memory or wall time

- Do not request all 128 cores; the shard is not internally parallel at that
  scale.
- Record the tick, controller, maximum RSS, and output phase that failed.
- Increase memory/wall time for the next capacity pilot.
- Prefer implementing streaming/partitioned output before launching a large
  matrix if serialization is the peak.
- Never publish `metadata.json` for a partial result.

### `--skip-existing` refuses a shard

The existing manifest or one of the recorded output hashes differs. Preserve
the directory for investigation and use a new campaign ID. Do not overwrite it
or edit its metadata by hand.

### PBS array completes but aggregation does not

Inspect `qstat -f <job-id>` and `logs/pbs/`. The aggregation job uses an
`afterok` dependency; any failed array member prevents it from running. Rerun
only the missing deterministic shards, verify their metadata, then aggregate.

### Forecast is absent early in a run

The first forecast is issued only after the first complete 10-minute bucket.
Until then, the controller uses its explicit static fallback. Check
`CDOT_FORECAST_BUNDLE`, `/api/v1/artifacts`, and the model checksum if the
Forecast Studio says `runtime fallback` after the first bucket.

### A remote browser cannot connect

Check the bind address, reverse proxy, host firewall, TLS certificate, and
WebSocket upgrade forwarding. Test `/api/v1/health` from the browser network.
Do not expose the service with default credentials.

### Prometheus data does not appear

The adapter is not wired into the current demo runner. Proving that the adapter
class can query an endpoint is not the same as enabling live mode. Implement
and test runtime source selection before troubleshooting network data as a demo
feature.

### The dashboard shows diversion but network traffic does not change

That is expected in simulation mode. The current action changes local simulated
new-session placement. Use the advisory sink for a reviewable recommendation.
Live C-DOT diversion is unavailable until an SMF/EMS contract is supplied and
implemented.

## 17. Shutdown and evidence retention

Stop the foreground demo process with `Ctrl-C`. Retain:

- source Git commit and dirty-state record;
- scenario, registry, and external hashes;
- every accepted shard `metadata.json`;
- paired and forecasting evaluation reports;
- frozen bundle checksums when implemented;
- demo preflight output; and
- SQLite audit data according to the project's retention policy.

Do not retain presenter passwords or bearer tokens in manifests, logs, bundles,
or screenshots.

## 18. Remaining implementation checklist

The following work is required before claiming the complete target pipeline:

- enforce the 30,000-UE/cohort population model and release tests;
- stream or partition long-run outputs to bound memory use;
- benchmark and update the 16-week PBS resource request;
- add paired multi-controller PBS orchestration;
- expand forecast evaluation by class, zone, event/fault regime, and horizon;
- freeze and verify a curated immutable demo bundle;
- wire `FlowSource` and `ActuatorSink` selection into `DemoRun`;
- add artifact-backed `ReplayFlowSource` operation;
- configure and test live Prometheus ingestion; and
- implement real SMF/EMS actuation only after C-DOT supplies a supported API.
