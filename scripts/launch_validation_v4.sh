#!/bin/bash
# Wait for discovery analysis, freeze candidates by the pre-registered rule,
# then submit the fresh-seed validation chain. Runs detached on the login node
# so it survives the interactive session that started it.
set -uo pipefail
cd /home/prarabdhas/5g-simulation
source pbs/env.sh

export CAMPAIGN_ROOT="$PWD/output/mixed-stress/guarded-v4"
export BASE_MANIFEST="$PWD/output/manifests/stage1-extreme-packing-1d-s20260817.json"
export FORECAST_BUNDLE="$PWD/output/stage1/models/extreme-forecaster-14d-s20260818.json"
export ANALYSIS="$PWD/output/mixed-stress-v4-analysis.json"
export VALIDATION_OUTPUT="$PWD/output/mixed-stress-v4-validation.json"
export VAL_PER_FAMILY=250
export VAL_CADENCES=10
LOG="$PWD/logs/v4/launch-validation.log"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "waiting for discovery analysis at $ANALYSIS"
for _ in $(seq 1 720); do
  [ -s "$ANALYSIS" ] && break
  qstat -u "$USER" 2>/dev/null | grep -qE '3790|3791|3792' || { sleep 120; break; }
  sleep 60
done
if [ ! -s "$ANALYSIS" ]; then say "FATAL: analysis never appeared; not submitting validation"; exit 1; fi
say "analysis present"

say "freezing candidates (pre-registered rule)"
if ! "$PYTHON_BIN" -m experiments.select_validation_candidates \
      --analysis "$ANALYSIS" --campaign-root "$CAMPAIGN_ROOT" \
      --per-family "$VAL_PER_FAMILY" --max-predrain 2 --max-mpc 2 >>"$LOG" 2>&1; then
  say "FATAL: selection failed or no arm passed every gate; not submitting validation"; exit 2
fi

SHARDS=$("$PYTHON_BIN" -c "import json;print(len(json.load(open('$CAMPAIGN_ROOT/validation/shard-index.json'))['shards']))")
CADENCES=$("$PYTHON_BIN" -c "
import json;m=json.load(open('$CAMPAIGN_ROOT/validation/frozen-candidates.json'))
print(','.join(sorted({str(c['arm']['cadence_minutes']) for c in m['candidates']})))")
export VAL_CADENCES="$CADENCES"
say "shards=$SHARDS cadences=$VAL_CADENCES"

VS=$(qsub -v CAMPAIGN_ROOT,BASE_MANIFEST,VAL_PER_FAMILY,VAL_CADENCES -o "$PWD/logs/v4/" \
     pbs/precompute_static_validation_v4.pbs) || { say "FATAL: static submit failed"; exit 3; }
say "validation statics: $VS"

VR=$(qsub -J "0-$((SHARDS-1))%40" -W "depend=afterany:${VS}" \
     -v CAMPAIGN_ROOT,BASE_MANIFEST,FORECAST_BUNDLE -o "$PWD/logs/v4/" \
     pbs/run_validation_v4.pbs) || { say "FATAL: validation submit failed"; exit 4; }
say "validation array: $VR"

VA=$(qsub -W "depend=afterany:${VR}" -v CAMPAIGN_ROOT,VALIDATION_OUTPUT,VAL_CELLS=1250 \
     -o "$PWD/logs/v4/" pbs/analyze_validation_v4.pbs) || { say "FATAL: analysis submit failed"; exit 5; }
say "validation analysis: $VA -> $VALIDATION_OUTPUT"
printf 'val_static=%s\nval_run=%s\nval_analysis=%s\n' "$VS" "$VR" "$VA" >> logs/v4/jobids.txt
say "validation chain submitted"
