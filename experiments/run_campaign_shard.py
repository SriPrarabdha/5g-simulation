from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import socket
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecasting import TrainedForecastBundle
from optimization import CohortMPCConfig, OptimizationConfig
from simulator.macro import Simulator, controller_by_name, load_scenario
from simulator.macro.controllers import ForecastAdjustmentConfig
from steering import PolicyGateConfig


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(project_root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def shard_directory(output_root: Path, campaign_id: str, scenario_id: str, controller: str, seed: int) -> Path:
    return (
        output_root
        / "schema_major=1"
        / f"campaign={campaign_id}"
        / f"scenario={scenario_id}"
        / f"controller={controller}"
        / f"seed={seed:06d}"
    )


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


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
) -> Path:
    project_root = Path(__file__).resolve().parent.parent
    base_config = load_scenario(manifest)
    config = replace(base_config, seed=seed)
    trained_forecaster = (
        TrainedForecastBundle.load(forecast_bundle) if forecast_bundle is not None else None
    )
    if trained_forecaster is not None:
        trained_forecaster.validate_groups(group.key for group in config.groups)
    if predictive_profile is not None and controller not in {"predictive", "forecast-capacity"}:
        raise ValueError("a predictive profile can only be used with a forecast controller")
    if mpc_profile is not None and controller != "mpc":
        raise ValueError("an MPC profile can only be used with the MPC controller")
    profile_payload = (
        json.loads(predictive_profile.read_text(encoding="utf-8"))
        if predictive_profile is not None else None
    )
    if profile_payload is not None and profile_payload.get("schema_version") != "predictive-controller-profile/1.0":
        raise ValueError("unsupported predictive controller profile schema")
    mpc_profile_payload = (
        json.loads(mpc_profile.read_text(encoding="utf-8"))
        if mpc_profile is not None else None
    )
    if mpc_profile_payload is not None and mpc_profile_payload.get("schema_version") != "cohort-mpc-profile/1.0":
        raise ValueError("unsupported cohort MPC profile schema")
    gate_config = (
        PolicyGateConfig(**profile_payload.get("gate", {}))
        if profile_payload is not None else None
    )
    optimization_config = (
        OptimizationConfig(**profile_payload.get("optimization", {}))
        if profile_payload is not None else None
    )
    optimizer_weight = (
        float(profile_payload.get("optimizer_weight", 1.0))
        if profile_payload is not None else 1.0
    )
    forecast_adjustment_config = (
        ForecastAdjustmentConfig(**profile_payload.get("forecast_adjustment", {}))
        if profile_payload is not None else None
    )
    mpc_config = (
        CohortMPCConfig(**mpc_profile_payload.get("mpc", {}))
        if mpc_profile_payload is not None else None
    )
    simulator = Simulator(
        config,
        controller_by_name(
            controller,
            forecaster=trained_forecaster,
            gate_config=gate_config,
            optimization_config=optimization_config,
            optimizer_weight=optimizer_weight,
            forecast_adjustment_config=forecast_adjustment_config,
            mpc_config=mpc_config,
        ),
    )
    destination = shard_directory(output_root, campaign_id, config.scenario_id, simulator.controller.name, seed)
    run_path = destination / "run.jsonl"
    metadata_path = destination / "metadata.json"
    parquet_path = destination / "run.parquet"
    audits_path = destination / "selection-audits.parquet"
    manifest_digest = file_sha256(manifest)
    forecast_identity = (
        {
            "path": str(forecast_bundle.resolve()),
            "file_sha256": file_sha256(forecast_bundle),
            "bundle_sha256": trained_forecaster.metadata["bundle_sha256"],
            "model_version": trained_forecaster.model_version,
        }
        if forecast_bundle is not None and trained_forecaster is not None else None
    )
    profile_identity = (
        {
            "path": str(predictive_profile.resolve()),
            "file_sha256": file_sha256(predictive_profile),
            "profile_id": profile_payload.get("profile_id"),
            "gate": profile_payload.get("gate", {}),
            "optimization": profile_payload.get("optimization", {}),
            "optimizer_weight": optimizer_weight,
            "forecast_adjustment": profile_payload.get("forecast_adjustment", {}),
        }
        if predictive_profile is not None and profile_payload is not None else None
    )
    mpc_profile_identity = (
        {
            "path": str(mpc_profile.resolve()),
            "file_sha256": file_sha256(mpc_profile),
            "profile_id": mpc_profile_payload.get("profile_id"),
            "mpc": mpc_profile_payload.get("mpc", {}),
        }
        if mpc_profile is not None and mpc_profile_payload is not None else None
    )
    progress_enabled = progress_every_simulated_hours is not None

    def log(message: str) -> None:
        if progress_enabled:
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            print(f"[{timestamp}] {message}", flush=True)

    if run_path.exists() or parquet_path.exists() or audits_path.exists() or metadata_path.exists():
        if not skip_existing or not run_path.is_file() or not parquet_path.is_file() or not audits_path.is_file() or not metadata_path.is_file():
            raise FileExistsError(f"refusing to overwrite shard: {destination}")
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            existing.get("manifest_sha256") != manifest_digest
            or existing.get("run_file_sha256") != file_sha256(run_path)
            or existing.get("parquet_file_sha256") != file_sha256(parquet_path)
            or existing.get("selection_audits_sha256") != file_sha256(audits_path)
            or existing.get("controller") != simulator.controller.name
            or existing.get("forecast_bundle") != forecast_identity
            or existing.get("predictive_profile") != profile_identity
            or existing.get("mpc_profile") != mpc_profile_identity
        ):
            raise FileExistsError(f"existing shard does not match this manifest or is incomplete: {destination}")
        log(f"phase=complete status=already_published destination={destination}")
        return destination

    progress_steps: int | None = None
    if progress_enabled:
        if progress_every_simulated_hours is None or progress_every_simulated_hours <= 0:
            raise ValueError("progress_every_simulated_hours must be positive")
        progress_steps = max(
            1,
            round(progress_every_simulated_hours * 3600 / config.step_seconds),
        )
    started = time.monotonic()
    simulated_days = config.steps * config.step_seconds / 86_400
    log(
        f"phase=simulate status=started scenario={config.scenario_id} controller={simulator.controller.name} "
        f"seed={seed} steps={config.steps} simulated_days={simulated_days:g} destination={destination}"
    )

    def progress(completed: int, total: int) -> None:
        elapsed = time.monotonic() - started
        rate = completed / elapsed if elapsed > 0 else 0.0
        eta = (total - completed) / rate if rate > 0 else None
        sim_day = completed * config.step_seconds / 86_400
        peak_rss_gib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
        log(
            f"phase=simulate status=running progress={completed / total * 100:.2f}% "
            f"step={completed}/{total} simulated_day={sim_day:.2f}/{simulated_days:g} "
            f"elapsed={_duration(elapsed)} eta={_duration(eta)} peak_rss_gib={peak_rss_gib:.2f}"
        )

    try:
        result = simulator.run(
            progress_interval_steps=progress_steps,
            progress_callback=progress if progress_steps is not None else None,
        )
    except BaseException as error:
        log(f"phase=simulate status=failed error={type(error).__name__}:{error}")
        raise
    log(f"phase=simulate status=complete elapsed={_duration(time.monotonic() - started)}")
    destination.mkdir(parents=True, exist_ok=True)
    temporary_run = destination / f".run.jsonl.{os.getpid()}.tmp"
    log("phase=write_jsonl status=started")
    result.write_jsonl(temporary_run)
    os.replace(temporary_run, run_path)
    log(f"phase=write_jsonl status=complete bytes={run_path.stat().st_size}")
    temporary_parquet = destination / f".run.parquet.{os.getpid()}.tmp"
    log("phase=write_parquet status=started")
    result.write_parquet(temporary_parquet)
    os.replace(temporary_parquet, parquet_path)
    log(f"phase=write_parquet status=complete bytes={parquet_path.stat().st_size}")
    temporary_audits = destination / f".selection-audits.parquet.{os.getpid()}.tmp"
    log("phase=write_selection_audits status=started")
    result.write_selection_audits_parquet(temporary_audits)
    os.replace(temporary_audits, audits_path)
    log(f"phase=write_selection_audits status=complete bytes={audits_path.stat().st_size}")

    log("phase=metadata status=started action=hash_artifacts")
    metadata = {
        "schema_version": "experiment-shard/1.0",
        "campaign_id": campaign_id,
        "scenario_id": config.scenario_id,
        "seed": seed,
        "controller": result.controller,
        "forecast_bundle": forecast_identity,
        "predictive_profile": profile_identity,
        "mpc_profile": mpc_profile_identity,
        "manifest": str(manifest.resolve()),
        "manifest_sha256": manifest_digest,
        "git_commit": git_commit(project_root),
        "component_versions": {"cdot_upf_simulation": "0.1.0", "python": platform.python_version()},
        "host": socket.gethostname(),
        "job_id": os.environ.get("PBS_JOBID"),
        "array_index": os.environ.get("PBS_ARRAY_INDEX"),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_file": run_path.name,
        "run_file_sha256": file_sha256(run_path),
        "canonical_file": parquet_path.name,
        "parquet_file_sha256": file_sha256(parquet_path),
        "selection_audits_file": audits_path.name,
        "selection_audits_sha256": file_sha256(audits_path),
        "summary": result.summary,
    }
    atomic_json(metadata_path, metadata)
    log(
        f"phase=complete status=published elapsed={_duration(time.monotonic() - started)} "
        f"metadata={metadata_path}"
    )
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one deterministic macro-campaign shard")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-root", default=Path("output/macro"), type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument(
        "--progress-every-simulated-hours",
        type=float,
        default=12.0,
        help="emit a flushed progress line at this simulated-time interval (default: 12 hours)",
    )
    parser.add_argument(
        "--controller",
        choices=("static", "reactive", "forecast-capacity", "predictive", "mpc", "oracle"),
        default="static",
    )
    parser.add_argument(
        "--forecast-bundle",
        type=Path,
        help="checksum-verified trained bundle for predictive or forecast-capacity controllers",
    )
    parser.add_argument(
        "--mpc-profile",
        type=Path,
        help="versioned horizon, objective, and certificate profile for MPC",
    )
    parser.add_argument(
        "--predictive-profile",
        type=Path,
        help="versioned optimizer and policy-gate profile for a forecast controller",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    destination = run_shard(
        args.manifest, args.output_root, args.campaign_id, args.seed,
        skip_existing=args.skip_existing, controller=args.controller,
        progress_every_simulated_hours=args.progress_every_simulated_hours,
        forecast_bundle=args.forecast_bundle,
        predictive_profile=args.predictive_profile,
        mpc_profile=args.mpc_profile,
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
