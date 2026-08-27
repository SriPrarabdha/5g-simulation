# C-DOT predictive UPF steering simulation

This repository implements the deterministic macro/HPC simulator and a
self-contained closed-loop Demo v1. The presentation host advances synthetic
30-second telemetry through forecasting, optimization, policy validation, and
simulated actuation, with a FastAPI service and a production-built React
operator console. External free5GC/PacketRusher validation, calibrated C-DOT
UPF envelopes, and an SMF selection hook still require a suitable privileged
5G test environment.

## Documentation

- [`docs/delhi-presenter-guide.md`](docs/delhi-presenter-guide.md) — the
  45-minute Delhi run of show, evidence language, live/offline fallback, and
  rehearsal checklist. The additive deck package is in `presentation/delhi/`.
- [`docs/README.md`](docs/README.md) — documentation map and implementation
  status language.
- [`docs/workshop-facilitator-guide.md`](docs/workshop-facilitator-guide.md) —
  90-minute interactive lab timing, materials, fallbacks, evidence language,
  rehearsal, and acceptance checks.
- [`docs/system-architecture-decisions.md`](docs/system-architecture-decisions.md)
  — stage-by-stage architecture decisions, boundaries, and communication paths.
- [`docs/end-to-end-runbook.md`](docs/end-to-end-runbook.md) — workstation and
  PBS data generation, current training limitations, artifact transfer, demo
  startup, split-host operation, rehearsal, and troubleshooting.
- [`docs/traffic-model-spec.md`](docs/traffic-model-spec.md) — cited synthetic
  traffic assumptions and unsupported-assumption boundary.
- [`docs/extreme-training-runbook.md`](docs/extreme-training-runbook.md) — the
  calibrated 16-million-UE, 8–12-hour forecast-training workload.
- [`docs/extreme-data-spec-and-cdot-gap-analysis.md`](docs/extreme-data-spec-and-cdot-gap-analysis.md)
  — exact generated schemas/model, public scale comparison, requirement gaps,
  and C-DOT meeting checklist.
- [`docs/extreme-forecaster-v1-results.md`](docs/extreme-forecaster-v1-results.md)
  — frozen v1 forecast results, baseline evidence, limitations, improvement
  plan, and predictive-optimizer handoff.
- [`docs/extreme-optimizer-pilot-results.md`](docs/extreme-optimizer-pilot-results.md)
  — one-day event-dense controller comparison and full-campaign decision.
- [`docs/cdot-session-migration-decision.md`](docs/cdot-session-migration-decision.md)
  — established-session control boundary and C-DOT confirmation checklist.
- [`docs/cohort-mpc-development-results.md`](docs/cohort-mpc-development-results.md)
  — frozen oracle handoff and the first same-state-certified cohort MPC result.
- [`docs/cohort-mpc-pilot-results.md`](docs/cohort-mpc-pilot-results.md)
  — historical failed-profile evidence and the replacement rationale.
- [`docs/cohort-mpc-full-campaign-results.md`](docs/cohort-mpc-full-campaign-results.md)
  — 30-seed static/MPC campaign, 10% demo gate, tail-risk boundary, and demo
  handoff.
- [`docs/extreme-optimizer-tuning-results.md`](docs/extreme-optimizer-tuning-results.md)
  — event-regime forecast evidence and optimizer-profile validation outcome.

## Implemented

- Six versioned v1 contracts with frozen, round-trip JSON fixtures.
- Deterministic 30-second cohort simulation with independent random streams.
- Offered, carried, queued, dropped, and rejected directional traffic.
- Session admission, lifetimes, residual load, health/capacity degradation,
  crowd-arrival factors, and path-latency events.
- Counter reconstruction and half-open event-time buckets that reject resets,
  restarts, gaps, negative deltas, duplicates, and post-watermark samples.
- Seasonal-naive, moving-average, and LightGBM p50/p90/p95 forecasting, plus
  MAE, WAPE, pinball loss, and empirical coverage metrics.
- The HiGHS LP with UL, DL, session, eligibility, health, latency, overload
  slack, locality, policy-churn, lifetime weighting, and per-group
  diversification terms.
- Auditable causal scheduled-demand hints and a closed-history anomaly
  fallback for unannounced surges.
- Independent policy validation, degraded-mode gating, and atomic in-process
  compare-and-swap publication.
- Static, reactive, forecast-capacity, predictive-HiGHS, and non-deployable
  oracle controllers using deterministic weighted rendezvous selection.
- A causal two-hour cohort-state MPC controller with known-capacity paths,
  terminal exposure, diversification, and same-state static certification.
- A completed 30-seed, four-scenario paired campaign: 10.52% mean-pair UL
  overload-area improvement, 2.84% severity-weighted improvement, and all
  aggregate guardrails passing.
- Canonical nested Parquet run data including compact 10-minute joint
  UPF/group demand snapshots, selection-audit Parquet, reproducibility
  metadata/hashes, PBS arrays, and exact paired bootstrap evaluation.
- Frozen, cited synthetic traffic-model registry with an explicit evidence and
  unsupported-assumption boundary.
- Accelerated closed-loop run lifecycle, deterministic seed selection,
  presenter/viewer roles, SQLite audit, safe-policy fallback, and ordered
  versioned WebSocket snapshots and deltas.
- A seeded 100-tick Live Dashboard with three scheduled episodes, one causal
  surprise, four decision-cycle outcomes, and exact rewindable checkpoints.
- Replaceable synthetic/replay/Prometheus `FlowSource` and
  simulation/advisory/SMF-placeholder `ActuatorSink` interfaces.
- Prometheus-compatible synthetic metrics and REST interfaces for topology,
  telemetry, forecasts, policy, decision traces, model metadata, and matched
  campaign comparisons.
- A five-destination React operations console served offline by FastAPI, with
  a routing-led synthetic dashboard, 3D twin, frozen Evidence, Technical
  Detail, and the isolated Live C-DOT console.
- An isolated `/live-cdot` console and `/api/v1/cdot-live/*` plane that reads
  closed Prometheus windows, forecasts carried traffic in explicitly
  uncalibrated pps-proxy units, runs one-step HiGHS allocation, and requires
  presenter review before verified h2c `/upf-admin` writes or exact rollback.
- An unattended closed loop (the autopilot) that streams C-DOT's live
  Prometheus on a fast poll, logs the health of that API per scrape, and every
  ten minutes forecasts, solves, and writes the resulting per-UPF weights to
  their SMF with GET verification -- holding the write whenever the telemetry
  is stale, the API is unhealthy, or a presenter is mid-review.

The selected primary metric for the supplied demo is directional UL overload
area (`overload_area_seconds.ul`). DL results are still reported separately and
cannot be hidden by a combined passing average.

## Not yet implemented

- One-UPF and three-UPF free5GC deployment and smoke tests.
- PacketRusher saturation sweeps and fitted directional/session capacities.
- The free5GC SMF new-session selection hook and external atomic policy store.
- An untouched production-release campaign with positive severity-weighted
  confidence bounds and no material fault-seed tail regressions.
- Optional post-v1 5G-LENA, ULCL, and Traffic Influence experiments.

Those items depend on the Stage-0 capability result and access to the target
free5GC and cluster environment; the repository does not claim they passed.

## Setup and tests

```bash
python3 -m venv env
env/bin/pip install -e .
env/bin/python -m unittest discover -s tests -v
```

Build and run the self-contained demo:

```bash
npm --prefix frontend ci
./scripts/start-demo.sh
```

The live plane defaults to Prometheus `http://192.168.218.8:29090` and SMF
`http://192.168.218.8:30956`. Override them with
`CDOT_PROMETHEUS_URL` and `CDOT_SMF_URL`; timeouts, poll/freshness intervals,
queries, UPF/job/pod/SMF identities, and proxy limits are also configurable via
the `CDOT_LIVE_*` variables in `demo_api/cdot_live/config.py` or a replacement
`CDOT_LIVE_CONFIG`. The default limits in `configs/cdot_live.json` are frozen
v02 p99 observations, not calibrated capacities. No live write occurs during
status, snapshot, polling, or evaluation; only a confirmed presenter apply or
rollback can POST to the SMF.

### Running the closed loop against C-DOT's live plane

The autopilot is the background job that keeps running on this machine: it
polls their Prometheus every `telemetry_poll_seconds` (30 s) and every
`control_interval_seconds` (600 s) forecasts, solves, and POSTs new per-UPF
weights to their SMF, verifying each write with a GET. Every poll is logged
with its latency, how many series Prometheus returned, and how many survived
label normalisation, so "the API is down" and "the API is up and answering
with zero matching series" -- the failure their unconfirmed metric names invite
-- never look alike.

Inside the dashboard, so `/live-cdot` shows the loop as it runs:

```bash
CDOT_LIVE_SOURCE=prometheus CDOT_LIVE_AUTOPILOT=1 ./scripts/start-demo.sh
```

Headless, when the loop should outlive the console:

```bash
python -m demo_api.cdot_live.runner \
    --prometheus http://192.168.218.8:29090 --smf http://192.168.218.8:30956
```

Supervised and detached, when it should also outlive the shell that started it
and come back on its own if the process dies:

```bash
./scripts/start-autopilot.sh --detach \
    --prometheus http://192.168.218.8:29090 --smf http://192.168.218.8:30956

./scripts/start-autopilot.sh --status   # alive? plus the last 20 log lines
./scripts/start-autopilot.sh --stop
```

A crash is restarted after ten seconds; a deliberate stop is not restarted.
`logs/autopilot-supervisor.log` records only restarts and any traceback, and
`logs/cdot-autopilot.log` holds the health stream, rotating at 10 MB.

Run one of these three, never two -- two loops writing `/upf-admin` on different
ten-minute phases fight over the weight table.

Rehearse first. `--dry-run` (or `CDOT_LIVE_AUTOPILOT_DRY_RUN=1`) does the full
poll, forecast and solve and logs the exact JSON array it *would* POST, without
touching the SMF; `--once` runs a single cycle and exits. Cadence, freshness
and history guards live under `autopilot` in `configs/cdot_live.json`, with
`CDOT_LIVE_AUTOPILOT_POLL_SECONDS` and `CDOT_LIVE_AUTOPILOT_CONTROL_SECONDS`
as overrides. The loop writes to `logs/cdot-autopilot.log` as well as stdout.

Check it against their lab before trusting it with a write:

```bash
# does their Prometheus answer, and do the configured metric names exist?
./scripts/start-autopilot.sh --once --dry-run
```

`--once` primes the three-hour buffer, runs one forecast-and-solve, logs the
weights it would post, and exits non-zero if the loop could not get that far.
The same evidence is available over HTTP at `GET /api/v1/cdot-live/autopilot`,
with `POST /api/v1/cdot-live/autopilot/{start,stop,cycle,poll}` to drive it.

The loop refuses to actuate -- and says why in the log and in the console --
whenever Prometheus has failed three polls in a row, the newest sample is older
than `require_fresh_seconds`, the buffer holds less than `min_history_seconds`
of history, the SMF is unreachable, or a presenter has a proposal open for
review. In each case it holds the weights already in the SMF rather than
steering on a picture it does not trust.

The presenter-reviewed apply path is unchanged and still available: the console
switches between the live loop and the recorded replay study, and nothing but a
confirmed apply, a rollback, or the autopilot itself ever POSTs to the SMF.

Rebuild the additive Delhi traffic-model/2.0 evidence and presentation without
changing the frozen v1 decks or campaign results:

```bash
python scripts/build_delhi_v2_scenario.py
python -m experiments.evaluate_traffic_realism_v2
python scripts/build_delhi_evidence_manifest.py
python presentation/build_delhi_deck.py
```

Prepare the individual PBS/JupyterHub workshop materials on the shared project filesystem:

```bash
env/bin/pip install -e ".[workshop]"
env/bin/python -m workshop.build_notebooks
env/bin/python -m workshop.prepare_teams --participants 35
```

The participant notebook is
[`workshop/CDOT_UPF_Closed_Loop_Lab.ipynb`](workshop/CDOT_UPF_Closed_Loop_Lab.ipynb).
It uses private per-user paths and the bounded jobs in `pbs/workshop_*.pbs`; it
contains no dashboard credentials or policy-publication capability. The supplied
Parquet, frozen notebook, standalone replay, and recorded reveal form the ordered
fallback chain. See [`workshop/OPERATIONS.md`](workshop/OPERATIONS.md) for the
90-minute run, seven-day readiness gate, 35-user rehearsal, and evidence language.

For the separate presenter demo, use the login-node launcher only after confirming
that the cluster permits a long-running demo process. Cloudflare is enabled by
default; disable it explicitly on login nodes where Quick Tunnels are unavailable:

```bash
./scripts/start-login-demo.sh --cloudflare no
```

It creates and reuses `.conda/cdot-demo`, installs missing Python, Node/npm,
and frontend dependencies without root access. It skips the `cloudflared`
download when Cloudflare is disabled. Run it inside `tmux` if the demo must
survive an SSH disconnect. Press `Ctrl+C` in that session to stop the process.

The launcher rebuilds the React dashboard, runs preflight, and starts the
FastAPI API/WebSocket service that serves the production dashboard from the
same origin. It selects the first free port at or above `CDOT_DEMO_PORT`
(default `8000`), starts a Cloudflare Quick Tunnel when enabled, and prints the
available URLs and presenter credentials. If no presenter
password is supplied, tunnel mode generates a new password for that run. Press
`Ctrl+C` to stop both processes. Use `--cloudflare no` for local-only mode
(`CDOT_DEMO_CLOUDFLARE=no` and the legacy `CDOT_DEMO_TUNNEL=0` are also
supported), or `CDOT_DEMO_SKIP_FRONTEND_BUILD=1` only when intentionally reusing
an already verified bundle.

With Cloudflare disabled, the launcher binds to `127.0.0.1` by default. From a
local workstation, reach a login-node process through SSH forwarding (replace
the host and port with the values printed by the launcher):

```bash
ssh -N -L 8000:127.0.0.1:8000 <user>@<login-node>
```

Open the printed URL and sign in with the printed credentials. Local-only mode
defaults to `presenter` / `demo`. Override them with `CDOT_DEMO_USER`,
`CDOT_DEMO_PASSWORD`, and `CDOT_DEMO_SECRET`. Run
`./scripts/preflight.py` separately to verify the pinned registry, frontend
bundle, service imports, and scenario before presentation.

The traffic assumptions and citations are documented in
[`docs/traffic-model-spec.md`](docs/traffic-model-spec.md); the exact frozen
values are in [`configs/traffic_model_registry.json`](configs/traffic_model_registry.json).

Freeze a seed-specific 16-week, 30-second history manifest before submitting
PBS shards:

```bash
env/bin/python -m experiments.build_history_manifest \
  --output output/manifests/history-s20260805.json \
  --seed 20260805
```

Run one controller:

```bash
env/bin/python -m simulator.macro.cli configs/demo_scenario.json \
  --controller predictive \
  --output output/demo/predictive.jsonl
```

Run one reproducible campaign shard:

```bash
env/bin/python -m experiments.run_campaign_shard \
  --manifest configs/demo_scenario.json \
  --output-root output/macro \
  --campaign-id demo-v1 \
  --controller predictive \
  --seed 1000
```

Or run the three required paired controllers locally for 30 seeds:

```bash
env/bin/python -m experiments.run_local_paired \
  --manifest configs/demo_scenario.json \
  --output-root output/macro \
  --campaign-id demo-v1 \
  --seed-count 30 \
  --skip-existing
```

The shard contains `run.parquet`, `selection-audits.parquet`, the JSONL audit
adapter, and hashed metadata. Use the same campaign ID and seeds for static,
reactive, and predictive controllers, then evaluate them with:

```bash
env/bin/python -m experiments.evaluate_paired \
  --root output/macro/schema_major=1/campaign=demo-v1 \
  --output output/macro/schema_major=1/campaign=demo-v1/paired-evaluation.json
```

The evaluator exits successfully only when every architecture acceptance gate
passes, including at least 30 exactly paired seeds per scenario.

Train the offline 10–80 minute model bundle from completed history shards:

```bash
env/bin/python -m experiments.train_forecaster \
  --campaign-root output/macro/schema_major=1/campaign=history-16w-static-v1 \
  --manifest output/manifests/history-s20260805.json \
  --controller static-capacity-v1 \
  --output output/models/forecaster-v1.json
```

Set `CDOT_FORECAST_BUNDLE` to that file before starting the demo. If it is not
set, the service loads the compact, explicitly synthetic demo-calibration bundle
in `configs/demo_forecast_bundle.json`. The live demo is causal: each policy is
selected after a bucket closes and affects only later simulated sessions;
injected surge/fault controls never regenerate prior telemetry.
