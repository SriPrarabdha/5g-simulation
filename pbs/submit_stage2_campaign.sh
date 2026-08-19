#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

: "${NODE_COUNT:?set desired concurrent node count}"
: "${WORKER_COUNT:?set the Stage 1 selected workers per node}"
: "${WORK_LIST:?set the frozen multi-node work list}"
: "${CAMPAIGN_ROOT:?set shared campaign storage}"
: "${CAMPAIGN_ID:?set a unique campaign ID}"
: "${STAGE1_REPORT:?set the passing Stage 1 characterization report}"
CHECKPOINT_ROOT="${CHECKPOINT_ROOT:-$CAMPAIGN_ROOT/checkpoints}"
ALLOCATED_MEMORY_BYTES="${ALLOCATED_MEMORY_BYTES:-128849018880}"
PRIOR_PILOT_REPORT="${PRIOR_PILOT_REPORT:-}"

case "$NODE_COUNT:$WORKER_COUNT" in
  *[!0-9:]*|:*|*:) echo "NODE_COUNT and WORKER_COUNT must be positive integers" >&2; exit 2 ;;
esac
if [ "$NODE_COUNT" -lt 1 ] || [ "$WORKER_COUNT" -lt 1 ]; then
  echo "NODE_COUNT and WORKER_COUNT must be positive" >&2
  exit 2
fi
case "$CAMPAIGN_ID" in
  ''|*[!A-Za-z0-9_.-]*) echo "CAMPAIGN_ID contains unsafe characters" >&2; exit 2 ;;
esac
REPORT_DIR="$CAMPAIGN_ROOT/multinode/$CAMPAIGN_ID"
SHARD_DIR="$CAMPAIGN_ROOT/shards/schema_major=2/campaign=$CAMPAIGN_ID"
if [ -e "$REPORT_DIR" ] || [ -e "$SHARD_DIR" ]; then
  echo "new Stage 2 campaign already has reports or shards; use the retry script" >&2
  exit 2
fi
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
if not p.get("campaign_input_sha256"): raise SystemExit("Stage 2 campaign input identity is missing")
if int(sys.argv[2]) > 4 and p.get("input_freeze_status") != "frozen-production": raise SystemExit("campaigns above four nodes require frozen-production inputs")
if int(p["node_count"]) != int(sys.argv[2]): raise SystemExit("work-list node_count mismatch")
if int(p["worker_count"]) != int(sys.argv[3]): raise SystemExit("work-list worker_count mismatch")
if len(p["work_items"]) < int(sys.argv[2]) * int(sys.argv[3]): raise SystemExit("Stage 2 work list does not fill one worker wave")
stage1 = json.load(open(sys.argv[4]))
if stage1.get("schema_version") != "stage1-characterization-report/1.0" or stage1.get("status") != "passed": raise SystemExit("Stage 1 characterization has not passed")
if int(stage1["selected_worker_count"]) != int(sys.argv[3]): raise SystemExit("WORKER_COUNT is not the selected Stage 1 rung")
nodes = int(sys.argv[2])
if nodes > 2:
    required_nodes = 2 if nodes <= 4 else 4
    if not sys.argv[5]: raise SystemExit(f"a passing {required_nodes}-node PRIOR_PILOT_REPORT is required")
    pilot = json.load(open(sys.argv[5]))
    if pilot.get("schema_version") != "stage2-pilot-report/1.0" or pilot.get("status") != "passed": raise SystemExit("prior Stage 2 pilot did not pass")
    if int(pilot.get("node_count", 0)) != required_nodes: raise SystemExit(f"prior pilot must use {required_nodes} nodes")
    if int(pilot.get("worker_count", 0)) != int(sys.argv[3]): raise SystemExit("prior pilot worker rung mismatch")
    if pilot.get("campaign_input_sha256") != p["campaign_input_sha256"]: raise SystemExit("prior pilot campaign inputs mismatch")
print(len(p["work_items"]))
' "$WORK_LIST" "$NODE_COUNT" "$WORKER_COUNT" "$STAGE1_REPORT" "$PRIOR_PILOT_REPORT")"
ARRAY_LAST=$((NODE_COUNT - 1))
EXPORTS="NODE_COUNT=$NODE_COUNT,WORKER_COUNT=$WORKER_COUNT,WORK_LIST=$WORK_LIST,CAMPAIGN_ROOT=$CAMPAIGN_ROOT,CAMPAIGN_ID=$CAMPAIGN_ID,CHECKPOINT_ROOT=$CHECKPOINT_ROOT,ALLOCATED_MEMORY_BYTES=$ALLOCATED_MEMORY_BYTES"
ARRAY_JOB="$(qsub -J "0-${ARRAY_LAST}%${NODE_COUNT}" -l "select=1:ncpus=${WORKER_COUNT}:mem=120gb" -v "$EXPORTS" pbs/run_stage2_node.pbs)"
AGG_EXPORTS="NODE_COUNT=$NODE_COUNT,CAMPAIGN_ROOT=$CAMPAIGN_ROOT,CAMPAIGN_ID=$CAMPAIGN_ID,EXPECTED_SHARDS=$EXPECTED_SHARDS"
AGGREGATE_JOB="$(qsub -W "depend=afterok:$ARRAY_JOB" -v "$AGG_EXPORTS" pbs/stage2_aggregate.pbs)"

echo "array_job=$ARRAY_JOB nodes=$NODE_COUNT workers_per_node=$WORKER_COUNT expected_shards=$EXPECTED_SHARDS"
echo "aggregate_job=$AGGREGATE_JOB dependency=afterok:$ARRAY_JOB"
