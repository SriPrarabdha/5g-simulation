# Extreme optimizer validation and tuning results

Status: **validation failed; no predictive profile selected; test seeds preserved**

Run date: 2026-08-06

This record follows the one-day decision pilot. It uses two separate
event-dense validation days to tune controller behavior without examining the
reserved fresh-seed test set.

## Forecast performance by regime

The frozen model was evaluated causally at 10- and 20-minute horizons on the
fresh-seed pilot. Each row uses at least six completed history buckets and only
features available at its forecast origin.

| Regime | WAPE | Macro group WAPE | p90 coverage | p95 coverage |
|---|---:|---:|---:|---:|
| Normal | 6.51% | 6.26% | 94.19% | 96.71% |
| Surge | 11.08% | 11.02% | 30.73% | 37.85% |
| Brownout | 5.09% | 5.73% | 87.88% | 91.96% |
| Near-total outage | 5.29% | 6.57% | 73.96% | 78.13% |
| Latency incident | 5.04% | 5.06% | 92.36% | 96.18% |

Brownout, outage, and latency labels describe the network state; they do not
directly alter offered demand. Point accuracy therefore remains close to
normal. Scripted traffic surges are the actual forecast weakness: p90 coverage
collapses because an unannounced step-change is not inferable before it begins.

Machine-readable evidence:
`output/models/extreme-forecaster-v1-pilot-regime-evaluation-s20260806.json`.

## Overload decomposition

Simulation summaries now retain total overload and add two diagnostic terms:

- `residual_overload_area_seconds`: overload already present after removing
  traffic from sessions admitted in the current 30-second tick;
- `incremental_new_session_overload_area_seconds`: the remaining immediate
  overload contribution from currently placeable sessions.

For finite capacity these terms sum to total overload area. The incremental
term is not a replacement acceptance metric: sessions placed now become
residual load later. Tuning demonstrated exactly this failure mode—several
profiles reduced immediate incremental overload while worsening total overload
and drops over the day.

Every new summary also states that the actuator scope is
`new_session_placement_only` and that session migration is unsupported.

## Validation design

Two validation manifests used seeds 20260810 and 20260811 on 2026-05-11 and
2026-05-12. Static and every candidate were exactly paired within each day.
Eight predictive profiles isolated:

- default p95 behavior;
- responsive policy gating;
- reduced locality/churn costs;
- 15% and 35% extra demand safety margins;
- a load-only aggressive upper bound; and
- static-anchored blends allowing 10%, 25%, or 50% optimizer adjustment.

Profiles were eligible for fresh-seed testing only if total UL overload area
improved and DL overload area, directional drops, and session failures did not
regress.

## Tuning outcome

Negative values below mean the candidate was worse than static.

| Profile | UL area reduction | DL area reduction | UL drop reduction | DL drop reduction | Accepted? |
|---|---:|---:|---:|---:|---|
| Responsive gate p95 | -7.11% | +1.13% | -33.59% | +1.17% | No |
| Static anchor, 10% optimizer | -9.12% | -6.31% | -8.57% | -6.55% | No |
| Static anchor, 25% optimizer | -18.75% | -13.80% | -17.11% | -14.33% | No |
| Default p95 | -38.98% | -68.46% | -49.38% | -71.07% | No |
| Static anchor, 50% optimizer | -52.30% | -39.75% | -54.52% | -41.26% | No |
| Load-only aggressive | -76.53% | -47.63% | -83.59% | -49.45% | No |
| Load-first with 35% safety | -93.81% | -61.42% | -201.74% | -63.76% | No |
| Load-first with 15% safety | -168.04% | -76.08% | -292.63% | -78.98% | No |

The machine-readable selection artifact is
`output/models/extreme-optimizer-tuning-decision-v1.json`. It records
`selected_profile_id: null`,
`decision: stop_tuning_no_candidate_beats_static_guardrails`, and
`test_seeds_consumed: false`.

The failure is monotonic for static blends: larger optimizer influence causes
larger total regressions. The LP reduces forecast-window peak utilization by
concentrating some allocations, but many sessions last for hours. Those
placements become persistent residual concentrations that a new-session-only
actuator cannot undo. Static capacity weighting spreads every group broadly
and is more robust in this topology.

## Implemented mechanism follow-up

The proposed moderate-complexity changes were implemented before making the
full-campaign decision:

- arrival events can carry `known_at_step` and an explicit
  `forecast_hint_multiplier`; invalid future knowledge is rejected;
- scheduled hints are applied only after their event becomes active and are
  recorded in forecast quality flags;
- an unannounced-surge fallback compares the latest closed bucket with a robust
  trailing median, with configured threshold and multiplier cap;
- a lifetime-aware demand multiplier estimates relative integrated occupancy
  over a configured number of decision windows using each group's uniform
  lifetime range; and
- `max_group_upf_weight` places an explicit upper bound on concentration. The
  solver returns structural infeasibility when the eligible set cannot satisfy
  the bound.

All mechanisms default off or to their backward-compatible value. The complete
suite contains 68 passing tests, including causal hint, anomaly, lifetime, and
diversification tests.

The v2 pilot labels stadium and industrial surges as scheduled with two hours'
notice, while the airport surge remains unannounced. This prevents a result
that relies on treating every synthetic event as advance knowledge.

## Mechanism-isolation validation

The same two validation dates and seeds were regenerated as immutable v2
manifests and run against a newly generated static reference. Negative values
still mean worse than static.

| Profile | UL area reduction | DL area reduction | UL drop reduction | DL drop reduction | Accepted? |
|---|---:|---:|---:|---:|---|
| Lifetime, 12 windows, 10% anchor | -7.17% | -5.02% | -6.60% | -5.21% | No |
| Reference, 10% anchor | -9.12% | -6.31% | -8.57% | -6.55% | No |
| Scheduled hints, 10% anchor | -9.17% | -7.18% | -8.58% | -7.46% | No |
| Anomaly fallback, 10% anchor | -12.43% | -10.11% | -11.83% | -10.50% | No |
| Combined, 25% anchor | -22.11% | -20.56% | -20.94% | -21.34% | No |
| Diversified cap 25%, full optimizer | -70.70% | -48.12% | -114.28% | -49.96% | No |

Lifetime weighting is directionally useful: relative to the otherwise identical
10% reference, it removes 1.95 percentage points of UL-area regression and
1.29 points of DL-area regression. It is not sufficient to beat static.
Scheduled demand magnitude does not solve persistence, and the anomaly fallback
over-corrects after the trained autoregressive features have already observed a
surge. A concentration ceiling by itself still permits correlated allocations
across many groups, so it is not a substitute for a true multi-period state
transition model.

Machine-readable evidence:

- `output/models/extreme-optimizer-mechanism-decision-v2.json`, SHA-256
  `356271e32d4b529ae840b256fbb734f0071d2c68f1879f1730f4b4247c4bacac`;
- `output/manifests/extreme-optimizer-validation-v2-1d-s20260810.json`, SHA-256
  `4a01a57e62d81ccdd3a6aad2eb5e762dff71ececa19e4debbff27befc46b6125`; and
- `output/manifests/extreme-optimizer-validation-v2-1d-s20260811.json`, SHA-256
  `be439be2f0e60e58406cd95bc2873f52b6f5dcf3f5ad2e48245530c2b6d2fd03`.

## Test-set and full-campaign decision

No v1 or v2 profile passed validation. Therefore no profile was frozen, the
reserved three to five fresh test seeds were not consumed, and the long full
campaign remains deferred. Selecting the least-bad candidate or inspecting the
test set would be test-set fishing.

The next optimizer change is no longer a parameter-tuning exercise. It requires
an explicit state-transition optimization that carries admitted cohorts and
their survival into future periods. Scheduled capacity/outage knowledge can
then pre-drain new placements from a UPF before a known maintenance event. For
unannounced outages, established-session migration remains dependent on the
C-DOT capability decision. Total overload and directional drops remain the
acceptance guardrails throughout.

This outcome is evidence against launching the expensive campaign now; it is
not evidence to hide, relax, or replace the failed primary metric.
