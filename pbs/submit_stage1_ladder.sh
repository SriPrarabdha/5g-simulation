#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

: "${MANIFEST:?set frozen one-day extreme packing manifest}"
: "${CAMPAIGN_ROOT:?set a new shared characterization root}"
: "${FORECAST_BUNDLE:?set frozen extreme forecast bundle}"
: "${MPC_PROFILE:?set frozen MPC profile}"
: "${PREFLIGHT_REPORT:?set the passing Stage 1 pre-characterization report}"
SEED_BASE="${SEED_BASE:-94000}"
ALLOCATED_MEMORY_BYTES="${ALLOCATED_MEMORY_BYTES:-128849018880}"
PYTHON_BIN="${PYTHON_BIN:-/home/abharadwaj/.conda/envs/penv/bin/python}"

if [ -e "$CAMPAIGN_ROOT" ]; then
  echo "CAMPAIGN_ROOT must not already exist: $CAMPAIGN_ROOT" >&2
  exit 2
fi

"$PYTHON_BIN" - "$PREFLIGHT_REPORT" "$MANIFEST" "$FORECAST_BUNDLE" "$MPC_PROFILE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

from experiments.run_campaign_shard import source_fingerprint

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if report.get("schema_version") != "stage1-precharacterization-gate/1.0":
    raise SystemExit("unsupported Stage 1 pre-characterization report")
if report.get("status") != "passed":
    raise SystemExit(f"Stage 1 pre-characterization gate did not pass: {report.get('reasons')}")
profile = report["sequential_profile"]
inputs = profile["inputs"]
actual = {
    "manifest": Path(sys.argv[2]),
    "forecast_bundle": Path(sys.argv[3]),
    "mpc_profile": Path(sys.argv[4]),
}
for name, path in actual.items():
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if inputs.get(name, {}).get("sha256") != observed:
        raise SystemExit(f"{name} does not match the passing pre-characterization report")
if profile.get("source_fingerprint") != source_fingerprint(Path.cwd()):
    raise SystemExit("source code changed after pre-characterization")
PY

mkdir -p "$CAMPAIGN_ROOT/worklists"
JOBS=()
for workers in 8 16 32 64; do
  work_list="$CAMPAIGN_ROOT/worklists/work-list-${workers}.json"
  "$PYTHON_BIN" -m experiments.build_stage1_worklist \
    --manifest "$MANIFEST" --workers "$workers" --seed-base "$SEED_BASE" \
    --forecast-bundle "$FORECAST_BUNDLE" --mpc-profile "$MPC_PROFILE" \
    --output "$work_list"
  for repetition in 1 2 3; do
    exports="WORKER_COUNT=$workers,REPETITION=$repetition,WORK_LIST=$work_list,CAMPAIGN_ROOT=$CAMPAIGN_ROOT,ALLOCATED_MEMORY_BYTES=$ALLOCATED_MEMORY_BYTES"
    JOBS+=("$(qsub -v "$exports" pbs/characterize_stage1.pbs)")
  done
done

dependency="$(IFS=:; echo "${JOBS[*]}")"
REPORT_JOB="$(qsub -W "depend=afterok:$dependency" -v "CAMPAIGN_ROOT=$CAMPAIGN_ROOT" pbs/build_stage1_report.pbs)"
printf 'characterization_job=%s\n' "${JOBS[@]}"
echo "report_job=$REPORT_JOB dependency=afterok-all-characterization"
