from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import tempfile
from pathlib import Path
from typing import Any

from .run_campaign_shard import atomic_json


CAP_NET_ADMIN = 12
CAP_BPF = 39


def effective_capabilities() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("CapEff:"):
                return int(line.split()[1], 16)
    except (OSError, ValueError, IndexError):
        pass
    return None


def item(status: str, detail: str) -> dict[str, str]:
    return {"status": status, "detail": detail}


def probe() -> dict[str, Any]:
    caps = effective_capabilities()
    nodefile = os.environ.get("PBS_NODEFILE")
    nodes: list[str] = []
    if nodefile and Path(nodefile).is_file():
        nodes = sorted(set(Path(nodefile).read_text().splitlines()))
    local_temp = Path(os.environ.get("PBS_JOBFS") or os.environ.get("TMPDIR") or tempfile.gettempdir())

    checks = {
        "pbs_job": item("pass" if os.environ.get("PBS_JOBID") else "unknown", os.environ.get("PBS_JOBID", "not running inside PBS")),
        "pbs_nodes": item("pass" if nodes else "unknown", ",".join(nodes) if nodes else "PBS_NODEFILE unavailable"),
        "container_runtime": item(
            "pass" if any(shutil.which(name) for name in ("apptainer", "singularity", "podman", "docker")) else "unknown",
            ",".join(name for name in ("apptainer", "singularity", "podman", "docker") if shutil.which(name)) or "none on PATH",
        ),
        "tun_tap": item("pass" if os.access("/dev/net/tun", os.R_OK | os.W_OK) else "unknown", "/dev/net/tun access"),
        "sctp": item("pass" if Path("/proc/net/sctp").exists() else "unknown", "/proc/net/sctp"),
        "gtp5g": item("pass" if Path("/sys/module/gtp5g").exists() else "unknown", "/sys/module/gtp5g"),
        "cap_net_admin": item("unknown" if caps is None else ("pass" if caps & (1 << CAP_NET_ADMIN) else "fail"), "effective capability bit 12"),
        "cap_bpf": item("unknown" if caps is None else ("pass" if caps & (1 << CAP_BPF) else "fail"), "effective capability bit 39"),
        "ebpf_tooling": item("pass" if shutil.which("bpftool") else "unknown", shutil.which("bpftool") or "bpftool not on PATH"),
        "local_scratch": item("pass" if local_temp.is_dir() and os.access(local_temp, os.W_OK) else "fail", str(local_temp)),
        "inter_node_udp_tcp": item("unknown", "requires an explicit two-node active socket probe"),
    }
    return {
        "schema_version": "cluster-capability-probe/1.0",
        "host": socket.gethostname(),
        "job_id": os.environ.get("PBS_JOBID"),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Record the non-destructive C-DOT capability gate")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = probe()
    atomic_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
