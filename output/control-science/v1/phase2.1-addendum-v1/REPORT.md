# Phase 2.1 forecast audit addendum

This addendum does not modify the sealed Phase 1/2 artifacts. The prior headline is labeled **pooled cross-target WAPE** because it pools session counts and Mbps targets.

| Candidate | Pooled improvement | Macro-target improvement | Session | UL | DL |
|---|---:|---:|---:|---:|---:|
| calendar-ridge | 10.11% | 10.04% | 9.88% | 9.86% | 10.40% |
| hist-gradient-quantile | 11.63% | 11.17% | 11.10% | 10.02% | 12.58% |
| lightgbm-quantile | 11.64% | 11.17% | 11.09% | 9.99% | 12.62% |
| regime-ensemble | -20.18% | -18.82% | -20.94% | -15.43% | -20.81% |
| ridge-v2 | 9.28% | 9.20% | 8.84% | 9.01% | 9.76% |

## Slice stability

LightGBM's worst aggregate slice is detected_surge / new_dl_mbps / 30 minutes: 14.70% regression, n=9 observations across 8 groups; the group-cluster bootstrap interval is [-6.53%, 50.93%].

The 15% promotion conclusion is unchanged; seed 46003 remains untouched.
