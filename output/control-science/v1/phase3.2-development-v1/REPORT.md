# Phase 3.2 development report

## Decision

Retain Static. None of the five candidates passed all twelve pre-registered
development gates. PBS job 3569.wlm completed with exit status 0 after
01:08:57, using one exclusive 125-CPU node at a reported 12,008% CPU.

The 120 paired one-day runs used fresh development seeds 46449–46472. Every
candidate has 24 pairs, every seed appears exactly five times, all pair files
share one work fingerprint, and no validation or release seed was consumed.

## Candidate outcomes

| Candidate | Mean-pair UL | Bootstrap 95% interval | Worst pair | Scheduled-fault mean | Maximum decision time | Failed gates |
|---|---:|---:|---:|---:|---:|---:|
| Fixed 35% | 6.15% | [−0.84%, 16.37%] | −9.49% | 28.40% | 764 ms | 3 |
| Fixed 40% | 6.39% | [−0.99%, 16.74%] | −10.58% | 30.01% | 225 ms | 3 |
| Fixed 45% | 6.65% | [−1.12%, 17.18%] | −11.97% | 31.59% | 970 ms | 4 |
| Adaptive 50→25%, 50–75% utilization | 6.61% | [−1.15%, 17.19%] | −11.87% | 31.53% | 739 ms | 4 |
| Adaptive 50→25%, 60–85% utilization | 6.63% | [−1.15%, 17.22%] | −11.87% | 31.59% | 628 ms | 4 |

All candidates reduced severity-weighted UL overload slightly, by 0.08–0.15%,
and improved aggregate DL overload and UL/DL drops. None reached the 10%
mean-pair gate, and every bootstrap interval crossed zero. Fixed 35% was the
only candidate to stay inside the strict worst-pair gate, but it still failed
the mean, confidence and end-to-end latency gates. Fixed 40% met the new 500 ms
deadline but failed mean, confidence and tail gates.

## Adaptive-mechanism audit

The adaptive candidates did exercise their configured range. Across 216
optimal actions each, both ranged from 25% to 50%; their mean applied strengths
were 47.00% and 47.50%. Observed residual utilization ranged from about 30% to
171%.

Adaptation did not protect the worst mixed-stress pair, seed 46468. The known
capacity reduction entered the pre-drain horizon at step 460 and the surprise
arrival surge began later at step 620. During all twelve pre-drain decisions,
the causal residual measurement remained near 35%, below both taper thresholds,
so both controllers applied the full 50% action. Their worst-pair loss was
therefore 11.87%, close to fixed 45%'s 11.97% loss. A contemporaneous residual
utilization rule cannot protect against a surprise that begins after the
pre-drain action has committed persistent sessions.

This result closes the simple fixed/interpolated and instantaneous-utilization
adaptive hypotheses. Further interpolation on additional seed pools would be
post-hoc tuning, not a justified experiment.

## Operational deadline

The new gate measures complete controller decision latency rather than only
solver status. Solver maxima were 100–171 ms with zero reported timeouts or
errors, but end-to-end maxima were 225–970 ms under the saturated 120-process
node. Only fixed 40% met the 500 ms maximum. The gate correctly exposes
wall-clock outliers hidden by solver-status-only reporting.

## Research boundary

No Phase 3.2 candidate is eligible for validation or release. Static remains
the production controller. Seeds 46201–46216 and 46301–46330 remain untouched;
seed 46003 remains generated and sealed but unused by evaluation or selection.

Any future candidate should use a mechanism that bounds commitments under
unobserved future demand—such as a robust uncertainty budget or reversible
short-lived admission allocation—not another fixed blend sweep. Future campaign
execution should also compute each unique Static baseline once and distribute
candidate-only tasks across multiple nodes to remove redundant simulation work.
