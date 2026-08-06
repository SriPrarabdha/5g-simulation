# Extreme 16-week forecast-training run

The extreme profile is a deterministic, synthetic national-scale workload for
training-data generation and failure-regime testing. It is calibrated for one
foreground static-controller shard on the Ryzen 9 9950X3D workstation used on
2026-08-05. It is not a claim that the synthetic traffic is field-calibrated
to a specific operator network.

Read [extreme-data-spec-and-cdot-gap-analysis.md](extreme-data-spec-and-cdot-gap-analysis.md)
before starting the long run. It documents the exact generated fields and the
important differences from the requested C-DOT Prometheus flow.

## Workload

- 16 simulated weeks, 322,560 ticks, and 16,128 ten-minute buckets;
- 16 million nominal UEs;
- 8 traffic zones, 24 UPFs, and 96 independently forecastable groups;
- compact joint `(UPF, zone, DNN, S-NSSAI, 5QI)` offered-demand snapshots at
  each completed 10-minute bucket;
- consumer video, social/live, gaming, voice, enterprise, RTC, URLLC, mMTC,
  V2X, edge-AI, backup, and public-safety traffic;
- hourly diurnal and weekly regimes;
- deterministic flash crowds, capacity brownouts, near-total UPF outages, and
  path-latency incidents; and
- 258,656 scheduled regime and fault events for seed `20260805`.

The engine processes every generated session for admission and deterministic
weighted rendezvous selection. Selection audits are retained at a deterministic
1-in-5,000 stride. Forecast training uses every 30-second step and every
ten-minute group bucket; retaining every session audit would not add forecast
targets or features.

## Calibrated runtime and resources

A complete one-day shard using the exact campaign writer and joint group/UPF
bucket schema completed in 5:06.31, peaked at 602,136 KiB RSS, and wrote 87.6
MiB. Linear projection for the 112-day profile is 9:31:46, approximately 64.3
GiB peak RSS, and approximately 9.6 GiB of artifacts. Daily/weekend mix,
filesystem throughput, CPU contention, and surge density can move the result,
so reserve 12 hours, 96 GiB RAM, and 16 GiB disk.

The calibrated command used the static controller. Predictive-controller
campaigns should be run separately after training and measured independently.

## Build and validate the manifest

```bash
env/bin/python -m experiments.build_extreme_history_manifest \
  --profile configs/extreme_training_profile.json \
  --output output/manifests/extreme-training-s20260805.json \
  --seed 20260805 \
  --start 2026-01-05T00:00:00Z

env/bin/python -c 'from simulator.macro import load_scenario; c=load_scenario("output/manifests/extreme-training-s20260805.json"); print({"steps":c.steps,"groups":len(c.groups),"upfs":len(c.upfs),"events":len(c.events)})'
```

The expected manifest SHA-256 stored inside the generated seed-`20260805`
manifest is `ef0e2bc43c8efc580b08fbc97e45842805d049d45fa654c67b7671bddeb6c08b`.

For a capacity check, add `--days 1` and use a distinct output file and
campaign ID. Do not mix a shortened manifest with the full campaign.

## Run the full shard

Run this in a persistent terminal or the site's approved job manager:

```bash
/usr/bin/time -v env/bin/python -m experiments.run_campaign_shard \
  --manifest output/manifests/extreme-training-s20260805.json \
  --output-root output/macro \
  --campaign-id extreme-training-16w-s20260805 \
  --controller static \
  --seed 20260805
```

The campaign CLI flushes a progress line every 12 simulated hours by default.
Each line includes percent complete, step, simulated day, wall-clock elapsed
time, ETA, and peak RSS. It also reports the JSONL, Parquet, audit, hashing, and
final-publication phases. Override the cadence with
`--progress-every-simulated-hours`; for example, use `24` for one line per
simulated day.

For an unattended workstation run, redirect both streams to a log and prevent
idle sleep with `systemd-inhibit`. Follow the log with:

```bash
tail -f logs/extreme-training-16w-s20260805.log
```

The command refuses to overwrite an existing partial or complete shard. Use a
new campaign ID after changing the profile, seed, start time, or code.

## Train the forecaster

```bash
env/bin/python -m experiments.train_forecaster \
  --campaign-root output/macro/schema_major=1/campaign=extreme-training-16w-s20260805 \
  --manifest output/manifests/extreme-training-s20260805.json \
  --controller static-capacity-v1 \
  --model-version extreme-calendar-ridge-conformal/1.0 \
  --output output/models/extreme-forecaster-v1.json
```

The trainer streams Parquet input in bounded batches and uses the mean active
sessions and offered UL/DL over all 20 samples in each 10-minute bucket. It
creates one model per
selection group and target with an ordered 70/15/15 train/calibration/test
split. This is not yet the exact 11/2/3-week boundary declared in the manifest.
More synthetic volume reduces sampling noise and broadens stress coverage, but
does not replace calibration and final testing against operator telemetry.

Report the bundle's held-out metrics by horizon and target after training:

```bash
env/bin/python -m experiments.report_forecast_bundle \
  output/models/extreme-forecaster-v1.json
```

Use `--json` for a machine-readable report. WAPE and interval coverage are
forecasting metrics; `1-WAPE` is included as a convenient score but should not
be described as classification accuracy.
