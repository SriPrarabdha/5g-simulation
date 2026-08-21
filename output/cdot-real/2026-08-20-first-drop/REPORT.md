# First C-DOT metrics review and provisional advisory

## Outcome

The existing synthetic trained bundles are not directly transferable to this
drop: their 96 group identities and 144-window feature history do not match the
24-window C-DOT `(TAC,DNN,DSCP)` trace. The repository's short-history
`seasonal-naive/3` forecaster and HiGHS optimizer do run end to end in PPS proxy
units. Cohort MPC correctly remains fail-closed because its required real
cohort state and capacity calibration are absent.

This is a **controlled second-run advisory, not an automatically deployable
policy**.

## Basic SMF advisory

Use the topology-safe global ratio **upf-1:upf-2:upf-3:upf-4 =
1:2:3:2** (normalized: 12.5%, 25.0%,
37.5%, 25.0%) only after C-DOT confirms the
UPF identity and weight-normalization semantics. Under the email's equal load
per TAC and the supplied TAC constraints, the effective shares are:

| Selection scope | Effective eligible-UPF shares |
|---|---|
| tac-1 | upf-1=33.3%, upf-4=66.7% |
| tac-2 | upf-1=33.3%, upf-2=66.7% |
| tac-3 | upf-1=16.7%, upf-2=33.3%, upf-3=50.0% |
| tac-4 | upf-1=16.7%, upf-3=50.0%, upf-4=33.3% |

Equal global weights produce a structural bias toward upf-1 because it is
eligible for every TAC. The 1:2:3:2 score exactly balances the four equal-TAC
load units when SMF renormalizes scores inside each eligible set.

## Forecast replay

The trace covers 4.0 hours and contains
23 complete ten-minute windows. The
email-declared 30-minute loop is visible in both packet rates and session
resets. Descriptive one-step carried-PPS WAPE is:

| Forecaster | WAPE |
|---|---:|
| last | 23.09% |
| ma3 | 18.97% |
| ma6 | 19.78% |
| seasonal3 | 13.30% |

`seasonal-naive/3` is the best of these existing short-history choices. Its
forecast targets 2026-08-20T20:40:00+05:30 through
2026-08-20T20:50:00+05:30 (Asia/Kolkata). This is carried PPS, not a forecast of
new sessions, so it is suitable for a test advisory and trace replay only.

## Trace-conditioned HiGHS output

HiGHS status: `optimal`. It used p95 carried-PPS forecasts, a 75%
per-group cap, equal unit-normalized UPF envelopes, and a cold-start assumption.
The detailed conditional result is:

| Group | Conditional weights |
|---|---|
| tac-2|ims|dscp-0 | upf-1=75.0%, upf-2=25.0% |
| tac-2|internet|dscp-0 | upf-1=75.0%, upf-2=25.0% |
| tac-3|ims|dscp-0 | upf-1=19.1%, upf-2=5.9%, upf-3=75.0% |
| tac-3|internet|dscp-0 | upf-1=0.0%, upf-2=61.0%, upf-3=39.0% |
| tac-4|ims|dscp-0 | upf-1=0.0%, upf-3=25.0%, upf-4=75.0% |
| tac-4|internet|dscp-0 | upf-1=0.0%, upf-3=25.0%, upf-4=75.0% |

These conditional weights are diagnostic. Prefer the simpler 1:2:3:2 global
advisory unless SMF confirms that it supports independent TAC/DNN policies.

## Blocking data-quality findings

- TAC 1 has zero classified traffic despite the four-TAC scenario.
- Under literal labels, 42.8% of class
  PPS appears on UPF/TAC pairs forbidden by the constraint CSV. The inferred
  permutation `upf-1→upf-1, upf-2→upf-3, upf-3→upf-4, upf-4→upf-2` reduces this to
  0.0%, but that is an
  inference and must be confirmed by C-DOT.
- Active-session gauges contain repeated downward resets, contradicting the
  statement that no subscribers detach. The loop appears to tear down sessions
  between passes.
- `smf.yaml` is not in the directory.
- Packet sizes, calibrated directional/session capacities, per-class session
  arrivals, and session lifetimes/ages are absent.

## What to ask C-DOT before the second run

1. Confirm the canonical UPF identity for every dashboard series and provide
   `smf.yaml`.
2. Confirm whether weights are global per UPF or scoped by TAC/DNN, their
   normalization rule, allowed range, update API, and rollback semantics.
3. Explain why TAC 1 is zero and why the observed class labels violate the
   supplied constraint mapping.
4. Add per-class session-create/session-delete counters, 5QI, packet/byte rate,
   session lifetime or age buckets, and calibrated UL/DL/session capacity.
5. Label loop boundaries and the exact times at which each advisory was
   activated.

The machine-readable policy, forecasts, hashes, and audit findings are in
`advisory.json` beside this report.
