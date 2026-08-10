# Cohort-state MPC development results

Status: **historical development screen; superseded by the completed MA6 campaign**

Date: 2026-08-07

## Outcome

The first deployable, new-session-only cohort-state MPC slice is implemented.
On five non-reserved development seeds it reduces UL overload area by 22.87%
to 27.96% versus paired static replay, with a 25.09% mean reduction. Every run
also improves DL overload area, UL and DL dropped bytes, and does not increase
session-establishment failures.

This result justified the larger pre-campaign pilot; this specific profile did
not survive that gate. A later MA6/static-anchor profile passed a fresh
holdout and completed the 30-seed campaign. See
`docs/cohort-mpc-full-campaign-results.md` for the current decision.

## Controller boundary

`optimization/cohort_mpc.py` and `CohortMPCController` implement:

- exact active cohort state by group, UPF, remaining lifetime, and directional
  per-session load;
- a 12-window/two-hour receding horizon with expected future-cohort survival;
- capacity and health trajectories visible only after each event's declared
  `known_at_step`;
- terminal maximum safe-utilization minimization and a per-group destination
  cap as failure-exposure/diversification tie-breaks;
- no established-session migration—the optimizer changes only new-session
  rendezvous weights;
- aggregate modeled UL, DL, session, and physical-drop constraints no worse
  than static from the identical cohort state and forecast path; and
- publication only after a positive same-state static certificate.

The frozen development profile applies 50% of each certified MPC action and
50% of the contemporaneous static allocation. The unanchored development
trial produced stronger overload reductions but small realized UL-drop
regressions. The static blend removed those regressions across all five
development seeds without sacrificing the 20% UL target.

## Results

All percentages are paired reductions relative to static; positive is better.

| Seed | UL overload | DL overload | UL dropped bytes | DL dropped bytes | Certified decisions |
|---:|---:|---:|---:|---:|---:|
| 31001 | 27.30% | 7.58% | 0.55% | 2.68% | 23 / 24 |
| 31002 | 23.77% | 8.21% | 0.46% | 7.35% | 23 / 24 |
| 31003 | 23.56% | 6.85% | 0.28% | 3.35% | 23 / 24 |
| 31004 | 22.87% | 4.46% | 0.37% | 6.49% | 23 / 24 |
| 31005 | 27.96% | 7.83% | 0.19% | 5.68% | 23 / 24 |

The first decision on every run retains static because no closed history is
available. All later decisions receive a same-state improvement certificate.

The machine-readable result is
`output/models/cohort-mpc-development-evaluation-v2.json` (SHA-256
`f54348c04dea584d1e9e5ef9e88a2ab786b1ecd158a68cbc26e579cb35247a93`).
The profile is `configs/cohort_mpc_development_v1.json` (SHA-256
`ae688a5d3ad212203e63d42aec893cc4b18a2eaad4b50f8afc3d58f84c4bd9d5`).

## Stage A freeze

The oracle handoff is frozen independently at
`output/models/extreme-oracle-bound-stage-a.freeze.json`. Its internal
`freeze_record_sha256` is
`b6de6d66727c623c6d5262c2716cbb63b3e04e955d2e2803668b5cbc51127425`.
The record revalidates the oracle evaluation, knowledge overlay, two scenario
manifests, results document, and implementation checksums without modifying or
re-running any reserved seed.

## Reproduction

```bash
../env/bin/python -m experiments.evaluate_cohort_mpc \
  --manifest configs/demo_scenario.json \
  --forecast-bundle configs/demo_forecast_bundle.json \
  --mpc-profile configs/cohort_mpc_development_v1.json \
  --seed 31001 --seed 31002 --seed 31003 --seed 31004 --seed 31005 \
  --steps 480 \
  --output output/models/cohort-mpc-development-evaluation-v2.json
```

The evaluator refuses to overwrite an existing result and rejects the two
reserved validation seeds.

## Next gate

Before labeling MPC release-ready, expand randomized development coverage and
add unknown-fault scenario robustness. After the profile is frozen, run it
once on untouched release seeds and require paired confidence bounds plus the
same directional guardrails. Migration and RL remain out of scope for this
demo stage.
