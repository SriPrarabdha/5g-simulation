# Documentation map

This directory separates executable operating instructions from design intent.
Do not treat a target-state architecture as proof that a release artifact or
external integration exists.

## Start here

- [End-to-end runbook](end-to-end-runbook.md) — commands, host responsibilities,
  validation gates, artifact movement, demo startup, and troubleshooting.
- [System architecture decisions](system-architecture-decisions.md) — why each
  stage is designed as it is, trust boundaries, current implementation status,
  and deferred integration decisions.
- [Synthetic traffic-model specification](traffic-model-spec.md) — evidence,
  distributions, leakage rules, and unsupported assumptions.
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
simulation one tick at a time, loads a checksum-verified offline forecast
bundle, and exposes the causal control loop through FastAPI. The PBS cluster can
generate immutable campaign shards and the trainer can turn their Parquet data
into a 10–80 minute bundle. A full release campaign and real C-DOT SMF/EMS
actuation remain outside the current evidence boundary.
