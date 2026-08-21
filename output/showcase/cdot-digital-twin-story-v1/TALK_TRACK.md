# Talk track — C-DOT 5G digital-twin evidence story

## 01 — Cover

Frame this as an end-to-end engineering and scientific story, not a controller victory lap.

## 02 — 02_executive_result

Lead with the distinction: the platform passed; no advanced controller passed every gate.

## 03 — 03_scientific_journey

Each stage reduces uncertainty. Early positive results are hypotheses, not release authority.

## 04 — 04_digital_twin_architecture

Explain causality: telemetry closes a bucket, forecasts and optimization use only observable state, and only future sessions can move.

## 05 — 05_traffic_fingerprint

The generator models service-by-hour demand, day types, autocorrelation, class mix and mobility conservation.

## 06 — 06_representative_upf_day

A representative synthetic UPF day combines normal load with bounded fault events and recovery.

## 07 — 07_telemetry_pathology

Raw counter resets and missingness must be reconstructed before either forecasting or control is trustworthy.

## 08 — 08_streaming_memory_scaling

Seven times longer duration grows peak RSS only 4.1%; the simulator is truly streaming.

## 09 — 09_cluster_scale

The final scale ladder completed 384 shards on 12 nodes at 90.9% CPU efficiency, with zero failures and zero swap.

## 10 — 10_forecast_horizon

Forecast horizon and interval coverage are measured separately; point accuracy alone is insufficient.

## 11 — 11_forecast_targets

LightGBM improves all separated targets, but the frozen 15% bar remains unmet.

## 12 — 12_survival_calibration

Kaplan–Meier mechanics converge on censored synthetic lifecycle records; this validates mechanics, not field calibration.

## 13 — 13_forecast_vs_control

The better 14-day forecast still failed the control gate. Prediction accuracy is an input—not the objective.

## 14 — 14_controllability_surface

New-session-only control is fundamentally limited by notice time and session lifetime.

## 15 — 15_oracle_ladder

The oracle ladder shows headroom: faults and migration relaxation dominate what is controllable.

## 16 — 16_guarded_mpc_architecture

Static is the production spine. MPC is a guarded branch that loses authority on uncertainty or failed certification.

## 17 — 17_solver_and_fallback

Removing timeouts proved the optimizer branch ran; it did not solve the performance gap.

## 18 — 18_all_mpc_candidates

Across the broad MPC sweep, intervals cross zero and scenario gains remain concentrated.

## 19 — 19_distribution_blind_survival

Distribution-blind lifecycle fitting works mechanically across five hidden families and fails closed when stale.

## 20 — 20_scenario_transfer

Pre-drain helps known scheduled faults, but that gain does not transfer safely to simultaneous surprise demand.

## 21 — 21_predrain_frontier

Blend interpolation exposes the trade-off: strong action buys mean benefit but unsafe tails; weak action loses the benefit.

## 22 — 22_mpc_action_funnel

MPC v2 fixed the warm-up interface and executed actions. The negative result is no longer an inert-branch artifact.

## 23 — 23_adaptive_causal_lag

Adaptive blending traversed its range, yet surprise demand arrived after persistent-session commitments.

## 24 — 24_overflow_fail_closed

The audit correction is explicit: any overflow above 1e-7 records resource slack and returns to Static without certification.

## 25 — 25_campaign_saturation_latency

These maxima are campaign-saturation measurements from 120 concurrent simulations—not isolated control-plane latency.

## 26 — 26_experiment_inventory

The inventory is exact: 516 candidate pairs plus 72 sensitivity comparisons equals 588, with protected seeds untouched.

## 27 — 27_worked_vs_did_not

Separate what worked in the scientific system from what did not work in candidate control algorithms.

## 28 — 28_final_decision

Close on disciplined non-promotion: the evidence system knew when the algorithm was not ready.
