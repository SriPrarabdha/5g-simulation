"""Notebook-facing helpers for preflight, PBS submission, analysis and fallbacks."""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def preflight(personal_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(personal_root or os.environ.get("CDOT_WORKSHOP_OUTPUT", ROOT / "output" / "workshop" / getpass.getuser()))
    root.mkdir(parents=True, exist_ok=True)
    probe = root / ".write-probe"; probe.write_text("ok\n", encoding="utf-8"); probe.unlink()
    imports = {name: importlib.util.find_spec(name) is not None for name in ("numpy", "scipy", "pyarrow", "pyscipopt", "IPython")}
    commands = {name: shutil.which(name) for name in ("qsub", "qstat", "scip", "parascip")}
    return {"schema_version": "workshop-preflight/1.0", "user": getpass.getuser(), "personal_root": str(root.resolve()),
            "personal_root_writable": os.access(root, os.W_OK), "shared_project": str(ROOT), "shared_project_readable": os.access(ROOT, os.R_OK),
            "python_imports": imports, "commands": commands, "webgl_check": "Run the browser WebGL2 cell below",
            "solver_readiness": "ready" if imports["pyscipopt"] and commands["scip"] and commands["parascip"] else "blocking-readiness-failure"}


def submit_pbs(script: str | Path, *, variables: dict[str, str]) -> dict[str, Any]:
    qsub = shutil.which("qsub")
    if not qsub:
        return {"submitted": False, "reason": "PBS unavailable; use the supplied personal result", "fallback": True}
    assignment = ",".join(f"{key}={value}" for key, value in sorted(variables.items()))
    try:
        answer = subprocess.run([qsub, "-v", assignment, str(script)], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        return {
            "submitted": False,
            "reason": f"PBS submission failed; continue with the labelled local path: {detail}",
            "fallback": True,
            "qsub_returncode": error.returncode,
        }
    return {"submitted": True, "job_id": answer.stdout.strip(), "fallback": False}


def execution_summary(checks: dict[str, Any] | None = None) -> dict[str, Any]:
    """Give a notebook-friendly, honest summary of the available execution paths."""
    checks = checks or preflight()
    commands = checks.get("commands", {})
    imports = checks.get("python_imports", {})
    return {
        "local_teaching_path": "ready" if imports.get("scipy") else "missing scipy",
        "pbs_submission": "ready" if commands.get("qsub") else "unavailable — local path remains runnable",
        "scip_assignment_mip": "ready" if imports.get("pyscipopt") and commands.get("scip") else "cluster module required",
        "parascip_presenter_demo": "ready" if commands.get("parascip") else "not available in this session",
        "evidence_mode": "synthetic shadow only",
    }


def pbs_status(job_id: str) -> dict[str, Any]:
    """Return one non-blocking PBS status snapshot; never busy-wait in a notebook."""
    qstat = shutil.which("qstat")
    if not qstat:
        return {"job_id": job_id, "available": False, "state": "unknown", "reason": "qstat unavailable"}
    answer = subprocess.run([qstat, "-f", str(job_id)], check=False, capture_output=True, text=True)
    status_text = answer.stdout or answer.stderr
    state = "unknown"
    for line in status_text.splitlines():
        if "job_state =" in line:
            state = line.split("=", 1)[1].strip()
            break
    return {
        "job_id": job_id,
        "available": True,
        "state": state,
        "qstat_returncode": answer.returncode,
        "terminal": answer.returncode != 0 or state in {"F", "C", "E"},
    }


def analyze_parquet(path: str | Path) -> dict[str, Any]:
    import pyarrow.parquet as pq
    rows = pq.read_table(path).to_pylist()
    result = {"steps": len(rows), "offered_mbit": 0.0, "carried_mbit": 0.0, "loss_mbit": 0.0,
              "overload_mbit": 0.0, "max_utilization": 0.0, "policy_ids": [], "solver_status": "recorded in decision trace",
              "decision_latency_ms": None, "series": []}
    for row in rows:
        interval = (row["window_end"] - row["window_start"]).total_seconds(); offered = carried = loss = overload = 0.0
        utilization = 0.0
        for upf in row["upfs"]:
            direction = upf["ul"]; offered += direction["offered_bytes"] * 8 / 1e6
            carried += direction["carried_bytes"] * 8 / 1e6; loss += (direction["dropped_bytes"] + direction["rejected_bytes"]) * 8 / 1e6
            utilization = max(utilization, direction["offered_bytes"] * 8 / 1e6 / interval / max(direction["safe_capacity_mbps"], 1e-12))
            overload += max(0, direction["offered_bytes"] * 8 / 1e6 - direction["safe_capacity_mbps"] * interval)
        result["offered_mbit"] += offered; result["carried_mbit"] += carried; result["loss_mbit"] += loss
        result["overload_mbit"] += overload; result["max_utilization"] = max(result["max_utilization"], utilization)
        if row["policy_id"] not in result["policy_ids"]: result["policy_ids"].append(row["policy_id"])
        result["series"].append({"step": row["step"], "offered_mbps": offered / interval,
                                 "carried_mbps": carried / interval, "loss_mbps": loss / interval,
                                 "overload_mbps": overload / interval, "utilization": utilization})
    return result


def metric_svg(analysis: dict[str, Any], width: int = 900, height: int = 320) -> str:
    series = analysis["series"]; maximum = max([row["offered_mbps"] for row in series] + [1]) * 1.08
    def points(key: str) -> str:
        return " ".join(f"{54 + i * (width - 78) / max(1, len(series)-1):.1f},{24 + (height-70)*(1-row[key]/maximum):.1f}" for i, row in enumerate(series))
    return f'''<svg viewBox="0 0 {width} {height}" style="max-width:100%;background:#071f28"><polyline points="{points('offered_mbps')}" fill="none" stroke="#bd8cff" stroke-width="3"/><polyline points="{points('carried_mbps')}" fill="none" stroke="#52d8cf" stroke-width="3"/><text x="54" y="{height-18}" fill="#d5e6e9">offered (violet) · carried (teal) · UL Mbps →</text></svg>'''


def supplied_result(name: str) -> Path:
    path = ROOT / "workshop" / "fallback" / name
    if not path.exists(): raise FileNotFoundError(f"supplied fallback is missing: {path}")
    return path
