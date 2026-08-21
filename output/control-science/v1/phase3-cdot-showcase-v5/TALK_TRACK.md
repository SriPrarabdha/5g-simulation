# C-DOT Phase 2.1/3 talk track

## 01 — Forecast targets

The pooled headline has been corrected: sessions and Mbps are shown separately.
LightGBM improves DL by 12.62%, sessions by 11.09%, and UL by 9.99%. These are
useful results, but none reaches the frozen 15% promotion requirement.

## 02 — Worst forecast slice

The worst LightGBM slice regresses 14.70%, but it contains nine scored
observations across eight groups. The wide confidence interval is the key
message: visible risk, not a stable population estimate.

## 03–04 — Synthetic lifecycle survival validation

Kaplan–Meier is validated on synthetically generated censored lifecycle
telemetry. At 10,000 samples per group, load-exposure error is 0.42% and worst
group/horizon error is 1.93%. This validates censoring and estimation mechanics;
it is not real-world or distribution-independent telemetry evidence. Oracle is
shown only as a non-deployable upper bound.

## 05–06 — Relative survival equivalence

Empirical survival tracks oracle outcomes within the measured relative
guardrail: oracle timed out on 1,188 of 1,728 decisions (68.75%), while the
empirical n=10,000 path timed out on 1,201 of 1,728 (69.50%). This is relative
survival equivalence under solver pressure, not an operational robustness pass.
Stale survival returns exactly to Static. A naive uniform curve can look good
in an MPC outcome while being badly miscalibrated, so outcome alone is
insufficient.

## 07–08 — MPC effectiveness

The broad candidates show mean gains, but their intervals cross zero and tail
risk remains. Scenario gains are concentrated rather than general. The narrow
scheduled variants are safe but essentially neutral.

## 09 — Solver behavior

The first scheduled sweep timed out. Shorter horizons and larger budgets
eliminated timeouts, proving the branch was exercised, but did not create a
material benefit. The stop decision is therefore not merely a timeout artifact.

## 10 — Frozen gates

Promotion is conjunctive: every gate must pass. Mean improvement cannot excuse
negative confidence bounds, worst-pair loss, regressions, churn, or solver
failure. No row is all green. Static therefore remains deployed even where a
candidate's average improvement appears attractive.

## 11 — Architecture

Static is the production default. MPC is an optional scheduled-event branch.
Unknown outage, uncertain telemetry, stale survival, failed solve, or failed
same-state certification returns immediately to Static.

## 12 — Experimental discipline

The Phase-3 campaign produced 228 paired one-day runs. Thirteen candidates were
tested and zero advanced. Forecast test seed 46003 was generated and sealed,
but untouched by model evaluation or selection. Validation seeds 46201–46216
and release seeds 46301–46330 remain unused for a future genuinely frozen
winner.


# Phase 3.1/3.2 extension

## 13 — Distribution-blind survival

The fitter consumes only observable lifecycle records; hidden uniform,
Weibull, lognormal, heavy-tail and drift parameters remain auditor-only. Mean
calibration error stays near 3–4%, drift refresh improves 4.17% to 2.75%, and
stale tables fail closed. This validates mechanics on simulated observations,
not external real-world calibration.

## 14 — Every new candidate

All fifteen newer candidates appear with their paired mean and bootstrap
interval. V1 MPC rows marked with X never proposed an action because the
forecaster could not warm up; v2 repairs that interface and exercises MPC.
Strong pre-drain means are visible, but uncertainty or tails prevent promotion.

## 15 — Scenario concentration

Pre-drain works strongly on scheduled faults and is neutral on scenarios where
it correctly does not trigger. Mixed-stress regressions show the missing piece:
known-fault benefit is not robust to simultaneous surprise demand.

## 16 — Complete gates

No candidate is all green. Historical rows show gray for the end-to-end deadline
that was registered only in Phase 3.2. Gates were not weakened after near misses.

## 17 — Benefit versus tail frontier

Full-strength pre-drain has enough mean but unsafe tails/churn. Weak blends are
safer but insufficient. The fresh Phase 3.2 pool moves all interpolated points
below the mean threshold and their intervals cross zero—there is no stable
sweet spot to tune into existence.

## 18 — MPC funnel

Phase 3.1 v1 is a valid fail-closed outcome but an invalid mechanism exercise.
Short-history preflight in v2 enables 1,697 proposals and 1,032 executions.
The exercised controller still averages slightly harmful results, proving the
negative finding is no longer a warm-up artifact.

## 19 — Latency

No-timeout status was insufficient. MPC model/solve diagnostics exceeded six
seconds despite a two-second per-call setting. Phase 3.2 therefore adds an
end-to-end 500 ms gate; only one of five candidates meets it under saturation.

## 20 — Adaptive causal lag

The adaptive blend spans its complete 25–50% range across the campaign, so the
implementation works. On the worst pair, however, surprise demand starts after
pre-drain commitments begin and residual telemetry remains below the trigger.
The controller stays at 50% throughout the critical lead-up.

## 21 — Scale and seed firewall

The complete story covers 588 paired controller runs plus 125 survival trials.
Development pools were consumed as registered; validation and release pools
remain untouched. Forecast seed 46003 was generated but never evaluated.

## 22 — What worked / did not work

The successful outcome is the science and safety system: reproducibility,
mechanism visibility, calibration testing, fallbacks and conjunctive promotion.
The candidate algorithms did not deliver robust, operationally ready benefit.
Static remains deployed.
