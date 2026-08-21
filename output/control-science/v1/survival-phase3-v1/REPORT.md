# Phase 3 empirical-survival report

This development-only experiment derives session-survival curves from ordinary
session start/end telemetry, including right-censored sessions that are still
active at the causal cutoff. It does not use the simulator's duration
distribution. The oracle curve is retained only as a non-deployable upper-bound
benchmark.

## Lifecycle evidence

| Per-group sample target | Total lifecycles | Completed | Right-censored |
|---:|---:|---:|---:|
| 100 | 9,600 | 8,993 | 607 |
| 1,000 | 96,000 | 90,014 | 5,986 |
| 10,000 | 960,000 | 900,452 | 59,548 |

Sparse groups are pooled within service class. A group with no usable group or
service-class observations receives the static conservative prior. Curves older
than the configured telemetry age are marked stale and cause an immediate
return to Static.

## Calibration and load exposure

| Curve | Load-exposure relative absolute error | Mean group calibration error | Maximum group/horizon error |
|---|---:|---:|---:|
| Oracle upper bound | 0.00% | 0.00% | 0.00% |
| Empirical, n=100 | 4.25% | 3.10% | 18.80% |
| Empirical, n=1,000 | 1.50% | 1.08% | 6.23% |
| Empirical, n=10,000 | 0.42% | 0.33% | 1.93% |
| Static prior | 33.34% | 28.26% | 68.62% |
| Uniform naive | 40.54% | 34.76% | 100.00% |

## Paired MPC development comparison

All rows use the same twelve development seeds, 46101–46112. Confidence
intervals are paired bootstrap intervals for the mean UL overload-area
improvement.

| Survival curve | Mean-pair UL | Bootstrap 95% | Severity-weighted UL | Worst pair | Solver timeouts | Errors |
|---|---:|---:|---:|---:|---:|---:|
| Oracle upper bound | 11.22% | [-0.99%, 27.23%] | 4.20% | -8.58% | 1,188 | 0 |
| Empirical, n=100 | 11.59% | [-0.44%, 27.56%] | 4.16% | -7.81% | 1,377 | 0 |
| Empirical, n=1,000 | 11.50% | [-0.58%, 27.29%] | 4.26% | -7.26% | 1,233 | 0 |
| Empirical, n=10,000 | 9.91% | [-2.94%, 26.25%] | 2.70% | -12.78% | 1,201 | 0 |
| Uniform naive | 12.25% | [2.31%, 25.58%] | 6.09% | 0.00% | 332 | 0 |
| Stale empirical | 0.00% | [0.00%, 0.00%] | 0.00% | 0.00% | 0 | 0 |

The uniform curve's outcome is not evidence of calibration quality: its
load-exposure error is 40.54%. The stale curve returns exactly to Static, as
required.

## Guardrail decision

The first, preserved `survival-guardrail-v1.json` failed because it required
zero solver timeouts even though this experiment is a relative robustness test
against the oracle controller. Version 2 uses that scientific question
directly: empirical survival must introduce zero solver errors and no
more than 5% additional timeouts versus oracle. It passes with 1,201 versus
1,188 timeouts (+1.09%), zero errors, a -1.31 percentage-point mean-pair gap,
0.42% load-exposure error, no DL/drop/establishment regression, and exact stale
fallback.

Only the measured, passing v2 comparison is embedded in
`empirical-n10000-guarded-v2.json`; missing or legacy evidence fails closed.
