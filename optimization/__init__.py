"""Constrained predictive allocation using SciPy's HiGHS backend."""

from .highs import OptimizationConfig, OptimizationResult, solve_allocation

__all__ = ["OptimizationConfig", "OptimizationResult", "solve_allocation"]
