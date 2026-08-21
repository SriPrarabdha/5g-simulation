# Phase 3.1 development matrix v1

## Decision

Retain Static. None of five candidates passed all eleven pre-registered gates.
The 120 paired one-day runs used fresh development seeds 46401–46424. Validation
seeds 46201–46216, release seeds 46301–46330, and forecast test seed 46003 were
not consumed.

## Candidate outcomes

| Candidate | Mean-pair UL | Bootstrap 95% interval | Worst pair | Solver timeout | Decision |
|---|---:|---:|---:|---:|---|
| Pre-drain balanced | 18.41% | [3.89%, 35.43%] | −16.22% | 0 | Reject |
| Pre-drain early/diverse | 18.97% | [3.78%, 36.63%] | −20.19% | 0 | Reject |
| MPC h3 configured-survival diagnostic | 0.00% | [0.00%, 0.00%] | 0.00% | 0 | Invalid mechanism exercise |
| MPC h3 lifecycle lognormal | 0.00% | [0.00%, 0.00%] | 0.00% | 0 | Invalid mechanism exercise |
| MPC h6 lifecycle heavy-tail | 0.00% | [0.00%, 0.00%] | 0.00% | 0 | Invalid mechanism exercise |

The balanced and early pre-drain controllers reduced scheduled-fault UL
overload by 79.20% and 83.05%, respectively. Once severity-weighted across all
scenarios, however, UL overload regressed by 0.025% and 0.144%. Mixed-stress
mean regressions were 5.58% and 7.18%; tail losses and normalized churn failed
the frozen gates. Every proposed flow action was flow-feasible, accepted, and
executed, with no timeout, error, or overflow slack. The 95th-percentile solve
times were 34 ms and 40 ms for 648 variables and 2,376 matrix nonzeros.

## MPC mechanism failure

The trained calendar forecaster requires 144 completed history windows. A
one-day 2,880-step scenario has 144 decision epochs but only 143 completed
prior windows at the final decision. Consequently all 10,368 MPC decisions
failed before optimization: 2,013 for insufficient history, 7,620 because no
known future fault was in horizon, and 735 after unplanned capacity state.

Configured, lognormal lifecycle, and heavy-tail lifecycle variants therefore
produced exactly identical Static outcomes. This is a valid fail-closed result,
but it is not closed-loop survival-sensitivity evidence. A corrective matrix
must preflight history compatibility and use a causal short-history forecaster
or a separate warm-up period before any optimizer comparison.

## Funnel

- Pre-drain balanced: 3,456 requested → 216 proposed → 216 flow-feasible →
  216 accepted → 216 executed.
- Pre-drain early/diverse: 3,456 requested → 324 proposed → 324 flow-feasible
  → 324 accepted → 324 executed.
- MPC variants: 3,456 requested each → 0 proposed → 0 certified → 0 executed.

No v1 candidate is eligible for validation or release.
