# Control-science v1

Status: **Phase 1 complete and frozen; Phase 2 complete with no eligible
forecast challenger; protected seed 46003 remains untouched**

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
  and per-feature `available_at` metadata. Future availability is rejected. A
  shared builder now drives both live observations and offline extraction;
  offline telemetry pathology is replayed per eligible UPF at the latest
  decision-boundary scrape.
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
- Ridge-v2, histogram-gradient, causal-regime and LightGBM challengers now have
  a train → checksum-verified serialize → load → MPC path. PBS dependencies are
  pinned in `requirements-pbs-forecast.lock`.
- MPC campaign paths accept hashed survival-table bundles and emit routing
  churn, decision/fallback reasons, solver status/timeouts, survival provenance
  and the imperfect-survival guardrail result.
- The untouched release evaluator requires exactly seeds 46301–46330, rejects
  missing observability evidence, and enforces every revised release gate.

The authoritative Phase 1 freeze is
`output/control-science/v1/forecast-phase2/phase1-freeze.json`. It records the
source/interface hashes, exact environment, seed policy, and both corrected
96-group cache identities. Earlier cache attempts are explicitly invalidated in
`output/control-science/v1/forecast-phase2/INVALIDATED.md`.

## Phase 2 forecast selection outcome

The authoritative results and audit are:

- `output/control-science/v1/forecast-phase2/REPORT.md`
- `output/control-science/v1/forecast-phase2/forecast-selection-v3.json`
- `output/control-science/v1/forecast-phase2/phase2-completion-v3.json`

The presentation-ready Phase 1/2 addendum is under
`output/control-science/v1/forecast-phase2/showcase-v1/`. It contains eleven
figures in PNG and SVG, an illustrated report, presenter talk track, HTML
gallery, figure-data JSON, 13-page PDF, and a SHA-256 artifact manifest. Every
plot is derived from the authoritative 480 selection metric shards; no result
is hand-entered.

All five frozen families completed across all 96 groups. LightGBM and
histogram-gradient were the strongest challengers at about 11.6% WAPE
improvement over the moving-average reference and about 27.5% lower
scheduled/detected peak underprediction. They did not meet the frozen 15%
WAPE-improvement requirement, and their worst aggregate regime/horizon slices
regressed by about 14–15%, above the 5% guardrail. No family passed every gate.

The campaign therefore did not select or merge a production challenger and
did not evaluate seed 46003. This is the required fail-closed action, not an
incomplete experiment. The completion evidence records 384 trained bundles,
384 calibrated bundles, 480 metric shards, and 96 independent checksum/load
audit shards, together with artifact-tree hashes and the PBS job identities.

## Work intentionally not fabricated

The three 28-day corpora and Phase 2 forecast selection are complete. The
seed-46003 forecast test was intentionally not run because no candidate earned
access to it. Survival sample-size/drift experiments and later MPC
development/validation/release work remain experimental. No placeholder
measurements or acceptance claims are included. Their immutable split and gate
definitions are in `configs/control_science_v1.json`.
