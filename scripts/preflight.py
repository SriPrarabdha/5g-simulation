#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    registry_path = ROOT / "configs" / "traffic_model_registry.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        checks.append(("traffic registry", registry.get("synthetic") is True, registry.get("schema_version", "missing")))
    except (OSError, json.JSONDecodeError) as error:
        checks.append(("traffic registry", False, str(error)))

    scenario_path = ROOT / "configs" / "demo_scenario.json"
    try:
        from simulator.macro.config import load_scenario
        scenario = load_scenario(scenario_path)
        checks.append(("scenario", len(scenario.upfs) == 3 and len(scenario.groups) == 6,
                       f"{scenario.scenario_id} / {len(scenario.upfs)} UPFs / {len(scenario.groups)} classes"))
    except Exception as error:
        checks.append(("scenario", False, str(error)))

    forecast_path = Path(os.environ.get(
        "CDOT_FORECAST_BUNDLE", ROOT / "configs" / "demo_forecast_bundle.json"
    ))
    try:
        from forecasting import TrainedForecastBundle
        forecast_bundle = TrainedForecastBundle.load(forecast_path)
        checks.append((
            "forecast bundle", True,
            f"{forecast_bundle.model_version} / {forecast_bundle.metadata['bundle_sha256'][:12]}",
        ))
    except Exception as error:
        checks.append(("forecast bundle", False, str(error)))

    try:
        from demo_api.main import app
        checks.append(("FastAPI/OpenAPI", len(app.openapi()["paths"]) >= 18, f"{len(app.openapi()['paths'])} paths"))
    except ModuleNotFoundError:
        try:
            command = [str(ROOT / "env" / "bin" / "python"), "-c",
                       "from demo_api.main import app; print(len(app.openapi()['paths']))"]
            path_count = int(subprocess.check_output(command, cwd=ROOT, text=True).strip())
            checks.append(("FastAPI/OpenAPI", path_count >= 18, f"{path_count} paths"))
        except Exception as error:
            checks.append(("FastAPI/OpenAPI", False, str(error)))
    except Exception as error:
        checks.append(("FastAPI/OpenAPI", False, str(error)))

    bundle = ROOT / "demo_api" / "static" / "index.html"
    checks.append(("operator console", bundle.is_file(), str(bundle.relative_to(ROOT))))
    if bundle.is_file():
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()[:12]
        checks.append(("bundle checksum", True, digest))

    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL':4}  {name:20} {detail}")
    failed = [name for name, passed, _ in checks if not passed]
    if failed:
        print(f"Preflight failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("Preflight passed. All displayed data remains explicitly synthetic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
