# C-DOT predictive UPF steering simulation

This repository implements the architecture in
`cdot_upf_simulation_demo_architecture.md` incrementally. The current vertical
slice provides:

- dependency-free Python representations of the six v1 data contracts;
- a deterministic, 30-second cohort simulator with independent random streams;
- offered versus carried UL/DL traffic, directional queues and drops;
- active-session admission limits and explicit establishment rejection;
- health and directional capacity events; and
- a controller interface with a static capacity-weighted baseline.

Run the example with the project environment:

```bash
env/bin/python -m simulator.macro.cli configs/demo_scenario.json \
  --output output/demo/run.jsonl
```

Run the tests:

```bash
env/bin/python -m unittest discover -s tests -v
```

The JSONL writer is the development/audit adapter. Canonical Parquet output
will be added behind the same result interface when `pyarrow` is introduced.

