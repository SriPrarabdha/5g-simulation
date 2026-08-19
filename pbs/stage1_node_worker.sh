#!/bin/bash

set -euo pipefail

NODE_INDEX="${1:?node index is required}"
NODE_COUNT="${2:?node count is required}"
NODE_PYTHON="${3:?shared Python path is required}"

cd "${PBS_O_WORKDIR:?PBS_O_WORKDIR is required}"
export PROJECT_PYTHON="$NODE_PYTHON"
source pbs/env.sh

SCRATCH_BASE="${PBS_JOBFS:-${TMPDIR:-/tmp}}"
if [ ! -d "$SCRATCH_BASE" ] || [ ! -w "$SCRATCH_BASE" ]; then
  echo "ERROR: scratch base is not writable on $(hostname): $SCRATCH_BASE" >&2
  exit 1
fi
JOB_TOKEN="${PBS_JOBID//[^[:alnum:]_.-]/_}"
SCRATCH_ROOT="$(mktemp -d "$SCRATCH_BASE/cdot-stage1-${JOB_TOKEN}-node-${NODE_INDEX}.XXXXXX")"
read -r SCRATCH_BLOCKS SCRATCH_BLOCK_SIZE < <(stat -f -c '%a %S' "$SCRATCH_ROOT")
SCRATCH_ALLOCATION_BYTES=$((SCRATCH_BLOCKS * SCRATCH_BLOCK_SIZE))

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export BLIS_NUM_THREADS=1

REPORT_DIR="${CAMPAIGN_ROOT}/multinode/${CAMPAIGN_ID}"
mkdir -p "$REPORT_DIR"
echo "[stage1-node] index=$NODE_INDEX/$NODE_COUNT host=$(hostname) scratch=$SCRATCH_ROOT capacity=$SCRATCH_ALLOCATION_BYTES"
df -h "$SCRATCH_ROOT" "$CAMPAIGN_ROOT"

"$PYTHON_BIN" -m experiments.packed_runner \
  --work-list "$WORK_LIST" \
  --workers "$WORKER_COUNT" \
  --partition-index "$NODE_INDEX" \
  --partition-count "$NODE_COUNT" \
  --repetition "$REPETITION" \
  --campaign-id "$CAMPAIGN_ID" \
  --output-root "$CAMPAIGN_ROOT/shards" \
  --scratch-root "$SCRATCH_ROOT" \
  --allocated-memory-bytes "$ALLOCATED_MEMORY_BYTES" \
  --scratch-allocation-bytes "$SCRATCH_ALLOCATION_BYTES" \
  --report "$REPORT_DIR/node-${NODE_INDEX}.json"

find "$SCRATCH_ROOT" -depth -type d -empty -delete
