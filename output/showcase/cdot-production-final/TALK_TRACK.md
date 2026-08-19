# C-DOT final production evidence — talk track

1. **Streaming memory** — seven times the measured duration increased peak RSS by only 4.1%, below the 20% gate.
2. **Controller cost** — the MPC adds compute cost, which is why worker packing and held-out promotion are explicit gates.
3. **Final controller outcomes** — across 128 paired production seeds, Reactive is 198–221% of Static on the plotted loss measures; promoted MPC improves this to 105–117%. Lower is better, and Static remains the baseline winner.
4. **Artifact economics** — tiered Bronze/Silver/Gold retention avoids making full audit evidence the default campaign cost.
5. **Packing ladder** — 8 workers/node is the only complete passing formal rung; incomplete or CPU-gate-failing higher rungs are shown, not hidden.
6. **Gated scale-out** — 2 → 4 → 12 nodes completed 33, 66, and 384 shards at 42.8, 84.5, and 269.9 shards/hour. The 12-node phase uses four waves/node versus two for the pilots.
7. **Resource headroom** — every node passed; minimum observed CPU efficiency was 89.1%, maximum RSS was 5.4 GiB of 120 GiB, with zero swap and zero failures.

Closing statement: All production gates PASSED: 2 → 4 → 12 nodes, frozen inputs, zero worker/establishment failures, and zero swap.
