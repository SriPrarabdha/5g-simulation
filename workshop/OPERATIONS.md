# C-DOT 5G Digital Twin Workshop Operations

This is a 90-minute, synthetic-data workshop. It does not connect to live C-DOT traffic, publish policy, actuate an SMF, or migrate established sessions.

## Run of show

| Minute | Presenter | Participant |
|---:|---|---|
| 0–6 | Controller vote and stadium surge | Join JupyterHub |
| 6–14 | Scope and causal loop | Stage 01 preflight |
| 14–22 | 24-UPF/96-group topology and PBS architecture | Inspect manifest |
| 22–32 | Teaching allocation LP | Stage 02 HiGHS/SCIP |
| 32–42 | SCIP vs ParaSCIP | Stage 03 one-node SCIP |
| 42–52 | PBS scenario factory | Stage 04 five-minute shard |
| 52–62 | p50/p90, causality, safety gates | Unsafe-policy drill |
| 62–72 | Parquet and replay contracts | Stage 05 analysis + Stage 06 replay |
| 72–81 | Guided dashboard story | Observe future-session placement |
| 81–87 | +10.52% guided scope vs national non-promotion | Inspect evidence boundary |
| 87–90 | C-DOT advisory-pilot path | Export report and closing sentence |

## Seven-day blocking readiness gate

The live solver track is blocked unless the actual cluster passes all of these checks:

- Open OnDemand/JupyterHub accounts and private writable paths for every participant.
- `qsub`, `qstat`, short-job reservation and site PBS launch syntax.
- SCIP command, matching PySCIPOpt, and license behavior.
- ParaSCIP/UG command, MPI/PALS integration and a reserved two-node test.
- HiGHS and SCIP LP objectives match within the notebook tolerance.
- SCIP and ParaSCIP frozen-MIP incumbent, dual bound and fixed seed reconcile.
- Offline Python wheels, locally bundled Three.js, WebGL2 and iframe content policy.

A failed ParaSCIP check remains a blocking readiness failure. The archived demonstration may be shown only with its fallback label; it is not represented as a live run.

## Capacity and isolation

- Participant solver: one node, one CPU, 4 GB, five minutes.
- Participant simulator: one node, one CPU, 6 GB, ten minutes; five simulated minutes.
- Presenter ParaSCIP: exactly two reserved nodes, one CPU per node, 4 GB per node, ten minutes.
- Every path contains participant identity, PBS job ID and seed. Shared writable campaign paths are prohibited.
- Participants receive neither presenter/dashboard credentials nor policy-publication capability.
- Never distribute one simulator run across 160 nodes. Parallelism comes from independent scenario/controller/seed jobs.

## Fallback order

1. Live personal jobs.
2. Supplied canonical `workshop-run.parquet` and frozen solver result/status.
3. Pre-executed frozen notebook.
4. Standalone `/twin?replay=…` HTML/Three.js replay.
5. Recorded dashboard reveal.

The fallbacks preserve evidence labels. The guided 30-pair +10.52% result is not the national production recommendation; later evidence does not promote MPC, and Static remains production-safe.

## Rehearsal gates

Seven days before delivery, conduct a 35-user rehearsal and record:

- login success, preflight status, scheduler wait p50/p95 and one-node job success;
- simultaneous storage growth, quota headroom, Parquet/replay export and cleanup;
- WebGL2 capability, frame rate, GPU/CPU pressure and iframe policy across venue browsers;
- presenter-only ParaSCIP authorization and participant submission denial;
- fallback transition time at every layer;
- a complete 90-minute rehearsal with section timestamps and at least five minutes of recovery margin.

Stop the live track if any blocking gate fails. The workshop can continue through the labeled fallback chain.
