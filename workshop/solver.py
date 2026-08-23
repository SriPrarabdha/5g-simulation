"""Bounded teaching optimizations used by the C-DOT workshop.

The tiny allocation model is deliberately solved twice.  HiGHS is available
through SciPy; SCIP is optional and must be provided by the workshop cluster.
There is no silent solver substitution.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
FROZEN_MIP = ROOT / "workshop" / "data" / "national_assignment_mip.json"


@dataclass(frozen=True, slots=True)
class AllocationResult:
    schema_version: str
    solver: str
    status: str
    objective: float
    allocation_mbps: dict[str, float]
    overload_slack_mbps: dict[str, float]
    routing_weights: dict[str, float]
    solve_seconds: float
    demand_mbps: float
    synthetic: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def teaching_problem(
    *, demand_mbps: float = 260.0,
    residual_capacity_mbps: Mapping[str, float] | None = None,
    eligible_upfs: Sequence[str] = ("upf-a", "upf-b", "upf-c"),
) -> dict[str, Any]:
    capacity = dict(residual_capacity_mbps or {"upf-a": 60.0, "upf-b": 80.0, "upf-c": 110.0})
    if not math.isfinite(demand_mbps) or demand_mbps <= 0:
        raise ValueError("demand_mbps must be positive and finite")
    unknown = set(eligible_upfs) - set(capacity)
    if unknown:
        raise ValueError(f"eligible UPFs have no capacity: {sorted(unknown)}")
    latency = {"upf-a": 7.0, "upf-b": 8.0, "upf-c": 4.0}
    return {
        "demand_mbps": float(demand_mbps),
        "capacity_mbps": capacity,
        "eligible_upfs": list(eligible_upfs),
        "latency_cost": {key: latency.get(key, 10.0) for key in capacity},
        "overload_penalty": 1000.0,
    }


def _result(problem: Mapping[str, Any], solver: str, status: str, objective: float,
            allocation: Mapping[str, float], slack: Mapping[str, float], elapsed: float) -> AllocationResult:
    demand = float(problem["demand_mbps"])
    weights = {key: max(0.0, float(allocation[key])) / demand for key in allocation}
    total = sum(weights.values())
    if total <= 0:
        raise RuntimeError("solver returned no allocation")
    weights = {key: value / total for key, value in weights.items()}
    return AllocationResult(
        "workshop-solver-result/1.0", solver, status, float(objective),
        {key: float(value) for key, value in allocation.items()},
        {key: float(value) for key, value in slack.items()}, weights, elapsed, demand,
    )


def solve_teaching_lp(problem: Mapping[str, Any] | None = None, *, solver: str = "highs") -> AllocationResult:
    problem = dict(problem or teaching_problem())
    upfs = sorted(problem["capacity_mbps"])
    eligible = set(problem["eligible_upfs"])
    start = time.perf_counter()
    if solver == "highs":
        from scipy.optimize import linprog

        # Variables are allocation x_u followed by overload slack s_u.
        c = [float(problem["latency_cost"][upf]) for upf in upfs] + [float(problem["overload_penalty"])] * len(upfs)
        a_ub, b_ub = [], []
        for index, upf in enumerate(upfs):
            row = [0.0] * (2 * len(upfs))
            row[index], row[len(upfs) + index] = 1.0, -1.0
            a_ub.append(row)
            b_ub.append(float(problem["capacity_mbps"][upf]))
        bounds = [(0.0, None) if upf in eligible else (0.0, 0.0) for upf in upfs] + [(0.0, None)] * len(upfs)
        answer = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=[[1.0] * len(upfs) + [0.0] * len(upfs)],
                         b_eq=[float(problem["demand_mbps"])], bounds=bounds, method="highs")
        if not answer.success:
            raise RuntimeError(f"HiGHS failed: {answer.message}")
        allocation = dict(zip(upfs, answer.x[:len(upfs)]))
        slack = dict(zip(upfs, answer.x[len(upfs):]))
        return _result(problem, "HiGHS", "optimal", answer.fun, allocation, slack, time.perf_counter() - start)
    if solver != "scip":
        raise ValueError("solver must be 'highs' or 'scip'")
    try:
        from pyscipopt import Model, quicksum
    except ImportError as error:
        raise RuntimeError("SCIP unavailable: load the SCIP module and install matching PySCIPOpt; no fallback was used") from error
    model = Model("cdot-teaching-allocation")
    model.hideOutput()
    x = {upf: model.addVar(f"x[{upf}]", lb=0, ub=None if upf in eligible else 0) for upf in upfs}
    slack = {upf: model.addVar(f"slack[{upf}]", lb=0) for upf in upfs}
    model.addCons(quicksum(x.values()) == float(problem["demand_mbps"]))
    for upf in upfs:
        model.addCons(x[upf] <= float(problem["capacity_mbps"][upf]) + slack[upf])
    model.setObjective(quicksum(float(problem["latency_cost"][u]) * x[u] + float(problem["overload_penalty"]) * slack[u] for u in upfs), "minimize")
    model.optimize()
    if model.getStatus() != "optimal":
        raise RuntimeError(f"SCIP failed: status={model.getStatus()}")
    return _result(problem, "SCIP", "optimal", model.getObjVal(),
                   {u: model.getVal(x[u]) for u in upfs}, {u: model.getVal(slack[u]) for u in upfs},
                   time.perf_counter() - start)


def compare_lp_solvers(problem: Mapping[str, Any] | None = None, *, tolerance: float = 1e-6) -> dict[str, Any]:
    highs = solve_teaching_lp(problem, solver="highs")
    scip = solve_teaching_lp(problem, solver="scip")
    difference = abs(highs.objective - scip.objective)
    if difference > tolerance * max(1.0, abs(highs.objective)):
        raise RuntimeError(f"solver objective mismatch: {difference}")
    return {"equivalent": True, "tolerance": tolerance, "objective_difference": difference,
            "highs": highs.to_dict(), "scip": scip.to_dict()}


def write_solver_result(result: AllocationResult, output: str | Path) -> Path:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def build_frozen_mip(path: str | Path = FROZEN_MIP) -> Path:
    """Create the deterministic 24-UPF/96-group assignment-activation MIP data."""
    upfs = [
        {"id": f"upf-{index + 1:02d}", "capacity_mbps": 680 + 35 * (index % 6),
         "activation_cost": 85 + 4 * (index % 5)} for index in range(24)
    ]
    groups = []
    for index in range(96):
        start = (index * 5 + index // 7) % 24
        eligible = [upfs[(start + offset) % 24]["id"] for offset in (0, 1, 4, 9)]
        groups.append({"id": f"group-{index + 1:03d}", "demand_mbps": 72 + 7 * (index % 9),
                       "eligible_upfs": eligible,
                       "latency_cost": {upf: 2 + ((index * 3 + int(upf[-2:])) % 17) for upf in eligible}})
    payload = {"schema_version": "workshop-assignment-mip/1.0", "synthetic": True, "seed": 20260822,
               "description": "Frozen national-scale teaching MIP: binary group assignment and UPF activation",
               "upfs": upfs, "groups": groups}
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination
