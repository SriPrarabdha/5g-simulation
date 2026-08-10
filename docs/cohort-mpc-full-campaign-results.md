# Cohort MPC full-campaign results

Status: **10% demo-stage mean-pair gate passed; working demo candidate, not production-ready**

Date: 2026-08-07

## Decision

The frozen MA6 cohort-state MPC candidate may advance into the self-contained
working demo. It beats paired static by **10.52%** on the declared mean-pair UL
overload-area metric across 30 fresh one-day scenarios. The deterministic
bootstrap 95% interval is **4.81% to 16.93%**, and every aggregate DL, drop, and
session-failure guardrail passes.

This is not a production-release result. When pairs are weighted by their
absolute overload severity, total UL overload-area reduction is only **2.84%**.
The worst individual pair regresses by **23.50%**. The controller therefore
remains a transparent synthetic demo candidate while fault robustness is
improved.

## Campaign design

The campaign ran 30 exactly paired static/MPC comparisons on fresh development
seeds 34001–34030. Each pair covered one simulated day at 30-second resolution.
The matrix was stratified across:

| Scenario | Pairs | Aggregate UL reduction | Worst pair |
|---|---:|---:|---:|
| Demand surge | 8 | 10.42% | 2.57% |
| Scheduled fault | 8 | 19.01% | -23.50% |
| Unannounced outage | 7 | 0.71% | -9.84% |
| Mixed stress | 7 | 1.92% | -8.28% |

Reserved seeds 20260810 and 20260811 were not consumed. Static and MPC shared
the same scenario, seed, random streams, event schedule, and rendezvous
namespace in every pair.

## Aggregate results

Positive values mean MPC improves on static.

| Metric | Reduction | Guardrail |
|---|---:|:---:|
| Mean paired UL overload area | 10.52% | Pass (target ≥10%) |
| Severity-weighted total UL overload area | 2.84% | Informational |
| Total DL overload area | 7.67% | Pass |
| Total UL dropped bytes | 12.42% | Pass |
| Total DL dropped bytes | 9.34% | Pass |
| Session-establishment failures | No increase | Pass |

The distinction between the headline mean-pair metric and severity-weighted
total is intentional. The former gives each scenario/seed pair equal weight;
the latter is dominated by the heaviest overload days. Both are published in
the demo evidence panel.

## Frozen controller

The candidate is `configs/cohort_mpc_pilot_10pct_v2.json` (SHA-256
`044634c6a9265533c4b7deb3174fe53fcd071a004e83fe7f2f9fe542e629916a`).
It uses:

- a causal six-window moving average;
- a 12-window/two-hour cohort-state horizon;
- a 50% blend with contemporaneous static weights;
- same-state static certification before publication;
- fallback to exact static during an observed unplanned capacity state; and
- new-session steering only, with no established-session migration.

## Artifacts and reproduction

- Full machine result:
  `output/models/cohort-mpc-full-campaign-30seed-v1.json`
- Full result SHA-256:
  `bd1d3727966ca423b2c75a77d48b01a6079eeb60c77ce0aa7ec4ba6155a0634c`
- Compact demo evidence:
  `demo_api/data/cohort_mpc_full_campaign_evidence_v1.json`
- Evaluator: `experiments/evaluate_cohort_mpc_candidate.py`

```bash
PYTHONPATH=. env/bin/python -m experiments.evaluate_cohort_mpc_candidate \
  --manifest configs/demo_scenario.json \
  --mpc-profile configs/cohort_mpc_pilot_10pct_v2.json \
  --seed-start 34001 \
  --total-seeds 30 \
  --steps 2880 \
  --output output/models/cohort-mpc-full-campaign-30seed-v1.json
```

The evaluator refuses to overwrite an existing result and rejects reserved
validation seeds.

## Next boundary

For the working demo, freeze the above numbers, default the local runner to
MPC, and expose same-state certificate/fallback status. Before any production
claim, reduce the scheduled-fault and unannounced-outage tail regressions and
require a positive severity-weighted lower-bound gate on untouched release
seeds.
