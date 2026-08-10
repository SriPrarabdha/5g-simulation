"""Constrained predictive allocation using SciPy's HiGHS backend."""

from .highs import OptimizationConfig, OptimizationResult, solve_allocation
from .cohort_mpc import (
    ActiveCohort,
    CohortMPCConfig,
    CohortMPCResult,
    MPCCertificate,
    MPCMetrics,
    bucket_survival,
    solve_cohort_mpc,
)
from .oracle_bounds import (
    OracleBoundResult,
    OracleMetrics,
    bucket_arrivals_from_steps,
    evaluate_allocation,
    expected_bucket_arrivals,
    solve_bounded_migration_bound,
    solve_new_session_bound,
    static_capacity_allocation,
)

__all__ = [
    "OptimizationConfig",
    "OptimizationResult",
    "ActiveCohort",
    "CohortMPCConfig",
    "CohortMPCResult",
    "MPCCertificate",
    "MPCMetrics",
    "OracleBoundResult",
    "OracleMetrics",
    "bucket_arrivals_from_steps",
    "evaluate_allocation",
    "expected_bucket_arrivals",
    "solve_allocation",
    "solve_cohort_mpc",
    "solve_bounded_migration_bound",
    "solve_new_session_bound",
    "static_capacity_allocation",
    "bucket_survival",
]
