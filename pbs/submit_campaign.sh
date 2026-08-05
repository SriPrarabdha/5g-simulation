#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
mkdir -p logs/pbs output/macro

MANIFEST="${MANIFEST:-configs/demo_scenario.json}"
CAMPAIGN_ID="${CAMPAIGN_ID:-demo-v1}"
OUTPUT_ROOT="${OUTPUT_ROOT:-output/macro}"
SHARD_COUNT="${SHARD_COUNT:-30}"
SEED_START="${SEED_START:-1000}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-penv}"
CONTROLLER="${CONTROLLER:-static}"

case "$SHARD_COUNT" in
    ''|*[!0-9]*) echo "SHARD_COUNT must be a positive integer" >&2; exit 2 ;;
esac
if [ "$SHARD_COUNT" -lt 1 ]; then
    echo "SHARD_COUNT must be at least one" >&2
    exit 2
fi
command -v qsub >/dev/null 2>&1 || { echo "qsub is not available" >&2; exit 1; }

ARRAY_LAST=$((SHARD_COUNT - 1))
EXPORTS="MANIFEST=$MANIFEST,CAMPAIGN_ID=$CAMPAIGN_ID,OUTPUT_ROOT=$OUTPUT_ROOT,SEED_START=$SEED_START,CONDA_ENV_NAME=$CONDA_ENV_NAME,CONTROLLER=$CONTROLLER"
ARRAY_JOB="$(qsub -J "0-$ARRAY_LAST" -v "$EXPORTS" pbs/run_macro_array.pbs)"

AGG_EXPORTS="CAMPAIGN_ID=$CAMPAIGN_ID,OUTPUT_ROOT=$OUTPUT_ROOT,EXPECTED_SHARDS=$SHARD_COUNT,CONDA_ENV_NAME=$CONDA_ENV_NAME"
AGGREGATE_JOB="$(qsub -W "depend=afterok:$ARRAY_JOB" -v "$AGG_EXPORTS" pbs/aggregate_campaign.pbs)"

echo "array_job=$ARRAY_JOB shards=$SHARD_COUNT seeds=$SEED_START-$((SEED_START + ARRAY_LAST))"
echo "aggregate_job=$AGGREGATE_JOB dependency=afterok:$ARRAY_JOB"
echo "monitor: qstat -u ${USER:-current-user}"
