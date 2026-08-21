# Invalidated Phase 2 attempts

These artifacts are retained for audit only and must not be used for model
selection or deployment:

- `train-cache/`, `selection-cache/`, `trained/`, `smoke/`, and
  `smoke-selection/`: telemetry quality was aggregated across every UPF and/or
  every scrape in a decision bucket instead of the live contract's latest
  scrape for the selection group's eligible UPFs.
- `train-cache-v2/` and `selection-cache-v2/`: eligibility was corrected, but
  transient telemetry flags were still unioned across the bucket.

Jobs 3471–3479 and 3480–3481 were cancelled or invalidated immediately after
the contract errors were identified. No validation/release MPC seed or forecast
test seed 46003 was consumed. The authoritative cache generation begins with
`train-cache-v3/` and `selection-cache-v3/`.
