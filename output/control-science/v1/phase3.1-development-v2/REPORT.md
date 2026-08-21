# Phase 3.1 corrective development matrix v2

## Decision

Retain Static. None of five candidates passed all eleven pre-registered
development gates. The 120 paired one-day runs used fresh development seeds
46425–46448. Validation seeds 46201–46216, release seeds 46301–46330, and
forecast test seed 46003 were not consumed.

PBS job 3568.wlm completed with exit status 0 after 01:10:41. It reserved one
exclusive 125-CPU node and reported 12,005% CPU utilization. All 120 pair files
share one work fingerprint, every candidate has 24 pairs, every seed occurs
exactly five times, and no protected seed overlaps the development pool.

## Candidate outcomes

| Candidate | Mean-pair UL | Bootstrap 95% interval | Worst pair | Severity-weighted UL | Failed gate |
|---|---:|---:|---:|---:|---|
| Pre-drain blend 50% | 10.16% | [0.99%, 21.92%] | −10.37% | +0.60% | Worst pair must be better than −10% |
| Pre-drain blend 25% | 6.35% | [0.66%, 14.19%] | −4.54% | +0.39% | Mean-pair improvement must reach 10% |
| MPC configured-survival diagnostic | −0.28% | [−0.86%, 0.14%] | −5.10% | +0.12% | Four gates |
| MPC lifecycle lognormal | −0.16% | [−0.50%, 0.07%] | −3.39% | +0.05% | Four gates |
| MPC lifecycle heavy-tail | −0.29% | [−0.89%, 0.06%] | −6.58% | +0.05% | Four gates |

The 50% blend passed ten of eleven gates. It reduced scheduled-fault UL
overload by 43.89%, retained a positive confidence lower bound, reduced
severity-weighted UL overload by 0.60%, and stayed within the churn, solver,
fallback, DL/drop and mixed-stress gates. Its mixed-stress seed 46447 pair
regressed by 10.37%, missing the strict tail gate by 0.37 percentage points.

The 25% blend also passed ten gates. It reduced scheduled-fault overload by
26.76%, had a worst-pair result of −4.54%, and lowered normalized churn to
0.0140 L1 per group-decision. Its mean-pair gain of 6.35% did not reach the
pre-registered 10% threshold. These results expose a real benefit/tail-risk
tradeoff; they do not justify changing the frozen gate.

## Corrective MPC mechanism exercise

The six-window causal forecaster resolved v1's unreachable 144-window history
condition. Across the three MPC variants, 1,697 actions were proposed, 1,032
were certified and executed, and no solver returned a timeout or error status.
The same-state certificate rejected 665 proposed actions, chiefly for
insufficient same-state improvement; another 166 decisions were infeasible.

All three exercised MPC variants were neutral to slightly harmful on mean-pair
UL overload. Their bootstrap intervals crossed zero. The configured diagnostic
also regressed aggregate DL overload by 0.43% and DL drops by 0.67%; lifecycle
lognormal and heavy-tail regressed UL drops by 0.057% and 0.290%, respectively.
The configured diagnostic is non-deployable, while both lifecycle bundles
correctly fail the measured empirical-survival gate because their closed-loop
guardrail evidence remains explicitly unmeasured.

Holding the forecaster and MPC profile fixed produced only small survival-table
sensitivity: lognormal certified 346 actions and heavy-tail certified 343, with
mean-pair outcomes of −0.16% and −0.29%. This is valid closed-loop mechanism
evidence, but not evidence of benefit or production robustness.

## Operational audit

The MPC models averaged 3,818 variables. Diagnostic solve time averaged
4.13–4.21 seconds and reached 6.18 seconds, even though the profile's
`timeout_seconds` is 2.0 and no individual solver call returned a timeout
status. The controller diagnostic spans multiple solver/certificate calls, so
the configured limit is not an end-to-end decision deadline. The frozen
timeout/error gate is reported exactly as pre-registered, but these candidates
are not operationally ready. A future interface must gate end-to-end decision
latency explicitly.

The pre-drain models averaged 648 variables and solved in 27–28 ms, with maxima
of 118–127 ms, zero timeouts/errors and zero unexpected fallbacks.

No v2 candidate is eligible for validation or release.
