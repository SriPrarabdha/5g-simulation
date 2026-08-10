# Extreme forecaster v1: frozen results and improvement plan

Status: **checksum-frozen provisional candidate; not release-accepted**

Frozen: 2026-08-06

This document is the decision record for the first model trained from the
completed 16-week extreme synthetic campaign. It separates measurements that
have passed from work that is still required. None of these results is a claim
of accuracy on C-DOT production traffic.

## 1. Frozen identities

| Artifact | Identity |
|---|---|
| Model | `output/models/extreme-forecaster-v1.json` |
| Model version | `extreme-calendar-ridge-conformal/1.0` |
| Internal canonical bundle SHA-256 | `8579c425e4db3a2476c3bf89c0ef2e0b0a297519cffa26d647d7bb4dc1156574` |
| Model file SHA-256 | `71ad93b4653ec61c264b6f206181a52983117f1e714b67ee9ec24588057ac20f` |
| Freeze record | `output/models/extreme-forecaster-v1.freeze.json` |
| Freeze-record canonical SHA-256 | `ea95a06b64dcdeccf80b9e3a08308156b8db2728e4aca568b4bdae0fea0dbf09` |
| Baseline evaluation | `output/models/extreme-forecaster-v1-baseline-evaluation.json` |

The freeze record validates the bundle's internal checksum and records the
source manifest, campaign metadata, artifact hashes, exact training-code
hashes, Git state, Python, NumPy, and PyArrow versions. It is separate from the
model so freezing does not alter the trained artifact. The command refuses to
overwrite an existing freeze record.

The worktree was dirty when this candidate was frozen. This is recorded in the
freeze record, with the exact training-code hashes, so the artifact is
traceable but is intentionally not called a clean-source release.

## 2. Training corpus and model

| Item | Frozen value |
|---|---:|
| Synthetic history | 16 weeks / 112 days |
| Simulator resolution | 30 seconds |
| Forecaster bucket | 10 minutes |
| Simulator ticks | 322,560 |
| Ten-minute windows | 16,128 |
| Zones / traffic groups / UPFs | 8 / 96 / 24 |
| Nominal scenario UE population | 16,000,000 |
| Grouped time-series observations | 1,548,288 |
| Targets | new sessions, new UL Mbps, new DL Mbps |
| Direct horizons | 10, 20, 30, 40, 50, 60, 70, 80 minutes |
| Fitted direct models | 2,304 |
| Ordered split used by v1 | 70% train / 15% calibration / 15% test |
| Total training command time | 3:03 |
| Actual fitting after data load | about 13 seconds |
| Peak training RSS | about 1.8 GiB |

The algorithm is per-group calendar ridge regression with one direct model per
target and horizon. Its inputs are intercept, last value, six-bucket mean,
recent trend, daily-seasonal value, time-of-day sine/cosine, and day-of-week
sine/cosine. Median calibration bias corrects p50. Split-conformal residual
widths provide upper p90/p95 bounds, and the runtime supports adaptive
conformal updates. Features are constructed only from information available at
the forecast origin.

GPU training is not useful for this v1 algorithm: fitting 2,304 tiny
nine-feature ridge systems takes seconds. Parquet decoding takes almost all of
the three minutes. A GPU becomes relevant only if the improvement plan reaches
large boosted-tree or neural candidates.

## 3. Held-out results

`1 - WAPE` is shown informally as a score, not as classification accuracy.
WAPE and interval coverage are the decision metrics.

| Overall metric | Result |
|---|---:|
| Macro WAPE | 7.63% |
| Informal `1 - WAPE` score | 92.37% |
| Mean p90 upper-bound coverage | 94.21% |
| Mean p95 upper-bound coverage | 96.69% |

| Horizon | Macro WAPE | p90 coverage | p95 coverage |
|---:|---:|---:|---:|
| 10 min | 4.48% | 94.84% | 97.38% |
| 20 min | 6.09% | 94.96% | 97.27% |
| 30 min | 7.35% | 94.92% | 97.03% |
| 40 min | 7.95% | 94.33% | 96.61% |
| 50 min | 7.42% | 93.88% | 96.36% |
| 60 min | 7.63% | 93.10% | 95.85% |
| 70 min | 9.34% | 93.67% | 96.52% |
| 80 min | 10.73% | 93.97% | 96.49% |

| Target | Macro WAPE | Mean MAE | p90 coverage | p95 coverage |
|---|---:|---:|---:|---:|
| New sessions | 7.63% | 191.732 sessions | 94.21% | 96.69% |
| New UL | 7.63% | 171.053 Mbps | 94.21% | 96.69% |
| New DL | 7.63% | 300.079 Mbps | 94.21% | 96.69% |

The three targets have identical WAPE and coverage because this simulator
derives per-group UL and DL demand from session arrivals using fixed offered
Mbps per session. Their MAE differs because their units and scale differ. This
is not evidence that three independent traffic mechanisms were learned.

Operational-horizon tails are acceptable for this provisional candidate: at
10 minutes the median group WAPE is 4.21%, the group p90 is 5.92%, and the
worst group is 15.85%; at 20 minutes the median is 5.80%, group p90 is 8.42%,
and the worst is 18.56%. Long-horizon tails need more work. The worst
session-count models are industrial V2X at 80 minutes (33.00%), 70 minutes
(31.30%), and 60 minutes (23.70%), followed by south-urban enterprise backup
at 80 minutes (25.36%) and 70 minutes (23.76%).

## 4. Fair baseline comparison

The evaluator reconstructed the exact ordered final 15% test rows used by each
frozen direct model. Each baseline uses only data available at its forecast
origin. Results are unweighted macro means across group, target, and horizon.

| Method | Macro WAPE | Ridge reduction |
|---|---:|---:|
| Frozen calendar ridge | 7.63% | — |
| Daily seasonal naive (144 buckets) | 13.71% | 44.36% |
| Six-bucket moving average | 14.30% | 46.68% |

The all-held-out component of the ≥10% improvement gate therefore passes. At
10 minutes ridge improves 67.28% over daily seasonal naive; at 20 minutes it
improves 55.54%. Even at 80 minutes it improves 21.69%.

This does **not** close the complete release gate: non-event and held-out surge,
brownout, and outage windows still need separate reporting.

## 5. Acceptance status

| Gate | Status | Evidence or blocker |
|---|---|---|
| Checksum and lineage | Pass, provisional | Separate freeze record and exact code/artifact hashes |
| Ordered held-out metrics | Pass for current 70/15/15 split | Results above |
| Mean p90 coverage in 85–95% band | Pass | 94.21% |
| ≥10% WAPE gain over seasonal naive | Pass for all held-out rows | 44.36% reduction |
| Non-event versus event-stratified accuracy | Pending | Must isolate normal, surge, brownout, outage, and latency windows |
| Manifest-declared 11/2/3 week split | Pending | v1 used 70/15/15 rather than explicit weeks 1–11 / 12–13 / 14–16 |
| Predictive optimizer integration smoke | Pass | 96 forecasts loaded from this bundle; HiGHS status `optimal` on the 24-UPF topology |
| One-day paired controller pilot | Below primary gate | 2.40% lower UL overload area than static; duration improved 19.53% UL and 76.38% DL |
| Full paired controller benefit | Deferred | Improve and repeat short fresh-seed pilots before a long campaign |
| C-DOT telemetry validation | External dependency | Requires representative labeled C-DOT history and capacity/topology truth |

The model is suitable for the next optimizer-evaluation phase and for a clearly
labeled synthetic demo. It is not yet suitable for a production accuracy or
overload-reduction claim.

## 6. When to improve it

Keep v1 frozen while the next gates are measured. Start a v2 experiment—not an
in-place edit—if any of these occurs:

- event-stratified 10-minute WAPE exceeds 10%, or materially regresses against
  non-event traffic;
- any operational 10- or 20-minute group exceeds 20% WAPE;
- p90 coverage falls outside 85–95% on the declared release slice or on key
  traffic classes;
- the model fails to beat seasonal naive by at least 10% on non-event rows;
- paired predictive control does not materially reduce UL overload without a
  DL, session-rejection, or policy-churn regression;
- C-DOT traffic exhibits different seasonality, class rates, missingness,
  counter resets, mobility, or topology regimes;
- 30–80 minute accuracy becomes operationally important rather than an
  explanatory display.

## 7. How to improve it

Apply changes in this order and retain v1 as the baseline:

1. Implement the manifest's exact week split and event/regime labels. This is
   evaluation correctness, not model complexity.
2. Add a true weekly lag (1,008 ten-minute buckets), multi-season Fourier
   terms, rolling volatility, and causal known-in-advance event covariates.
3. Select the best candidate per group/horizon on validation data: ridge,
   seasonal model, or LightGBM. Do not select on test data.
4. Vary offered Mbps per session and independently model session arrivals, UL,
   and DL so throughput evaluation is no longer a scaled copy of arrivals.
5. Pool sparse groups with hierarchical/global models while preserving zone,
   DNN, S-NSSAI, and 5QI dimensions. The current selection identity is
   `(zone, DNN, S-NSSAI)` and each tuple has one 5QI; promote 5QI into that
   identity before supporting multiple 5QIs for the same tuple.
6. Calibrate on representative C-DOT history, add drift monitoring and
   missing/reset quality features, then repeat the untouched test and paired
   controller evaluations.

Only after those steps should neural temporal models be considered. They add
GPU and tuning cost, but will not repair a split, data-generation, or
event-labeling problem.

## 8. Reproduce and move to optimizer evaluation

Report the frozen held-out metrics:

```bash
env/bin/python -m experiments.report_forecast_bundle \
  output/models/extreme-forecaster-v1.json
```

The freeze and baseline CLIs refuse accidental overwrite. Use a new v2 output
name for any rerun.

Predictive campaign shards can now receive an explicit trained bundle. The
following long command is retained for reproducibility, but the one-day pilot
in [extreme optimizer pilot results](extreme-optimizer-pilot-results.md) says
**not to run it yet**:

```bash
env/bin/python -m experiments.run_campaign_shard \
  --manifest output/manifests/extreme-training-s20260805.json \
  --output-root output/macro \
  --campaign-id extreme-training-16w-s20260805 \
  --controller predictive \
  --forecast-bundle output/models/extreme-forecaster-v1.json \
  --seed 20260805 \
  --progress-every-simulated-hours 12
```

The published predictive shard records both the model file hash and internal
bundle hash. Running that 16-week command is a same-seed integration pilot and
will take roughly another workstation night and substantial disk. A formal
claim needs new held-out seeds and exactly paired static, reactive, and
predictive runs, not only the seed used to generate the training history.

Do not point the default `configs/demo_scenario.json` UI runtime at this extreme
bundle: its six group identities do not overlap the 96 extreme-training group
identities. Startup and campaign commands now reject an incompatible pairing
instead of silently evaluating the fallback. Use the extreme manifest for the
optimizer campaign, or train a separately versioned model against the default
demo scenario before loading it there.
