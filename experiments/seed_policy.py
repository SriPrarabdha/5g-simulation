"""Fail-closed seed partitions for the control-science campaign."""

from __future__ import annotations


FORECAST_SEEDS = {"train": frozenset({46001}), "selection": frozenset({46002}), "test": frozenset({46003})}
MPC_SEEDS = {
    "development": frozenset(range(46101, 46113)),
    "validation": frozenset(range(46201, 46217)),
    "release": frozenset(range(46301, 46331)),
}


def require_forecast_seed(seed: int, purpose: str) -> None:
    if purpose not in FORECAST_SEEDS or seed not in FORECAST_SEEDS[purpose]:
        expected = sorted(FORECAST_SEEDS.get(purpose, ()))
        raise ValueError(f"forecast purpose {purpose!r} requires exact seed set {expected}; got {seed}")


def require_mpc_seeds(seeds: list[int], stage: str) -> None:
    if stage not in MPC_SEEDS:
        raise ValueError(f"unknown MPC seed stage: {stage}")
    actual = frozenset(seeds)
    expected = MPC_SEEDS[stage]
    if len(seeds) != len(actual) or actual != expected:
        raise ValueError(
            f"MPC stage {stage!r} requires each frozen seed exactly once: "
            f"{min(expected)}-{max(expected)}"
        )


def reject_protected_mpc_seeds(seeds: list[int]) -> None:
    protected = MPC_SEEDS["validation"] | MPC_SEEDS["release"]
    overlap = sorted(protected.intersection(seeds))
    if overlap:
        raise ValueError(f"development path cannot consume protected validation/release seeds: {overlap}")
