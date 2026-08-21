# Phase 3.1 Day-5 development report

## Final decision

Retain Static. No Phase 3.1 candidate passed every conjunctive development
gate, so validation and release remain unauthorized. Seeds 46201–46216 and
46301–46330 remain untouched. Seed 46003 remains generated and sealed but was
never evaluated or used for selection.

## Work completed through Day 4–5

- Restored all eight overwritten Phase-3 sources and revalidated all 29 frozen
  hashes. Added entry-point regression tests and content-addressed source
  archives.
- Corrected the immutable C-DOT presentation in v4 without modifying v3. It
  now labels the historical telemetry synthetic, the survival comparison
  relative, reports the 68.75%/69.50% timeout rates, explains the complete
  promotion gate, and states seed 46003 precisely.
- Implemented observable lifecycle export and distribution-blind
  Kaplan–Meier fitting. The 125-worker PBS campaign tested uniform, Weibull,
  lognormal, heavy-tail mixture and drift distributions using auditor-hidden
  lifetime parameters.
- Instrumented the optimizer's proposed → certified → accepted → executed
  funnel, certificate rejection causes, solve time and model size.
- Implemented and tested the bounded event-triggered min-cost-flow pre-drain
  controller, then evaluated full-strength and blended actions.
- Pre-registered two fresh-seed Phase 3.1 matrices and completed 240 paired
  Static comparisons. V2 added a history-compatibility preflight after v1
  exposed an unreachable trained-forecaster warm-up.

## Survival results

Across 25 trials per hidden distribution, calibration MAE was 3.52% for
uniform, 2.97% for Weibull, 3.18% for lognormal and 2.92% for the heavy-tail
mixture. Drift raised MAE to 4.17%; refreshing from post-drift observations
reduced it to 2.75%. Pooling coverage was 25% and stale tables failed closed in
100% of trials. These results validate the lifecycle/Kaplan–Meier mechanism,
not real-world calibration or operational acceptability.

## Optimizer diagnosis

V1 found strong scheduled-fault pre-drain headroom but unacceptable tails and
churn. Its MPC comparisons were invalid as mechanism exercises because a
144-window forecaster could not warm up in a 144-epoch experiment. The new
preflight now rejects that interface before consuming a development pool.

V2 exercised the MPC correctly: 1,697 proposed actions and 1,032 certified and
executed actions across three survival variants, with no timeout/error solver
statuses. The controlled results were nevertheless neutral to harmful, with
mean-pair UL changes from −0.16% to −0.29%, confidence intervals crossing zero,
and overload/drop guardrail regressions. Survival-table choice made only small
changes to accepted actions and did not produce benefit.

The 50% blended pre-drain candidate was closest to promotion. It passed ten of
eleven gates, averaged +10.16% with a positive bootstrap interval, and improved
severity-weighted UL by 0.60%, but its −10.37% worst pair failed the strict
−10% tail gate. The 25% blend passed ten gates and limited its worst pair to
−4.54%, but its +6.35% mean did not reach the 10% gate. The frozen promotion
logic therefore correctly retains Static.

## Remaining research boundary

No candidate should consume validation seeds. The next development interface,
if authorized, should treat end-to-end decision latency as a gate rather than
relying only on individual solver timeout status, and should investigate a
tail-aware adaptive pre-drain strength between the 25% and 50% settings without
altering the existing gate thresholds.

## Evidence seal

`REFERENCED_EVIDENCE.json` records SHA-256 hashes for both interface freezes,
the content-addressed v2 source archive, the 125-trial survival campaign, both
120-pair development campaigns, and C-DOT showcase v4. The local artifact
manifest seals this report, the final decision and that cross-manifest ledger.
