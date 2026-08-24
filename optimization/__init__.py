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
from .survival import (
    EmpiricalSurvivalProvider,
    SessionLifecycle,
    SessionTelemetry,
    SurvivalTable,
    extract_session_lifecycles,
    kaplan_meier_table,
    static_survival_table, load_survival_guardrail_evidence,
    load_survival_tables, write_survival_tables,
)
from .predrain_flow import PreDrainFlowConfig, PreDrainFlowResult, solve_predrain_flow
from .exposure_guard import ExposureGuardConfig, ExposureGuardDecision, guard_allocation
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
    "EmpiricalSurvivalProvider",
    "SessionLifecycle",
    "SessionTelemetry",
    "SurvivalTable",
    "extract_session_lifecycles",
    "kaplan_meier_table",
    "static_survival_table",
    "load_survival_tables",
    "load_survival_guardrail_evidence",
    "write_survival_tables",
    "PreDrainFlowConfig",
    "PreDrainFlowResult",
    "ExposureGuardConfig", "ExposureGuardDecision", "guard_allocation",
    "solve_predrain_flow",
]
