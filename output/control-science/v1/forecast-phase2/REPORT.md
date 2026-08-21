# Forecast Phase 2 Completion Report

Phase 2 is complete. No challenger passed every frozen selection gate, so no model was promoted and protected seed 46003 was not consumed.

Presentation-ready figures, a talk track, HTML gallery and the consolidated
PDF are available in `showcase-v1/`. Their hashes and authoritative source
identities are recorded in `showcase-v1/artifact-manifest.json`.

## Selection results

The common best simple reference was the six-window moving-average baseline at 14.160% WAPE.

| Candidate | WAPE | vs baseline | p90 coverage | peak improvement | worst slice | Eligible |
|---|---:|---:|---:|---:|---:|:---:|
| calendar-ridge | 12.729% | +10.11% | 93.60% | +11.93% | +11.39% | no |
| hist-gradient-quantile | 12.513% | +11.63% | 93.53% | +27.44% | +14.34% | no |
| lightgbm-quantile | 12.512% | +11.64% | 93.53% | +27.54% | +14.70% | no |
| regime-ensemble | 17.017% | -20.18% | 94.81% | +64.16% | +601.67% | no |
| ridge-v2 | 12.846% | +9.28% | 94.39% | +25.76% | +12.42% | no |

The nonlinear histogram-gradient and LightGBM challengers produced the best WAPE improvements (about 11.6%) and both reduced scheduled/detected peak underprediction by more than 20%, but both missed the required 15% WAPE improvement and exceeded the 5% maximum aggregate regime/horizon regression. Every candidate kept calibrated p90 coverage inside 88–95%.

## Causal and release discipline

- Train seed: 46001.
- Selection/calibration seed: 46002, split into first-half calibration and second-half selection.
- Protected test seed 46003: not evaluated because the selection gate failed.
- Pre-observation unknown-surge rows were excluded from scoring: 14,652 per family.
- Phase 1 interfaces were frozen before authoritative training and selection.

## Reproducibility audit

- 96 train-cache groups and 96 selection-cache groups.
- 384 trained challenger bundles and 384 calibrated challenger bundles.
- 480 group metric shards across five model families.
- 96 independent audit shards reloaded every candidate artifact through the checksum-verifying production load path and cross-checked parent, calibration, group, seed, and metric identities.
- The completion JSON contains the Phase-1 fingerprint, artifact counts, PBS job identities, and Merkle-style tree hashes.

The correct Phase-2 handoff is therefore a documented negative selection result: retain the existing production forecast/controller path and preserve seed 46003 for a future independently frozen challenger.
