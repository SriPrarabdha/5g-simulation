# Phase 2.1 implementation addendum

This versioned addendum leaves the sealed Phase 1/2 evidence unchanged.

Implemented before the Phase 3 interface freeze:

- The forecast headline is relabeled pooled cross-target WAPE. Target-separated and macro-target WAPE, sample counts, and group-cluster bootstrap intervals are recorded in `phase2-audit-addendum-v1.json`.
- The LightGBM detected-surge DL/30-minute slice is explicitly reported as n=9 observations across 8 groups, with a wide bootstrap interval.
- Empirical-survival status now fails closed unless a v2 survival bundle contains a measured imperfect-versus-oracle comparison and its criteria.
- The release gate checks DL overload, drops, establishment failures, solver timeout/error/infeasibility, unexpected fallback and skipped-decision budgets, normalized routing churn, and measured survival evidence.
- The generic MPC PBS wrapper forwards both the survival bundle and the Phase 3 interface freeze.
- A dedicated claim-before-use release runner and PBS wrapper cover seeds 46301–46330 exactly once and require passing frozen validation evidence.
- Calendar features separate simulator truth from explicit operational hints; demand magnitude is no longer inferred from the ground-truth event when a hint is absent. Bias and timing offsets are configurable for scheduled-capacity experiments.
- The Phase 3 freeze directly hashes the parallel runner and PBS wrappers in addition to Python simulation sources and candidate profiles.

Verification before freezing:

- Focused Phase 2.1/Phase 3 suite: 48 passed.
- Full project suite in the PBS `penv`: 163 passed; the sole failure is the previously known missing `output/models/extreme-oracle-bound-evaluation-v1.json` fixture.
- Bare login Python 3.13 is not the project environment and lacks FastAPI and qrcode; PBS and verification use `penv` Python 3.11.

Protected data status at freeze time:

- Forecast test seed 46003: untouched.
- MPC validation seeds 46201–46216: untouched.
- MPC release seeds 46301–46330: untouched.
