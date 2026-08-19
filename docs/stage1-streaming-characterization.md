# Stage 1 streaming and one-node characterization

Stage 1 executes each shard as a stream. `Simulator.run(...)` requires explicit
sinks and returns a `RunOutcome` containing only incremental summary values,
counts, artifact descriptors, timings, and completion state. The causal demo is
the sole in-memory exception: it attaches an explicit 100-step bounded sink so
its rewind story remains deterministic.

Campaign workers write bounded, immutable Parquet segments below job-local
scratch (`PBS_JOBFS`, then `TMPDIR`, then compute-node `/tmp`). A checkpoint
seals those segments before atomically writing gzip JSON simulator, RNG,
controller, and sink state. Resume requires exact manifest, artifact-policy,
model/profile, topology, controller, and source fingerprints. The latest two
checkpoints remain in scratch until the final shard is committed.

Retention is selected before simulation from the topology/scenario/seed pair.
Bronze contains summaries, provenance, performance, and audit counts. Silver
adds canonical `run.parquet`. Gold adds every selection audit and every complete
policy decision. JSONL is disabled unless an artifact policy explicitly enables
it for smoke/debug work.

Publication writes and hashes artifacts in a hidden directory on the same
shared filesystem as the final destination, atomically renames that directory,
and writes `metadata.json` last. Readers accept `metadata.json` as the commit
marker and revalidate every declared artifact.

## Characterization commands

The memory gate uses two days of cohort warm-up because the extreme profile's
maximum session lifetime is 5,760 30-second steps. Its inputs therefore contain
three and nine simulated days: the same two-day warm-up followed by one-day and
seven-day measurement horizons. `experiments.memory_regression` rejects shorter
or mislabeled windows before starting simulation.

Run `pbs/build_stage1_preflight.pbs` only after the steady-state memory report,
the three-controller sequential profile, and the complete 3-tier × 3-controller
artifact matrix exist. The packing ladder remains blocked unless that gate
writes a passing `stage1-precharacterization-gate/1.0` report.

Freeze one interleaved, two-wave work list per rung:

```bash
env/bin/python -m experiments.build_stage1_worklist \
  --manifest /path/to/one-day-extreme.json --workers 8 --seed-base 94000 \
  --forecast-bundle /path/to/frozen-bundle.json \
  --mpc-profile /path/to/frozen-mpc-profile.json \
  --output /shared/stage1/work-list-8.json
```

Submit `pbs/characterize_stage1.pbs` three times for each of 8, 16, 32, and 64
workers, setting `WORKER_COUNT`, `REPETITION`, `WORK_LIST`, `CAMPAIGN_ROOT`, and
`ALLOCATED_MEMORY_BYTES`. Each job owns one full node and starts no PBS array
children. `SCRATCH_ALLOCATION_BYTES` is optional: use it for a conservative
operator-defined local-scratch budget; otherwise the script records the free
bytes visible at job start. This fallback is required on C-DOT, whose PBS
server does not define a `jobfs` resource or export `PBS_JOBFS`. Build the final
report from all twelve packed-run reports with `experiments.build_stage1_report`.

For the 125 GiB C-DOT nodes, the first 8-worker demo submission is:

```bash
qsub \
  -v WORKER_COUNT=8,REPETITION=1,WORK_LIST="$HOME/5g-stage1/worklists/work-list-8.json",CAMPAIGN_ROOT="$HOME/5g-stage1/campaign",ALLOCATED_MEMORY_BYTES=128849018880 \
  pbs/characterize_stage1.pbs
```

`configs/demo_scenario.json` has only 60 steps (30 simulated minutes), so this
submission validates scheduling, multiprocessing, scratch, publication, and
hashes but is too short to pass the 70% CPU-efficiency characterization gate.
Build the production work lists from the one-day extreme manifest before
selecting a packing rung.

Do not request `-l jobfs=...` on this cluster. The script prints `df` for both
the selected compute-node scratch and shared Lustre campaign storage before it
starts workers. C-DOT PBS also requires memory within the select chunk
(`select=1:ncpus=128:mem=120gb`); a separate `-l mem=120gb` is rejected when
`select` or `place` is present.

## Two-node demo scaling

The two-node launcher uses PBS Task Manager (`pbsdsh`), not MPI. It requests two
exclusive chunks, launches one packed process pool per node, and partitions the
frozen work list by position. For a 16-item, 8-worker demo list, node 0 receives
the eight even positions and node 1 receives the eight odd positions. Thus all
16 items run once with 16 workers active across the allocation.

```bash
qsub \
  -v WORKER_COUNT=8,REPETITION=1,WORK_LIST="$HOME/5g-stage1/worklists/work-list-8.json",CAMPAIGN_ROOT="$HOME/5g-stage1/campaign",ALLOCATED_MEMORY_BYTES=128849018880,CAMPAIGN_ID=stage1-demo-2node-w8-r1 \
  pbs/characterize_stage1_2node.pbs
```

Each node writes `node-<index>.json`; after both reports exist, the allocation's
launch node writes `combined.json`. Per-node scratch remains local while unique
immutable shards are committed directly to the common Lustre campaign tree.
The 60-step demo is useful for validating distribution and publication but is
too short for a meaningful speedup or CPU-efficiency measurement.

## Stage 2 campaign handoff

Do not choose multi-node worker density from the demo. Run
`pbs/submit_stage1_ladder.sh` against the frozen one-day extreme manifest,
setting `PREFLIGHT_REPORT` to the passing pre-characterization gate. The
submitter rejects mismatched input hashes, changed source code, or an existing
campaign root. Use only the selected worker count from its passing
`stage1-report.json`.

Stage 2 uses a PBS array capped at the requested number of concurrently active
nodes. Each array subjob owns one exclusive node and one deterministic work-list
partition. This provides better scheduling and failure isolation than one
monolithic `select=12` allocation:

Build the list with `experiments.build_stage2_worklist`. It pairs every seed
across static, reactive, and MPC controllers, pins every manifest/model/profile
file by SHA-256, records a canonical hash for the whole list, and refuses to
overwrite an existing frozen list. Both initial submission and retry reject a
changed list or missing input hashes.

```bash
NODE_COUNT=12 WORKER_COUNT=<selected-rung> \
WORK_LIST=/shared/stage2/work-list.json \
STAGE1_REPORT=/shared/stage1/stage1-report.json \
CAMPAIGN_ROOT=/shared/stage2 CAMPAIGN_ID=extreme-v1 \
bash pbs/submit_stage2_campaign.sh
```

The initial Stage 2 submitter requires at least one full worker wave and refuses
an existing campaign ID. It also verifies that `WORKER_COUNT` is exactly the
selected rung in the passing Stage 1 report. Existing campaign state is handled
only through `pbs/retry_stage2_partition.sh`.

Every successful aggregate writes `pilot-report.json`, applying the Stage 1
memory, CPU, swap, scratch, failure, and stage-out gates to every participating
node. A 4-node launch requires `PRIOR_PILOT_REPORT` from a passing 2-node run;
any launch above 4 nodes requires it from a passing 4-node run. The prior report
must use the same worker rung and `campaign_input_sha256` (manifest, forecast
bundle, MPC profile, artifact policy, controller matrix, and source code), so an
unrelated smoke test cannot authorize the extreme campaign.

The checked-in MPC profiles are `development_only`. They are sufficient for
resource characterization but not for a campaign above four nodes.
`experiments.promote_mpc_profile` creates a production profile only from a
fresh held-out `cohort-mpc-10pct-candidate-evaluation/1.0` report that passes
the 10% gate and every aggregate guardrail without consuming reserved seeds.
Re-run `experiments.freeze_stage1_inputs` with that promoted profile; it emits
`frozen-production`. `experiments.build_stage2_worklist --frozen-inputs ...`
requires that record for campaigns above four nodes and verifies every hash,
the artifact policy, and the source fingerprint.

Failed partitions are resubmitted with `pbs/retry_stage2_partition.sh`.
Already committed shards are hash-validated and skipped. Incomplete local
scratch is copied to a versioned directory below `CHECKPOINT_ROOT` on failure
and restored before retry. Each durable copy has an atomic completion marker
and per-file SHA-256 inventory; corruption is rejected before restore.
`metadata.json` remains the only final shard commit marker; durable checkpoint
copies are recovery inputs, never published shards.

Sequential controller and artifact profiling are provided by
`experiments.profile_stage1` and `experiments.characterize_stage1_artifacts`.
The report builder rejects a rung unless all three repetitions satisfy the
memory, CPU-efficiency, failure/swap/hash, scratch, and stage-out gates.

Stage 1 has no SDFlex dependency. PBS workers commit verified shards directly
to shared campaign storage. SDFlex may consume those immutable artifacts later
as a separate downstream analysis system; it is not an executor, stage-out
target, commit coordinator, or availability dependency here.

## C-DOT presentation package

Build slide-ready PNG/SVG figures and a self-contained HTML report with:

```bash
python scripts/build_cdot_showcase.py \
  --memory /shared/stage1/reports/memory-regression-steady-state.json \
  --profile /shared/stage1/reports/sequential-profile.json \
  --artifacts /shared/stage1/reports/artifact-characterization.json \
  --multinode /shared/stage1/demo-2node/combined.json \
  --multinode /shared/stage2/demo-2node/combined.json \
  --ladder /shared/stage1/packing/stage1-report.json \
  --output output/showcase/cdot-stage1
```

`pbs/build_cdot_showcase.pbs` can run after all packing jobs using an
`afterany` dependency. It rebuilds the ladder report when all twelve repetition
reports exist, then refreshes the plots even when a rung is rejected. The
generator records every input hash in `metrics.json` and automatically includes
the paired Bronze controller outcomes when the artifact shard tree is adjacent
to the prerequisite report directory.
