# C-DOT control-science showcase v6

This immutable post-audit package preserves the v5 evidence and corrects its
control-safety and presentation labels. Static remains the deployed controller;
MPC and pre-drain remain guarded shadow/replay experiments only.

## Corrections closed

- Pre-drain now rejects to Static whenever predicted overflow exceeds `1e-7`.
- UL, DL and session overflow is published in `ConstraintSlack`; an
  optimal-with-overflow result is proposed but never certified, accepted or executed.
- `zero_predicted_overflow` is a conjunctive promotion gate.
- The authoritative seed-36001/36002 oracle evaluation was restored from the
  completed PBS job; the full repository suite passes 174/174 tests.
- 516 paired runs across 28 declared candidate configurations, plus 72 survival-sensitivity controller comparisons: 588 controller pairs total.
- The combined gate is labelled “combined severity-weighted unknown + mixed.”
- The 225–970 ms pre-drain maxima are labelled campaign-saturation latency from
  120 concurrent simulations on a saturated 125-CPU node—not an isolated
  production control-plane benchmark.

Historical evaluations are not rescored and their negative decisions do not
change. Their recorded overflow means the affected pre-drain actions should not
be described as flow-feasible or certified. Validation seeds 46201–46216,
release seeds 46301–46330 and sealed forecast seed 46003 remain unconsumed.
