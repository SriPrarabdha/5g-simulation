# Extreme optimizer one-day pilot results

Status: **completed decision pilot; full campaign not recommended yet**

Run date: 2026-08-06

This pilot tests whether the frozen forecaster improves closed-loop UPF
steering. It is a fresh-seed engineering decision test, not statistically
accepted campaign evidence.

## Experiment contract

| Item | Value |
|---|---|
| Manifest | `output/manifests/extreme-optimizer-pilot-1d-s20260806.json` |
| Scenario | `extreme-optimizer-pilot-1d-s20260806` |
| Seed | 20260806, different from training seed 20260805 |
| Simulated period | 2026-05-04, one day after the training period |
| Resolution | 2,880 ticks at 30 seconds; 144 decision epochs |
| Topology | 24 UPFs, 8 zones, 96 traffic groups |
| Controllers | static capacity, reactive threshold, trained predictive HiGHS |
| Forecast bundle | `extreme-calendar-ridge-conformal/1.0` |
| Bundle SHA-256 | `8579c425e4db3a2476c3bf89c0ef2e0b0a297519cffa26d647d7bb4dc1156574` |

All controllers used the same seed, manifest, demand factors, and fault times.
They ran concurrently and completed in 5:47–6:02 wall time each. The pilot
contains three four-hour localized surges: stadium crowd with a two-UPF
brownout, airport crowd with a near-total edge-UPF outage, and industrial
uplink demand with a two-UPF brownout. It also contains a regional latency
incident and explicit recovery periods.

The trained-model identity is recorded in the predictive shard metadata. The
predictive run used the safe startup fallback for the first epoch, then issued
96 model forecasts per epoch. HiGHS candidates were evaluated thereafter; the
policy stability gate retained the prior safe policy in 109 epochs and applied
new predictive policies in 34 epochs.

## Results

| Metric | Static | Reactive | Predictive | Predictive vs static |
|---|---:|---:|---:|---:|
| UL overload area (primary) | 107,767.25 s | 211,891.46 s | 105,180.65 s | **2.40% lower** |
| DL overload area | 59,906.17 s | 118,166.83 s | 56,376.04 s | **5.89% lower** |
| UL overload duration | 16,440 s | 19,650 s | 13,230 s | **19.53% lower** |
| DL overload duration | 30,480 s | 7,200 s | 7,200 s | **76.38% lower** |
| UL dropped bytes | 49.274 TB | 119.438 TB | 49.163 TB | **0.23% lower** |
| DL dropped bytes | 79.192 TB | 163.051 TB | 76.297 TB | **3.66% lower** |
| Establishment failures | 0 | 0 | 0 | no regression |
| Rejected bytes | 0 | 0 | 0 | no regression |

Against reactive steering, predictive reduced UL/DL overload area by
50.36%/52.29% and UL/DL dropped bytes by 58.84%/53.21%. Reactive is therefore
not competitive in this scenario. Static capacity weighting is the meaningful
baseline.

The paired evaluator correctly marks the pilot unaccepted: it has only one
seed, and the primary UL overload-area reduction is below the required 20%.
Its result is in
`output/macro/schema_major=1/campaign=extreme-optimizer-pilot-1d-s20260806/paired-evaluation.json`.

## Event interpretation

The airport outage accounts for 103,965 of predictive's 105,181 UL
overload-area seconds and 106,727 of static's 107,767. Predictive improves this
dominant interval only slightly. The load already attached to the failed UPF
cannot be moved by the implemented new-session steering interface.

Only 3.36% of UL and 2.71% of DL offered traffic in the pilot is new-session
traffic controllable at the current decision. This limits how much immediate
total overload-area reduction routing alone can produce during an abrupt
near-total outage. Static capacity weighting is also already a strong policy
for a symmetric six-eligible-UPF topology.

Predictive does show useful behavior that total overload area hides: it reduces
UL overload duration by 19.53% and DL duration by 76.38%. In the industrial
window it shortens UL overload from 9,240 to 6,030 UPF-seconds, although its UL
overload area and drops are slightly worse than static in that interval. That
trade-off needs correction before a long run.

## Decision

**Do not spend workstation nights on the full campaign yet.** The pilot does
not support the hypothesis required by the current primary gate. A longer run
would produce a more precise estimate of the present behavior, but there is no
evidence yet that it would turn a 2.40% reduction into the required 20%.

Before a full campaign:

1. Report forecast error separately inside surge, outage, brownout, and normal
   windows to distinguish a forecast limitation from an optimizer limitation.
2. Add an avoidable/controllable overload measure alongside total overload;
   keep total UL overload area visible so the new measure cannot hide failures.
3. Tune policy-gate hold/hysteresis and optimizer objectives on separate
   validation seeds, especially the industrial interval where predictive
   shortened duration but increased severity.
4. Decide with C-DOT whether only new-session placement is controllable. If
   session migration is unavailable, state the resulting impact ceiling; if it
   is available, model its cost and constraints before claiming the benefit.
5. Run three to five additional one-day fresh-seed pilots only after a profile
   passes validation. The subsequent
   [optimizer tuning matrix](extreme-optimizer-tuning-results.md) found no such
   profile, so those test seeds remain unconsumed.

This decision does not invalidate the forecaster. It says the present
forecaster-plus-new-session-steering system has not yet demonstrated enough
incremental benefit over a strong static policy to justify a long campaign.
