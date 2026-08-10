# Extreme validation oracle-bound results

Status: **the 20% UL gate is reachable in the continuous new-session action
space; proceed to cohort-state MPC development**

Date: 2026-08-07

## Decision

The clairvoyant-fault, new-session-only relaxation eliminates modeled UL
overload on both existing validation days without modeled DL-overload or
directional-drop regression. The prior conditional conclusion is therefore
resolved: the 20% gate is **not mathematically incompatible** with
new-session-only placement on these two scripted scenarios.

This is an action-space result, not a deployable-controller result. It shows
that sufficiently early, persistent-session-aware placement has enough causal
leverage. It does not show that the present forecast stack can identify the
unannounced airport outage or realize fractional LP assignments exactly.

## Method

`optimization/oracle_bounds.py` builds a full-day continuous cohort LP at the
10-minute decision resolution. It:

- reads the exact per-step arrival realization from each already-produced,
  checksum-verified static validation shard;
- carries every admission bucket forward using the configured uniform session
  lifetime distribution;
- preserves per-group UPF eligibility and physical session capacity;
- models directional safe-capacity overload and physical-capacity drops;
- minimizes UL overload subject to no modeled DL-overload or UL/DL-drop
  regression versus the paired modeled static baseline; and
- returns evaluator-only fractional allocations, never a publishable policy.

The approximation is well anchored to the simulator. Modeled static UL
overload area differs from the full 30-second simulator by +0.16% on seed
20260810 and -0.32% on seed 20260811. DL differences are +0.69% and -0.36%,
respectively.

The scheduled-fault run applies the checksum-recorded evaluator overlay
`configs/extreme_validation_fault_knowledge_v1.json`. It declares the stadium
and industrial brownout/recovery trajectories at their two-hour notice times
without modifying the immutable validation manifests. The airport outage
remains unannounced.

The migration run is a second optimistic relaxation: at most 10% half-L1
turnover of each group's aggregate active UPF shares per 10-minute bucket. It
does not claim that the current C-DOT build exposes session relocation.

## Results

UL reductions below use the continuous modeled static baseline from the same
exact arrival trace. A gate pass also requires no modeled DL-overload or
directional-drop regression.

| Regime | Seed 20260810 | Guardrails | Seed 20260811 | Guardrails |
|---|---:|:---:|---:|:---:|
| Perfect arrivals, causal fault observation | -91.20% | fail | 97.26% | pass |
| Scheduled faults at declared knowledge time | 0.36% | fail | 100.00% | pass |
| Clairvoyant faults, new sessions only | 100.00% | pass | 100.00% | pass |
| Clairvoyant + 10% aggregate migration/bucket | 100.00% | pass | 100.00% | pass |

The clairvoyant rows reach numerical zero UL overload (below
`7e-13 overload-area-seconds`) on both days. The 10% migration relaxation also
reaches numerical zero UL overload, while leaving small DL safe-envelope
overload well below static.

The causal and scheduled rows should not be interpreted as stable lower or
upper bounds. Before an unannounced fault, many allocations have identical
zero current loss. With no terminal exposure or failure-diversity objective,
small arrival changes can make HiGHS choose very different members of that tied
set. Their seed-to-seed reversal is direct evidence that the next controller
needs a robust static-relative tie-break and explicit terminal exposure, not
another one-window weight tweak.

## Consequence for implementation

The next engineering milestone is a 2–4 hour cohort-state MPC, still scoped to
new-session placement:

1. carry age/survival state by group and UPF across the horizon;
2. consume declared future capacity trajectories at `known_at_step`;
3. penalize terminal exposure and concentration by UPF/failure domain;
4. compare the candidate with static from the identical state and scenario
   set, applying it only with a robust improvement certificate; and
5. validate on randomized development faults before freezing a profile.

Bounded migration remains separately versioned and blocked on build-specific
C-DOT capability confirmation. No reserved release-test seeds were consumed.

## Reproduction

The complete machine-readable result is
`output/models/extreme-oracle-bound-evaluation-v1.json`. Re-run it with:

```bash
./env/bin/python -m experiments.evaluate_oracle_bounds \
  --manifest output/manifests/extreme-optimizer-validation-v2-1d-s20260810.json \
  --static-metadata output/macro/schema_major=1/campaign=extreme-opt-v2-static/scenario=extreme-optimizer-pilot-1d-s20260810/controller=static-capacity-v1/seed=20260810/metadata.json \
  --manifest output/manifests/extreme-optimizer-validation-v2-1d-s20260811.json \
  --static-metadata output/macro/schema_major=1/campaign=extreme-opt-v2-static/scenario=extreme-optimizer-pilot-1d-s20260811/controller=static-capacity-v1/seed=20260811/metadata.json \
  --fault-knowledge-overlay configs/extreme_validation_fault_knowledge_v1.json \
  --output output/models/extreme-oracle-bound-evaluation-v1.json
```

The evaluator refuses to overwrite an existing result and verifies the
manifest and Parquet checksums recorded by each static shard.
