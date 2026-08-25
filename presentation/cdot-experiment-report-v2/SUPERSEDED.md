# SUPERSEDED — do not present these conclusions

**Date: 2026-08-25.** The evidence in this directory comes from the v3
mixed-stress discovery campaign. Its measurements are real and its raw pairs
are untouched, but its **headline conclusion is superseded** by the v4
campaign. Do not present "0/160 passed" or "Static remains the production
recommendation" from here.

| | v3 (this report) | v4 |
|---|---|---|
| arms passing every gate | 0 / 160 | **36 / 160** |
| validity failures | 160 / 160 | **0 / 160** |
| `maintenance_then_outage/brownout` | −1.7% (blocked 88 arms) | **+24.2%** |
| MPC solver timeouts | 40% at 10 min, 100% at 2 min | **0** |
| informative maintenance seeds | 44% | 77–93% |
| best macro gain | n/a (no arm promotable) | **+27.7%** (arm 60) |

Current results:

- `output/mixed-stress-v4-analysis.json` — discovery, 160 arms, 20,000 pairs
- `output/mixed-stress-v4-validation.json` — fresh-seed validation of the
  frozen candidates (this is the one to quote)
- `logs/v4/RUNBOOK.md` — how the campaign was run and how to read the numbers

## Why v3 reached the wrong conclusion

Three defects, all in the evaluator and scenario generator rather than in the
controllers:

1. **`surprise_outage` used `health="unavailable"`**, driving safe capacity to
   zero. The engine scores overload as `load / capacity − 1`, so those pairs
   returned `inf` for *both* controllers — an exact tie that nonetheless failed
   the finite-metric gate on all 160 arms.

   This report is careful on one point and deserves credit for it: it states
   that removing the finite gate alone still promotes no arm. That is correct.
   The problem was that the *second* blocking gate was also mis-specified.

2. **Tail risk was gated on the worst single pair-ratio out of 125.** Gains cap
   at +1.0 while losses are unbounded below, and ratios on near-zero baselines
   explode, so one immaterial pair could cancel one and a half perfect ones.
   v4 bounds the tail in the scored unit instead: overload-seconds added versus
   removed.

3. **Cross-family severity weighting summed raw relative overload areas.**
   Relative overload scales with the reciprocal of remaining capacity, so the
   family containing a fixed 1% brownout carried roughly 275× the weight of
   every other family and decided the aggregate alone.

Separately, the controllers themselves were handicapped: the exposure guard and
the pre-drain LP both worked in absolute Mbps while the campaign scored
*relative* overload, and the MPC horizon was sized in windows as
`hours × 60 / cadence`, so halving the cadence quintupled the LP and every
2-minute arm hit a 2-second solver budget that the contract allowed to be 120
seconds.

## What did NOT change

The v3 raw pairs, analysis JSON, and figures are unmodified. The v4 campaign
uses new seed pools (80000+) disjoint from every v3 pool (47000–70999) and from
the protected seeds, so nothing here was overwritten or re-scored.
