# C-DOT Control-Science Talk Track

## Opening (30 seconds)

The 12-node production scale result is frozen and unchanged: 384/384 shards, 85.4 minutes, 90.9% aggregate CPU, and zero worker, establishment, or swap failures. This section asks a different question: what controller evidence is strong enough for a production claim?

## Figure 1 — Traffic-v2 realism fingerprint

The corrected corpus trains against actual generated rate-bin UL/DL load, not session count multiplied by a nominal rate. Point out the visible label gap and the causal event markers. Claim the data correction; do not claim forecast superiority yet.

## Figure 2 — Corpus execution integrity

All three 28-day splits are complete and independently seeded: 46001 training, 46002 selection/calibration, and untouched 46003 test. Each contains 80,640 steps and about 201 MiB of detailed evidence.

## Figure 3 — 30-pair versus 128-seed reconciliation

The same UL overload-area direction reverses: development +18.76%, production -13.30%. The contracts differ in scenario composition and seed population. The 128-seed campaign is authoritative: Static remains the production winner.

## Figure 4 — MPC ablation waterfall

Walk left to right across independently switchable changes. Churn/trigger delivered -22.3% mean and -28.5% severity-weighted UL improvement. Whiskers crossing zero and tail regressions prevent promotion even when a mean bar looks encouraging.

## Figure 5 — Scenario controllability

Scheduled notice is genuinely useful; an unknown outage cannot be predicted before observable evidence. The heatmap is the reason we report scenario strata rather than one optimistic average.

## Figure 6 — Mean versus tail risk

The release contract requires both mean benefit and a bounded worst pair. None of the development candidates occupies the safe upper-right gate region, so validation and release remain closed.

## Figure 7 — Plan coverage and release discipline

Separate completed evidence, implementation foundations, and experiments not yet run. Forecast challenger and survival-provider outcomes are pending; release seeds 46301–46330 have not been viewed.

## Figure 8 — Solve activity versus network outcome

Accepted-new-policy counts show actual optimization activity; retained/skipped epochs are excluded. Lower solve activity is valuable only if network overload remains controlled. Exact L1 routing churn was not emitted by this evaluator, so that gate is pending.

## Close / Q&A guardrails

- Production scale is proven.
- Static is the current production controller winner.
- MPC ablations are development learning, not release promotion.
- Forecast and survival implementations are not outcome claims until their held-out experiments run.
- Every result and figure is hashed in the artifact manifest.
