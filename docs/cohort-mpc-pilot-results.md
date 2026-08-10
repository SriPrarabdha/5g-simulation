# Cohort MPC pre-campaign pilot results

Status: **historical failed profile; superseded by the MA6 candidate and completed full campaign**

Date: 2026-08-07

> This document records the failure of `cohort_mpc_development_v1.json`. It is
> not the current release decision. The later MA6/static-anchor candidate
> passed a fresh holdout and the 30-seed campaign; see
> [Cohort MPC full-campaign results](cohort-mpc-full-campaign-results.md).

## Decision

The 12-pair pre-campaign pilot is complete. The current development MPC profile
must not advance to the full 30-seed campaign.

The test matrix covered four distinct full simulated days with three fresh
development seeds per day:

1. demand surges without capacity faults;
2. scheduled capacity faults with two-hour notice;
3. unannounced 1%-capacity outages; and
4. mixed scheduled faults, surprise outages, surges, and latency changes.

Every run was exactly paired in memory: static and MPC used the same scenario,
seed, arrival streams, lifetime streams, topology, and events. Seeds 32001–32012
were used. Reserved seeds 20260810 and 20260811 were not consumed.

## Result

The overall mean UL overload-area reduction was only **4.74%**, below the 20%
gate. Five of twelve pairs failed at least one guardrail.

| Scenario | Mean UL reduction | Worst seed | All guardrails |
|---|---:|---:|:---:|
| Surge | 2.84% | -13.91% | No |
| Scheduled fault | 19.37% | 12.69% | Yes |
| Unannounced outage | 1.25% | -6.89% | No |
| Mixed stress | -4.50% | -12.47% | No |

Scheduled-fault behavior is directionally sound but still averages just below
20%. The broader controller is not robust: two surge seeds, one unannounced
outage seed, and two mixed-stress seeds regress UL overload. One surge seed
also creates a directional drop regression, and one mixed seed regresses DL
overload.

The candidate issued 122–143 certified MPC decisions out of 144 on failing
days. This is the important diagnosis: the continuous same-state certificate
is too optimistic under closed-loop forecast error and accumulated rendezvous
realization. Merely running more seeds with the same profile is not justified.

## Metric hardening

The first pilot attempt represented surprise outages with `health=unavailable`,
which correctly yielded infinite normalized overload area at zero safe
capacity. That makes relative overload reduction undefined. The corrected
pilot matches the existing extreme benchmark convention: an outage retains a
1% emergency capacity envelope, keeping the declared primary metric finite.
The failed decision above is from this corrected run.

## Artifacts

- Evaluator: `experiments/evaluate_cohort_mpc_pilot.py`
- Corrected machine result:
  `output/models/cohort-mpc-pre-campaign-pilot-v2.json`
- Result SHA-256:
  `3b703cd167a7c928cd36f72895e8c8659dde6096e20747841f49951ac87b37d7`
- Profile tested: `configs/cohort_mpc_development_v1.json`
- Simulated duration: 12 paired one-day scenarios, or 24 simulator-days across
  static and MPC.

## Required development before another pilot

1. Add closed-loop/static rollout certification rather than certifying only a
   continuous expected horizon at each isolated decision.
2. Include forecast-error and rendezvous-realization margins in the
   certificate.
3. Make the controller retain exact static when the baseline UL overload is
   negligible; the current objective sometimes trades a small UL regression
   for a much larger DL improvement.
4. Add unknown-fault scenario exposure to the planning objective instead of
   relying only on terminal concentration.
5. Re-run on new development seeds after changes. Do not tune against or
   promote the 32001–32012 pilot seeds.

That profile remained blocked. It was replaced rather than promoted. The
replacement used fresh seeds, a 10% demo-stage mean-pair gate, and aggregate
directional guardrails before the full campaign was run.
