# Documentation map

This directory separates executable operating instructions from design intent.
Do not treat a target-state architecture as proof that a release artifact or
external integration exists.

## Start here

- [Interactive workshop facilitator guide](workshop-facilitator-guide.md) —
  90-minute control-room lab, team flow, presenter handoff, fallbacks,
  rehearsal, and acceptance checks.
- [Workshop coordination templates](workshop-coordination.md) — professor
  confirmation, room/cluster questions, attendance, and travel handoff.
- [Pilot presenter guide](presenter-guide.md) — five-minute chapter timing,
  exact defensible claims, deterministic checkpoints, and recovery steps.
- [End-to-end runbook](end-to-end-runbook.md) — commands, host responsibilities,
  validation gates, artifact movement, demo startup, and troubleshooting.
- [System architecture decisions](system-architecture-decisions.md) — why each
  stage is designed as it is, trust boundaries, current implementation status,
  and deferred integration decisions.
- [Synthetic traffic-model specification](traffic-model-spec.md) — evidence,
  distributions, leakage rules, and unsupported assumptions.
- [Extreme training runbook](extreme-training-runbook.md) — calibrated
  national-scale manifest generation, runtime sizing, execution, and training.
- [Extreme data specification and C-DOT gap analysis](extreme-data-spec-and-cdot-gap-analysis.md)
  — schemas, classes, probabilistic model, scale comparison, mail-requirement
  traceability, and meeting questions.
- [Extreme forecaster v1 results](extreme-forecaster-v1-results.md) — frozen
  checksums, held-out and baseline results, acceptance status, improvement
  triggers, and the optimizer handoff.
- [Extreme optimizer pilot results](extreme-optimizer-pilot-results.md) —
  fresh-seed one-day paired outcome, event analysis, and the decision to defer
  the full campaign.
- [C-DOT session migration decision](cdot-session-migration-decision.md) —
  public evidence boundary, current new-session-only scope, and exact questions
  requiring build-specific confirmation.
- [Extreme validation oracle-bound results](extreme-oracle-bound-results.md) —
  full-horizon continuous action-space bounds, knowledge-regime comparison,
  and the decision to proceed with cohort-state MPC.
- [Cohort-state MPC development results](cohort-mpc-development-results.md) —
  Stage A freeze, causal MPC implementation, randomized development replay,
  and the initial development-screen decision.
- [Cohort MPC pre-campaign pilot results](cohort-mpc-pilot-results.md) —
  historical failed-profile evidence and the replacement rationale.
- [Cohort MPC full-campaign results](cohort-mpc-full-campaign-results.md) —
  30 paired one-day runs, the passed 10% demo gate, aggregate guardrails, and
  the remaining fault-tail risk.
- [Extreme optimizer tuning results](extreme-optimizer-tuning-results.md) —
  regime-aware forecast metrics, overload decomposition, validation matrix,
  and the recorded no-selection decision.
- [Architecture whitepaper](whitepaper.pdf) — presentation-oriented background.

The root [README](../README.md) remains the short project entry point. PBS-specific
queue conventions are in [pbs/README.md](../pbs/README.md).

## Status language

The documentation uses three labels:

- **Implemented** — executable in this repository and covered by tests.
- **Target state** — the approved interface or workflow, but not yet executable.
- **External dependency** — requires C-DOT infrastructure, credentials, network
  policy, an SMF/EMS interface, or a completed cluster campaign.

## Current release boundary

The standalone demo is runnable on one host. It advances a local deterministic
simulation one tick at a time, defaults to the frozen MA6 cohort-MPC profile,
and exposes the causal control loop through FastAPI. A 30-seed synthetic demo
campaign is complete; its 10.52% mean-pair result coexists with only 2.84%
severity-weighted improvement and material fault-seed tails. A production
release campaign and real C-DOT SMF/EMS actuation remain outside the current
evidence boundary.
