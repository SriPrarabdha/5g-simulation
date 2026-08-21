from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecasting import load_forecaster_bundle
from optimization import (
    CohortMPCConfig, OptimizationConfig, load_survival_guardrail_evidence,
    load_survival_tables,
)
from simulator.macro import (
    AuditSink, CompositeSink, DecisionTraceSink, JsonlSink, ParquetSink, Simulator,
    controller_by_name, load_scenario,
)
from simulator.macro.checkpoint import CheckpointManager, canonical_sha256
from simulator.macro.controllers import ForecastAdjustmentConfig
from steering import PolicyGateConfig

from .artifacts import (
    ArtifactPolicy,
    assign_retention,
    atomic_json,
    topology_identity,
    validate_published_shard,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_sha256(path: Path) -> str:
    if path.is_file():
        return file_sha256(path)
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(str(item.relative_to(path)).encode())
        digest.update(bytes.fromhex(file_sha256(item)))
    return digest.hexdigest()


def git_commit(project_root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def source_fingerprint(project_root: Path) -> str:
    digest = hashlib.sha256()
    for package in ("simulator", "schemas", "steering", "forecasting", "optimization", "experiments"):
        for path in sorted((project_root / package).rglob("*.py")):
            relative = path.relative_to(project_root).as_posix()
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def shard_directory(
    output_root: Path, campaign_id: str, scenario_id: str, controller: str, seed: int
) -> Path:
    return (
        output_root / "schema_major=2" / f"campaign={campaign_id}"
        / f"scenario={scenario_id}" / f"controller={controller}" / f"seed={seed:06d}"
    )


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _identity(path: Path | None, payload: dict[str, Any] | None, identity_key: str) -> dict[str, Any] | None:
    if path is None or payload is None:
        return None
    return {
        "path": str(path.resolve()), "file_sha256": file_sha256(path),
        identity_key: payload.get(identity_key),
    }


def _scratch_root(explicit: Path | None) -> Path:
    if explicit is not None:
        root = explicit
    elif os.environ.get("PBS_JOBFS"):
        root = Path(os.environ["PBS_JOBFS"])
    elif os.environ.get("TMPDIR"):
        root = Path(os.environ["TMPDIR"])
    elif not os.environ.get("PBS_JOBID"):
        root = Path(tempfile.gettempdir())
    else:
        raise RuntimeError("PBS execution requires job-local scratch in PBS_JOBFS or TMPDIR")
    root.mkdir(parents=True, exist_ok=True)
    if not os.access(root, os.W_OK):
        raise RuntimeError(f"job-local scratch is not writable: {root}")
    minimum = int(os.environ.get("STAGE1_MIN_SCRATCH_BYTES", str(64 * 1024 * 1024)))
    filesystem = os.statvfs(root)
    available = filesystem.f_bavail * filesystem.f_frsize
    if available < minimum:
        raise RuntimeError(
            f"job-local scratch has {available} bytes free; Stage 1 requires at least {minimum}"
        )
    return root


def _tree_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _artifact(path: Path, kind: str, *, rows: int | None = None, row_groups: int | None = None) -> dict[str, Any]:
    return {
        "kind": kind, "path": path.name, "sha256": file_sha256(path),
        "bytes": path.stat().st_size, "rows": rows, "row_groups": row_groups,
    }


def run_shard(
    manifest: Path,
    output_root: Path,
    campaign_id: str,
    seed: int,
    skip_existing: bool = False,
    controller: str = "static",
    progress_every_simulated_hours: float | None = None,
    forecast_bundle: Path | None = None,
    predictive_profile: Path | None = None,
    mpc_profile: Path | None = None,
    survival_bundle: Path | None = None,
    *,
    artifact_policy: ArtifactPolicy | None = None,
    scratch_root: Path | None = None,
    resume: bool = True,
    stage_out_semaphore: Any | None = None,
    stop_event: Any | None = None,
) -> Path:
    project_root = Path(__file__).resolve().parent.parent
    policy = artifact_policy or ArtifactPolicy()
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    topology_id = topology_identity(manifest_payload)
    base_config = load_scenario(manifest)
    config = replace(base_config, seed=seed)
    trained_forecaster = load_forecaster_bundle(forecast_bundle) if forecast_bundle else None
    if trained_forecaster is not None:
        trained_forecaster.validate_groups(group.key for group in config.groups)
    if predictive_profile is not None and controller not in {"predictive", "forecast-capacity"}:
        raise ValueError("a predictive profile can only be used with a forecast controller")
    if mpc_profile is not None and controller != "mpc":
        raise ValueError("an MPC profile can only be used with the MPC controller")
    profile_payload = json.loads(predictive_profile.read_text()) if predictive_profile else None
    mpc_payload = json.loads(mpc_profile.read_text()) if mpc_profile else None
    if profile_payload and profile_payload.get("schema_version") != "predictive-controller-profile/1.0":
        raise ValueError("unsupported predictive controller profile schema")
    if mpc_payload and mpc_payload.get("schema_version") != "cohort-mpc-profile/1.0":
        raise ValueError("unsupported cohort MPC profile schema")
    gate = PolicyGateConfig(**profile_payload.get("gate", {})) if profile_payload else None
    optimization = OptimizationConfig(**profile_payload.get("optimization", {})) if profile_payload else None
    adjustment = ForecastAdjustmentConfig(**profile_payload.get("forecast_adjustment", {})) if profile_payload else None
    mpc_config = CohortMPCConfig(**mpc_payload.get("mpc", {})) if mpc_payload else None
    survival_tables = load_survival_tables(str(survival_bundle)) if survival_bundle else None
    simulator = Simulator(config, controller_by_name(
        controller, forecaster=trained_forecaster, gate_config=gate,
        optimization_config=optimization,
        optimizer_weight=float(profile_payload.get("optimizer_weight", 1.0)) if profile_payload else 1.0,
        forecast_adjustment_config=adjustment, mpc_config=mpc_config,
        survival_by_group=survival_tables,
        survival_guardrail_evidence=(
            load_survival_guardrail_evidence(str(survival_bundle))
            if survival_bundle else None
        ),
    ))
    decision = assign_retention(
        policy, topology_id=topology_id, scenario_id=config.scenario_id, seed=seed
    )
    destination = shard_directory(
        output_root, campaign_id, config.scenario_id, simulator.controller.name, seed
    )
    manifest_digest = file_sha256(manifest)
    forecast_identity = (
        {
            "path": str(forecast_bundle.resolve()), "file_sha256": artifact_sha256(forecast_bundle),
            "bundle_sha256": trained_forecaster.metadata["bundle_sha256"],
            "model_version": trained_forecaster.model_version,
        } if forecast_bundle and trained_forecaster else None
    )
    profile_identity = _identity(predictive_profile, profile_payload, "profile_id")
    mpc_identity = _identity(mpc_profile, mpc_payload, "profile_id")
    survival_identity = (
        {"path": str(survival_bundle.resolve()), "file_sha256": file_sha256(survival_bundle)}
        if survival_bundle else None
    )
    if destination.exists():
        if not skip_existing:
            raise FileExistsError(f"refusing to overwrite shard: {destination}")
        existing = validate_published_shard(destination)
        expected = {
            "manifest_sha256": manifest_digest, "controller": simulator.controller.name,
            "forecast_bundle": forecast_identity, "predictive_profile": profile_identity,
            "mpc_profile": mpc_identity, "survival_bundle": survival_identity,
            "artifact_policy": policy.to_dict(),
            "retention": decision.to_dict(),
        }
        if any(existing.get(key) != value for key, value in expected.items()):
            raise FileExistsError(f"existing shard does not match this work item: {destination}")
        return destination

    scratch_base = _scratch_root(scratch_root)
    work_identity = canonical_sha256({
        "campaign": campaign_id, "scenario": config.scenario_id, "seed": seed,
        "controller": simulator.controller.name, "policy": policy.to_dict(),
        "manifest": manifest_digest, "forecast": forecast_identity,
        "predictive_profile": profile_identity, "mpc_profile": mpc_identity,
        "survival_bundle": survival_identity,
    })
    scratch = scratch_base / "cdot-stage1" / work_identity[:24]
    scratch.mkdir(parents=True, exist_ok=True)
    source_digest = source_fingerprint(project_root)
    fingerprints = {
        "manifest_sha256": manifest_digest, "artifact_policy": policy.to_dict(),
        "model": forecast_identity, "predictive_profile": profile_identity,
        "mpc_profile": mpc_identity, "survival_bundle": survival_identity,
        "source_fingerprint": source_digest,
        "topology_id": topology_id, "scenario_id": config.scenario_id,
        "seed": seed, "controller": simulator.controller.name,
    }
    summary_sink = simulator.make_summary_sink()
    audit_mode = "parquet" if decision.tier == "gold" else "count"
    audit_sink = AuditSink(
        audit_mode, scratch_directory=scratch / "audit-segments",
        salt=policy.salt, row_group_size=policy.row_group_size,
    )
    sinks: list[Any] = [summary_sink, audit_sink]
    parquet_sink: ParquetSink | None = None
    decision_sink: DecisionTraceSink | None = None
    if decision.tier in {"silver", "gold"}:
        parquet_sink = ParquetSink(
            scratch / "step-segments", controller=simulator.controller.name,
            row_group_size=policy.row_group_size,
        )
        sinks.append(parquet_sink)
    if decision.tier == "gold":
        decision_sink = DecisionTraceSink(
            scratch / "decision-segments", row_group_size=policy.row_group_size
        )
        sinks.append(decision_sink)
    jsonl_sink: JsonlSink | None = None
    if policy.jsonl_enabled:
        jsonl_sink = JsonlSink(scratch / "run.jsonl")
        sinks.append(jsonl_sink)
    composite = CompositeSink(sinks)
    checkpoints = CheckpointManager(
        scratch / "checkpoints", step_seconds=config.step_seconds,
        interval_simulated_seconds=policy.checkpoint_interval_seconds,
        fingerprints=fingerprints, resume=resume,
        stop_event=stop_event,
    )
    progress_steps = None
    if progress_every_simulated_hours is not None:
        if progress_every_simulated_hours <= 0:
            raise ValueError("progress_every_simulated_hours must be positive")
        progress_steps = max(1, round(progress_every_simulated_hours * 3600 / config.step_seconds))
    started = time.monotonic()
    cpu_started = time.process_time()

    def log(message: str) -> None:
        if progress_every_simulated_hours is not None:
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            print(f"[{stamp}] {message}", flush=True)

    def progress(completed: int, total: int) -> None:
        elapsed = time.monotonic() - started
        rate = completed / elapsed if elapsed else 0.0
        eta = (total - completed) / rate if rate else None
        log(
            f"phase=simulate status=running step={completed}/{total} "
            f"elapsed={_duration(elapsed)} eta={_duration(eta)}"
        )

    old_handlers: dict[int, Any] = {}
    if __import__("threading").current_thread() is __import__("threading").main_thread():
        for number in (signal.SIGTERM, signal.SIGINT):
            old_handlers[number] = signal.getsignal(number)
            signal.signal(number, lambda _signum, _frame: checkpoints.request_stop())
    try:
        outcome = simulator.run(
            composite, checkpoint_manager=checkpoints,
            progress_interval_steps=progress_steps,
            progress_callback=progress if progress_steps else None,
        )
    finally:
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
    if not outcome.completed:
        raise InterruptedError(f"shard checkpointed at step {outcome.step_count}; scratch retained at {scratch}")

    stage_parent = destination.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    staging = stage_parent / f".{destination.name}.staging-{os.getpid()}-{work_identity[:8]}"
    if staging.exists():
        raise FileExistsError(f"stale publication staging directory exists: {staging}")
    staging.mkdir()
    stage_started = time.monotonic()
    semaphore_context = stage_out_semaphore if stage_out_semaphore is not None else nullcontext()
    with semaphore_context:
        atomic_json(staging / "summary.json", outcome.summary)
        atomic_json(staging / "audit-counts.json", {
            "schema_version": "audit-counts/1.0", "mode": audit_mode,
            "selection_audits": audit_sink.count, "retained_rows": audit_sink.retained,
        })
        atomic_json(staging / "provenance.json", {
            "schema_version": "simulation-provenance/1.0", "fingerprints": fingerprints,
            "fingerprint_sha256": canonical_sha256(fingerprints),
        })
        artifacts: list[dict[str, Any]] = [
            _artifact(staging / "summary.json", "summary"),
            _artifact(staging / "audit-counts.json", "audit_counts"),
            _artifact(staging / "provenance.json", "provenance"),
        ]
        if parquet_sink is not None:
            descriptor = parquet_sink.finalize(staging / "run.parquet", outcome.summary)
            artifacts.append(descriptor.to_dict())
        if decision.tier == "gold":
            descriptor = audit_sink.finalize(staging / "selection-audits.parquet")
            artifacts.append(descriptor.to_dict())
            assert decision_sink is not None
            descriptor = decision_sink.finalize(staging / "decision-traces.parquet")
            artifacts.append(descriptor.to_dict())
        if jsonl_sink is not None:
            shutil.copyfile(scratch / "run.jsonl", staging / "run.jsonl")
            artifacts.append(_artifact(staging / "run.jsonl", "debug_jsonl"))
        scratch_bytes = _tree_bytes(scratch)
        cpu_seconds = time.process_time() - cpu_started
        peak_rss_bytes = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)
        performance = {
            "schema_version": "simulation-performance/1.0",
            "wall_seconds": time.monotonic() - started,
            "cpu_seconds": cpu_seconds, "peak_rss_bytes": peak_rss_bytes,
            "scratch_bytes": scratch_bytes, "phase_timings": outcome.timings,
        }
        atomic_json(staging / "performance.json", performance)
        artifacts.append(_artifact(staging / "performance.json", "performance"))
        for artifact in artifacts:
            candidate = staging / artifact["path"]
            if file_sha256(candidate) != artifact["sha256"]:
                raise RuntimeError(f"artifact changed during verification: {candidate}")
    stage_out_seconds = time.monotonic() - stage_started
    if destination.exists():
        raise FileExistsError(f"publication target appeared during stage-out: {destination}")
    os.replace(staging, destination)
    metadata = {
        "schema_version": "experiment-shard/2.0", "campaign_id": campaign_id,
        "topology_id": topology_id, "scenario_id": config.scenario_id, "seed": seed,
        "controller": simulator.controller.name, "forecast_bundle": forecast_identity,
        "predictive_profile": profile_identity, "mpc_profile": mpc_identity,
        "survival_bundle": survival_identity,
        "manifest": str(manifest.resolve()), "manifest_sha256": manifest_digest,
        "artifact_policy": policy.to_dict(), "retention": decision.to_dict(),
        "checkpoint_lineage": checkpoints.lineage, "artifacts": artifacts,
        "source_fingerprint": source_digest, "git_commit": git_commit(project_root),
        "component_versions": {"cdot_upf_simulation": "0.2.0", "python": platform.python_version()},
        "host": socket.gethostname(), "job_id": os.environ.get("PBS_JOBID"),
        "array_index": os.environ.get("PBS_ARRAY_INDEX"),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary": outcome.summary, "step_count": outcome.step_count,
        "audit_count": outcome.audit_count, "completion_status": outcome.completion_status,
        "peak_rss_bytes": peak_rss_bytes, "cpu_seconds": cpu_seconds,
        "scratch_bytes": scratch_bytes, "stage_out_seconds": stage_out_seconds,
    }
    atomic_json(destination / "metadata.json", metadata)
    validate_published_shard(destination)
    # The verified shared commit is now the authority. Successful scratch is
    # disposable; interrupted runs return above and retain it for exact resume.
    if scratch.parent.name == "cdot-stage1" and len(scratch.name) == 24:
        shutil.rmtree(scratch)
    log(f"phase=complete status=published tier={decision.tier} metadata={destination / 'metadata.json'}")
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one streaming deterministic macro-campaign shard")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-root", default=Path("output/macro"), type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--progress-every-simulated-hours", type=float, default=12.0)
    parser.add_argument("--controller", choices=("static", "reactive", "forecast-capacity", "predictive", "mpc", "oracle"), default="static")
    parser.add_argument("--forecast-bundle", type=Path)
    parser.add_argument("--mpc-profile", type=Path)
    parser.add_argument("--survival-bundle", type=Path)
    parser.add_argument("--predictive-profile", type=Path)
    parser.add_argument("--artifact-policy", type=Path)
    parser.add_argument("--scratch-root", type=Path)
    parser.add_argument("--no-resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    policy = ArtifactPolicy.from_dict(json.loads(args.artifact_policy.read_text())) if args.artifact_policy else None
    destination = run_shard(
        args.manifest, args.output_root, args.campaign_id, args.seed,
        skip_existing=args.skip_existing, controller=args.controller,
        progress_every_simulated_hours=args.progress_every_simulated_hours,
        forecast_bundle=args.forecast_bundle, predictive_profile=args.predictive_profile,
        mpc_profile=args.mpc_profile, artifact_policy=policy,
        survival_bundle=args.survival_bundle,
        scratch_root=args.scratch_root, resume=not args.no_resume,
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
