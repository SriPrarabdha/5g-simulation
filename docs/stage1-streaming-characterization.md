# Stage 1 streaming and one-node characterization

Stage 1 executes each shard as a stream. `Simulator.run(...)` requires explicit
sinks and returns a `RunOutcome` containing only incremental summary values,
counts, artifact descriptors, timings, and completion state. The causal demo is
the sole in-memory exception: it attaches an explicit 100-step bounded sink so
its rewind story remains deterministic.

Campaign workers write bounded, immutable Parquet segments below job-local
scratch (`PBS_JOBFS`, then `TMPDIR`, then compute-node `/tmp`). A checkpoint seals those segments before
atomically writing gzip JSON simulator, RNG, controller, and sink state. Resume
requires exact manifest, artifact-policy, model/profile, topology, controller,
and source fingerprints. The latest two checkpoints remain in scratch until the
final shard is committed.

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

Do not request `-l jobfs=...` on this cluster. The script prints `df` for both
the selected compute-node scratch and shared Lustre campaign storage before it
starts workers.

Sequential controller and artifact profiling are provided by
`experiments.profile_stage1` and `experiments.characterize_stage1_artifacts`.
The report builder rejects a rung unless all three repetitions satisfy the
memory, CPU-efficiency, failure/swap/hash, scratch, and stage-out gates.

Stage 1 has no SDFlex dependency. PBS workers commit verified shards directly
to shared campaign storage. SDFlex may consume those immutable artifacts later
as a separate downstream analysis system; it is not an executor, stage-out
target, commit coordinator, or availability dependency here.
