# C-DOT showcase talk track

## Opening message

This work turns the macro 5G simulation into a bounded-memory, resumable HPC
campaign system. PBS workers execute independently, write immutable artifacts
to shared campaign storage, verify every hash, and publish `metadata.json` last
as the commit marker. SDFlex remains an optional downstream consumer, not a
runtime dependency.

## Evidence sequence

1. **Streaming memory scaling** — after a two-day cohort warm-up, increasing
   the measured horizon from one to seven days increased peak RSS from 457 MiB
   to 476 MiB: 4.1%, against a 20% limit. This is the central evidence that
   result retention no longer grows linearly with simulated duration.

2. **Controller profile** — one extreme day took 10.8 minutes for Static, 14.7
   minutes for Reactive, and 30.5 minutes for the development MPC. Peak RSS was
   444, 457, and 682 MiB respectively. Rendezvous selection dominates Static;
   controller work dominates MPC.

3. **Paired controller outcomes** — on the identical extreme seed, Reactive is
   roughly 2× Static across overload/drop measures. MPC substantially improves
   on Reactive, but remains 7–20% above Static. Do not hide this result: it is
   why campaigns above four nodes require held-out MPC promotion and a
   `frozen-production` input record.

4. **Artifact economics** — Bronze is about 3–4 KiB, Silver about 5.6 MiB, and
   Gold about 2.94 GiB per controller. Gold records approximately 43 million
   audits in 10,501 row groups and takes about 20–23 minutes just for verified
   stage-out. This motivates deterministic 1% Silver retention and an explicit
   Gold allowlist.

5. **Multinode proof** — two independent implementations committed and
   hash-validated 16 and 32 shards across two nodes with zero failures. This
   covers node-local scratch, deterministic partitioning, atomic publication,
   aggregation, and PBS-array execution.

6. **Packing ladder** — once `stage1-report.json` is published, show all three
   repetitions at 8, 16, 32, and 64 workers against the CPU, memory, scratch,
   swap, failure, and stage-out gates. Select only the highest rung for which
   all three repetitions pass.

## Scaling story

The next steps are deliberately gated:

1. Select worker density from the one-node ladder.
2. Promote the MPC profile only after the complete 30-pair held-out evaluation.
3. Freeze production manifest, model, profile, policy, and source hashes.
4. Run a matching two-node extreme pilot.
5. Require that pilot to authorize the four-node run.
6. Require the matching four-node pilot to authorize the approximately
   twelve-node campaign.

Retries restore versioned, hash-inventoried checkpoints and independently
validate already committed shards before skipping them.

## Presentation assets

Open [`output/showcase/cdot-stage1/index.html`](../output/showcase/cdot-stage1/index.html)
for the self-contained report. Each figure is also provided as a slide-ready
PNG and editable SVG in `output/showcase/cdot-stage1/figures/`. `metrics.json`
records the SHA-256 identity of every source report.

