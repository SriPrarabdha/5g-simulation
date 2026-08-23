"""Structured entry points for tightly bounded workshop PBS jobs."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workshop.solver import FROZEN_MIP, solve_teaching_lp, teaching_problem, write_solver_result


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def provenance(kind: str, seed: int | None = None) -> dict[str, Any]:
    return {"schema_version": "workshop-job-status/1.0", "kind": kind, "synthetic": True,
            "user": getpass.getuser(), "job_id": os.environ.get("PBS_JOBID", "local"), "host": socket.gethostname(),
            "seed": seed, "python": sys.version.split()[0], "platform": platform.platform(),
            "started_at": datetime.now(timezone.utc).isoformat()}


def require_command(command: str, label: str) -> str:
    path = shutil.which(command)
    if not path:
        raise RuntimeError(f"{label} unavailable: expected command {command!r}; no fallback was used")
    return path


def run_solver(output_dir: Path, solver: str, demand: float) -> Path:
    status = provenance("solver") | {"state": "running", "solver": solver}
    atomic_json(output_dir / "status.json", status)
    try:
        result = solve_teaching_lp(teaching_problem(demand_mbps=demand), solver=solver)
        destination = write_solver_result(result, output_dir / "solver-result.json")
        atomic_json(output_dir / "reproducibility.json", status | {"problem": teaching_problem(demand_mbps=demand)})
        atomic_json(output_dir / "status.json", status | {"state": "completed", "finished_at": datetime.now(timezone.utc).isoformat()})
        return destination
    except Exception as error:
        atomic_json(output_dir / "status.json", status | {"state": "failed", "error": str(error),
                                                            "finished_at": datetime.now(timezone.utc).isoformat()})
        raise


def run_assignment_mip(output_dir: Path, *, parascip: bool = False) -> Path:
    """Solve the frozen MIP with explicit SCIP/ParaSCIP availability checks.

    The presenter ParaSCIP path invokes the site-provided command because its UG,
    MPI/PALS and PBS launch syntax is cluster-specific.  The command must emit the
    canonical result file; this wrapper never substitutes a local solver.
    """
    status = provenance("parascip" if parascip else "scip-mip") | {"state": "running", "mip": str(FROZEN_MIP)}
    atomic_json(output_dir / "status.json", status)
    try:
        if parascip:
            if os.environ.get("CDOT_WORKSHOP_PRESENTER") != "1":
                raise PermissionError("ParaSCIP submission is presenter-only")
            command = require_command(os.environ.get("PARASCIP_CMD", "parascip"), "ParaSCIP/UG")
            result = output_dir / "solver-result.json"
            subprocess.run([command, str(FROZEN_MIP), str(result), "20260822"], check=True)
            if not result.is_file():
                raise RuntimeError("ParaSCIP command completed without solver-result.json")
        else:
            try:
                from pyscipopt import Model, quicksum
            except ImportError as error:
                raise RuntimeError("SCIP unavailable: load SCIP and matching PySCIPOpt; no fallback was used") from error
            data = json.loads(FROZEN_MIP.read_text(encoding="utf-8")); model = Model("cdot-national-assignment")
            model.hideOutput(); model.setParam("randomization/randomseedshift", int(data["seed"]))
            upfs = {item["id"]: item for item in data["upfs"]}
            active = {upf: model.addVar(f"active[{upf}]", vtype="B") for upf in upfs}
            assign = {(group["id"], upf): model.addVar(f"assign[{group['id']},{upf}]", vtype="B")
                      for group in data["groups"] for upf in group["eligible_upfs"]}
            for group in data["groups"]:
                model.addCons(quicksum(assign[group["id"], upf] for upf in group["eligible_upfs"]) == 1)
                for upf in group["eligible_upfs"]: model.addCons(assign[group["id"], upf] <= active[upf])
            for upf, item in upfs.items():
                model.addCons(quicksum(group["demand_mbps"] * assign[group["id"], upf]
                                       for group in data["groups"] if upf in group["eligible_upfs"]) <= item["capacity_mbps"])
            model.setObjective(quicksum(upfs[u]["activation_cost"] * active[u] for u in upfs) +
                               quicksum(group["latency_cost"][u] * assign[group["id"], u]
                                        for group in data["groups"] for u in group["eligible_upfs"]), "minimize")
            started = time.perf_counter(); model.optimize()
            if model.getNSols() < 1: raise RuntimeError(f"SCIP MIP produced no incumbent: {model.getStatus()}")
            payload = {"schema_version": "workshop-mip-result/1.0", "solver": "SCIP", "status": str(model.getStatus()),
                       "objective": model.getObjVal(), "dual_bound": model.getDualbound(), "gap": model.getGap(),
                       "solve_seconds": time.perf_counter() - started, "seed": data["seed"],
                       "active_upfs": sorted(u for u in upfs if model.getVal(active[u]) > .5),
                       "assignments": {g["id"]: next(u for u in g["eligible_upfs"] if model.getVal(assign[g["id"], u]) > .5)
                                       for g in data["groups"]}}
            result = output_dir / "solver-result.json"; atomic_json(result, payload)
        atomic_json(output_dir / "reproducibility.json", status)
        atomic_json(output_dir / "status.json", status | {"state": "completed", "finished_at": datetime.now(timezone.utc).isoformat()})
        return result
    except Exception as error:
        atomic_json(output_dir / "status.json", status | {"state": "failed", "error": str(error),
                                                            "finished_at": datetime.now(timezone.utc).isoformat()})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    lp = sub.add_parser("solve-lp"); lp.add_argument("--output-dir", type=Path, required=True)
    lp.add_argument("--solver", choices=("highs", "scip"), default="scip"); lp.add_argument("--demand", type=float, default=260)
    mip = sub.add_parser("solve-mip"); mip.add_argument("--output-dir", type=Path, required=True); mip.add_argument("--parascip", action="store_true")
    args = parser.parse_args()
    path = run_solver(args.output_dir, args.solver, args.demand) if args.command == "solve-lp" else run_assignment_mip(args.output_dir, parascip=args.parascip)
    print(path); return 0


if __name__ == "__main__": raise SystemExit(main())
