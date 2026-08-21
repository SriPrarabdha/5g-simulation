# C-DOT Forecast Phase 1/2 Talk Track

## Opening

Phase 1 closed the experimental plumbing gaps before any model comparison: causal event availability, telemetry-quality replay, checksum model packaging, empirical-survival provenance, fail-closed release evidence, and protected seed partitions. Phase 2 then ran five frozen model families across all 96 groups.

## Figure 1 — WAPE

The moving-average reference is 14.16% WAPE. LightGBM and histogram-gradient reach about 12.51%, an 11.6% relative improvement. That is meaningful, but below the precommitted 15% gate. We do not lower the gate after seeing the answer.

## Figure 2 — Peak underprediction

The causal challengers do better around scheduled and detected events. LightGBM and histogram-gradient reduce peak misses by about 27.5%; Ridge-v2 reduces them by 25.8%. This gate passes for four causal challengers except calendar ridge.

## Figure 3 — Coverage

Every model lands inside the accepted 88–95% p90 coverage band. Calibration worked, but calibration alone is not promotion.

## Figure 4 — Tail guardrail

Every candidate has at least one aggregate regime/horizon slice more than 5% worse than the moving average. The strongest nonlinear models regress about 14–15% in their worst slice. The regime ensemble is an extreme failure at more than 600%.

## Figure 5 — Gate scorecard

Promotion is conjunctive: every column must pass. There is no eligible row, which is why protected test seed 46003 remains untouched.

## Figures 6 and 7 — Regime and horizon detail

These heatmaps explain why one global WAPE is insufficient. Gains are not uniform across operating regimes or planning horizons. Quote a cell only with its regime/horizon context.

## Figure 8 — Causal observability

Unknown events are not labelled as knowable before evidence exists. Pre-signal target rows are excluded; detected-surge performance is measured only after the first observable signal. This prevents look-ahead leakage.

## Figure 9 — Phase 1 plumbing

The key contribution is not just another model. Offline and live observations now share one causal metadata contract; bundles are serialized, hashed and production-loaded; controller campaigns emit churn, solver and survival provenance; missing release fields fail closed.

## Figure 10 — Cluster evidence

The work was divided into hundreds of independent PBS shards: 384 trained bundles, 384 calibrated bundles, 480 metric shards and 96 independent verification shards. Cluster capacity is shown as available infrastructure, not claimed utilization.

## Figure 11 — Architecture

The honest deployment direction is Static-first. A guarded MPC branch is considered only for a causally known scheduled capacity event. Unknown or observed unplanned faults fall back immediately to Static.

## Close

The result is a disciplined negative promotion decision with positive scientific learning: causal event features help peak forecasting, uncertainty calibration is sound, and the release process correctly refuses a model with insufficient average gain and unsafe slices.
