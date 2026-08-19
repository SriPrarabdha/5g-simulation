#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

: "${PARTITION_INDEX:?set failed partition index}"
: "${NODE_COUNT:?set total campaign node count}"
: "${WORKER_COUNT:?set selected workers per node}"
: "${WORK_LIST:?set frozen multi-node work list}"
: "${CAMPAIGN_ROOT:?set shared campaign storage}"
: "${CAMPAIGN_ID:?set existing campaign ID}"
: "${STAGE1_REPORT:?set the passing Stage 1 characterization report}"
CHECKPOINT_ROOT="${CHECKPOINT_ROOT:-$CAMPAIGN_ROOT/checkpoints}"
ALLOCATED_MEMORY_BYTES="${ALLOCATED_MEMORY_BYTES:-128849018880}"

if [ "$PARTITION_INDEX" -lt 0 ] || [ "$PARTITION_INDEX" -ge "$NODE_COUNT" ]; then
  echo "PARTITION_INDEX must be in [0, NODE_COUNT)" >&2
  exit 2
fi
case "$CAMPAIGN_ID" in
  ''|*[!A-Za-z0-9_.-]*) echo "CAMPAIGN_ID contains unsafe characters" >&2; exit 2 ;;
esac
EXPECTED_SHARDS="$(/home/abharadwaj/.conda/envs/penv/bin/python -c '
import hashlib, json, sys
from pathlib import Path
from experiments.run_campaign_shard import source_fingerprint
p = json.load(open(sys.argv[1]))
if p.get("schema_version") != "stage2-work-list/1.0": raise SystemExit("not a frozen Stage 2 work list")
expected = p.get("work_list_sha256")
canonical = dict(p); canonical.pop("work_list_sha256", None)
observed = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
if not expected or expected != observed: raise SystemExit("frozen Stage 2 work-list hash mismatch")
if any(not item.get("input_sha256") for item in p["work_items"]): raise SystemExit("Stage 2 work item lacks frozen input hashes")
if p.get("source_fingerprint") != source_fingerprint(Path.cwd()): raise SystemExit("source code does not match frozen Stage 2 work list")
if int(p["node_count"]) != int(sys.argv[2]): raise SystemExit("work-list node_count mismatch")
if int(p["worker_count"]) != int(sys.argv[3]): raise SystemExit("work-list worker_count mismatch")
if len(p["work_items"]) < int(sys.argv[2]) * int(sys.argv[3]): raise SystemExit("Stage 2 work list does not fill one worker wave")
stage1 = json.load(open(sys.argv[4]))
if stage1.get("schema_version") != "stage1-characterization-report/1.0" or stage1.get("status") != "passed": raise SystemExit("Stage 1 characterization has not passed")
if int(stage1["selected_worker_count"]) != int(sys.argv[3]): raise SystemExit("WORKER_COUNT is not the selected Stage 1 rung")
print(len(p["work_items"]))
' "$WORK_LIST" "$NODE_COUNT" "$WORKER_COUNT" "$STAGE1_REPORT")"
EXPORTS="PARTITION_INDEX=$PARTITION_INDEX,NODE_COUNT=$NODE_COUNT,WORKER_COUNT=$WORKER_COUNT,WORK_LIST=$WORK_LIST,CAMPAIGN_ROOT=$CAMPAIGN_ROOT,CAMPAIGN_ID=$CAMPAIGN_ID,CHECKPOINT_ROOT=$CHECKPOINT_ROOT,ALLOCATED_MEMORY_BYTES=$ALLOCATED_MEMORY_BYTES"
RETRY_JOB="$(qsub -l "select=1:ncpus=${WORKER_COUNT}:mem=120gb" -v "$EXPORTS" pbs/run_stage2_node.pbs)"
AGG_EXPORTS="NODE_COUNT=$NODE_COUNT,CAMPAIGN_ROOT=$CAMPAIGN_ROOT,CAMPAIGN_ID=$CAMPAIGN_ID,EXPECTED_SHARDS=$EXPECTED_SHARDS"
AGGREGATE_JOB="$(qsub -W "depend=afterok:$RETRY_JOB" -v "$AGG_EXPORTS" pbs/stage2_aggregate.pbs)"

echo "retry_job=$RETRY_JOB partition=$PARTITION_INDEX"
echo "aggregate_job=$AGGREGATE_JOB dependency=afterok:$RETRY_JOB"
