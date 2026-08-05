#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
source pbs/env.sh

SMOKE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/upf-local-smoke.XXXXXX")"
trap 'rm -rf "$SMOKE_ROOT"' EXIT

"$PYTHON_BIN" -m experiments.run_campaign_shard \
    --manifest configs/demo_scenario.json --campaign-id local-smoke \
    --seed 1 --output-root "$SMOKE_ROOT"
"$PYTHON_BIN" -m experiments.aggregate_campaign \
    --root "$SMOKE_ROOT/schema_major=1/campaign=local-smoke" \
    --expected-shards 1 --output "$SMOKE_ROOT/summary.json"
test -s "$SMOKE_ROOT/summary.json"
echo "LOCAL CAMPAIGN SMOKE TEST PASSED"
