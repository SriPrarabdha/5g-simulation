# Phase 3.1 distribution-blind survival campaign

## Result

All 125 pre-registered trials completed successfully: 25 independent auditor
seeds for each of uniform, Weibull, lognormal, heavy-tail mixture, and drifting
holding-time regimes. No validation, release, or forecast-test seed was used.

The fitter consumed only `lifecycle-export/1.0` records containing session ID,
group, service class, start, observable end or censor, and timestamps. Generator
family and holding-time parameters were available only to the auditor.

| Hidden auditor regime | Trials | Mean calibration MAE | Worst trial MAE | Sparse-group pooling | Stale fail-closed |
|---|---:|---:|---:|---:|---:|
| Uniform | 25 | 3.52% | 3.70% | 25% | 100% |
| Weibull | 25 | 2.97% | 3.18% | 25% | 100% |
| Lognormal | 25 | 3.18% | 3.39% | 25% | 100% |
| Heavy-tail mixture | 25 | 2.92% | 3.17% | 25% | 100% |
| Distribution drift | 25 | 4.17% | 4.40% | 25% | 100% |

After post-drift telemetry refresh, mean drift calibration MAE fell from 4.17%
to 2.75%. This is evidence for calibration, staleness detection, pooling, and
adaptation using a distribution-blind fitting interface. Closed-loop sensitivity
is evaluated separately in the dependent Phase 3.1 controller matrix.

These results do not establish production robustness by themselves. Survival
bundles remain fail-closed with an unmeasured closed-loop guardrail until paired
controller evidence is available.
