# Control-science v1

Status: **implementation ready for new corpora and ablation campaigns; no new
release claim has been made**

The validated production-scale package remains frozen at
`output/showcase/cdot-production-final/`. Its reference hashes are recorded in
`output/control-science/v1/frozen-production-reference.json`; this work does
not overwrite any production evidence.

## MPC reconciliation

The machine-readable reconciliation is
`output/control-science/v1/mpc-reconciliation.json`.

The 30-pair promotion result and 128-seed production result use the same
one-day duration, trained bundle, MPC settings, new-session-only control scope,
and UL overload-area definition. They are not the same evaluation contract:

- The 30-pair development set used seeds 33001–33030 and was deliberately
  stratified across surge, scheduled-fault, unannounced-outage, and mixed
  stress scenarios. Scheduled faults drove the gain; unannounced and mixed
  scenarios already showed aggregate regressions.
- The production campaign used seeds 49000–49127 on one fixed extreme
  scenario. It was a much larger, shifted scenario distribution with no seed
  overlap.
- The candidate was selected on the 30-pair contract. Its 18.76% unweighted
  mean-pair improvement had only 1.15% severity-weighted improvement and a
  -23.29% worst pair.
- On production, mean-pair UL improvement is -13.30%, its deterministic
  bootstrap 95% interval is [-14.44%, -12.20%], severity-weighted improvement
  is -13.30%, only 2/128 pairs improve, and the worst pair is -36.22%.

Therefore Static is the authoritative production winner. The 30-pair outcome
may be shown only as development evidence under its explicit stress contract;
MPC must not be described as production-promoted.

## Implemented interfaces

- Traffic-v2 Parquet rows now carry actual generated per-group UL/DL rate-bin
  load. The trainer consumes it and explicitly marks the nominal fallback used
  for legacy artifacts.
- `DemandObservation` carries causal regime, telemetry-quality, event-feature,
  and per-feature `available_at` metadata. Future availability is rejected.
- Challenger forecasting includes rich causal ridge, deterministic
  scikit-learn histogram-gradient quantile models, and a regime ensemble that
  switches on an unknown surge only after observable evidence.
- The shared challenger schema includes the requested ten lags, four rolling
  windows with mean/std/max/slope, daily and weekly seasonality, EWMA residual,
  surge score, group aggregates, telemetry quality, event phase/lead time, and
  time since observable anomaly.
- Empirical survival supports right-censored Kaplan–Meier estimation, pooled
  service-class shrinkage, conservative upper curves, a static fallback, and
  staleness/sample/confidence provenance.
- MPC accepts survival tables and audits provenance. Adaptive p50–p90 risk,
  failure-domain caps/N−1 exposure, per-group L1 churn, hold periods, safe-state
  solve skipping, and last-safe fallback are independently switchable. Existing
  configurations retain legacy p95/uniform behavior by default.
- The untouched release evaluator requires exactly seeds 46301–46330 and
  enforces every revised release gate.

## Work intentionally not fabricated

The three new 28-day corpora, forecast selection/test results, survival
sample-size/drift experiments, MPC development/validation/release ablations,
two-node post-change smoke, plots, and live Delhi run require new simulation
campaigns. No placeholder measurements or acceptance claims are included.
Their immutable split and gate definitions are in
`configs/control_science_v1.json`.
