# C-DOT PBS Pro scripts

These jobs follow the working conventions in `../5g-allocation/pbs` and the
local HPE PBS/PALS training material:

- jobs run from `PBS_O_WORKDIR` on queue `workq`;
- the cluster exposes 128 physical CPUs per node, with the neighboring project
  reserving three and using 125 for CPU-heavy work;
- `env.sh` prefers this repository's `env/bin/python`, then falls back to the
  shared `penv` conda environment;
- shared campaign output stays below the repository `output/` tree; node-local
  `/tmp`, `TMPDIR`, or `PBS_JOBFS` is used only for disposable smoke artifacts;
- macro runs are independent PBS array subjobs. They do not use `mpiexec`,
  because the architecture treats cluster nodes as independent scenario
  workers rather than one distributed 5G network.

Recommended bring-up order:

```bash
mkdir -p logs/pbs output/macro
qsub pbs/check_dependencies.pbs
qsub pbs/check_build.pbs
qsub pbs/check_nodes.pbs
qsub pbs/capability_probe_2node.pbs
```

Submit the default 30-seed campaign and its dependent aggregation job:

```bash
bash pbs/submit_campaign.sh
```

Override a campaign at submission time:

```bash
SHARD_COUNT=60 SEED_START=2000 CAMPAIGN_ID=crowd-ul-v1 \
MANIFEST=configs/demo_scenario.json bash pbs/submit_campaign.sh
```

`capability_probe_2node.pbs` is deliberately non-destructive. A result of
`unknown` is not a privileged pass; per the architecture, unknown privilege or
network-namespace support selects the split deployment with high-fidelity
components on a dedicated privileged host.
