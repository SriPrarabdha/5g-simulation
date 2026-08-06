# C-DOT predictive UPF steering simulation

This repository implements the deterministic macro/HPC simulator and a
self-contained closed-loop Demo v1. The presentation host advances synthetic
30-second telemetry through forecasting, optimization, policy validation, and
simulated actuation, with a FastAPI service and a production-built React
operator console. External free5GC/PacketRusher validation, calibrated C-DOT
UPF envelopes, and an SMF selection hook still require a suitable privileged
5G test environment.

## Documentation

- [`docs/README.md`](docs/README.md) — documentation map and implementation
  status language.
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
  slack, locality, and policy-churn terms.
- Independent policy validation, degraded-mode gating, and atomic in-process
  compare-and-swap publication.
- Static, reactive, forecast-capacity, predictive-HiGHS, and non-deployable
  oracle controllers using deterministic weighted rendezvous selection.
- Canonical nested Parquet run data including compact 10-minute joint
  UPF/group demand snapshots, selection-audit Parquet, reproducibility
  metadata/hashes, PBS arrays, and exact paired bootstrap evaluation.
- Frozen, cited synthetic traffic-model registry with an explicit evidence and
  unsupported-assumption boundary.
- Accelerated closed-loop run lifecycle, deterministic seed selection,
  presenter/viewer roles, SQLite audit, safe-policy fallback, and ordered
  versioned WebSocket snapshots and deltas.
- Replaceable synthetic/replay/Prometheus `FlowSource` and
  simulation/advisory/SMF-placeholder `ActuatorSink` interfaces.
- Prometheus-compatible synthetic metrics and REST interfaces for topology,
  telemetry, forecasts, policy, decision traces, model metadata, and matched
  campaign comparisons.
- A five-view React/ECharts operations console served offline by FastAPI.

The selected primary metric for the supplied demo is directional UL overload
area (`overload_area_seconds.ul`). DL results are still reported separately and
cannot be hidden by a combined passing average.

## Not yet implemented

- One-UPF and three-UPF free5GC deployment and smoke tests.
- PacketRusher saturation sweeps and fitted directional/session capacities.
- The free5GC SMF new-session selection hook and external atomic policy store.
- A completed 30-seed campaign for every showcase scenario and its accepted
  immutable result bundle.
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
cd frontend && npm ci && npm run build && cd ..
./scripts/start-demo.sh
```

Open `http://127.0.0.1:8000` and sign in with the local rehearsal credentials
`presenter` / `demo`. Override them with `CDOT_DEMO_USER`,
`CDOT_DEMO_PASSWORD`, and `CDOT_DEMO_SECRET` outside a local demo. Run
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
