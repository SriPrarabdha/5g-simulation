# C-DOT predictive UPF steering simulation

This repository implements the macro/HPC portion of
`cdot_upf_simulation_demo_architecture.md`. It is not yet a complete Demo v1:
the external free5GC/PacketRusher plane, calibrated UPF envelopes, Prometheus
adapters, and the SMF selection hook still require a suitable privileged 5G
test environment.

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
- Canonical nested Parquet run data, selection-audit Parquet, reproducibility
  metadata/hashes, PBS arrays, and exact paired bootstrap evaluation.

The selected primary metric for the supplied demo is directional UL overload
area (`overload_area_seconds.ul`). DL results are still reported separately and
cannot be hidden by a combined passing average.

## Not yet implemented

- Generator/UPF Prometheus exporters and live DemandBucket assembly.
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
