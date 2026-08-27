# C-DOT UPF load-balancing demo — first-look FAQ

Nineteen questions people ask the first time they see this demo, with honest
answers. Every number here is measured from C-DOT's own trace, not projected.

Companion documents:
- `docs/cdot-live-demo-worklog.md` — full build log, measurements, and the plan
- `output/cdot-real/2026-08-20-first-drop/REPORT.md` — the first data-drop analysis

---

## The demo in one paragraph

C-DOT's SMF spreads PDU sessions across four UPFs using a fixed weight table.
Under their looped load generator that table leaves **upf-1 carrying about ten
times what upf-3 carries**, and the hottest UPF sits above a 70,000 pps capacity
line for **70% of the run**. We forecast the offered demand ten minutes ahead,
solve one linear program for a better weight table every minute, and push it to
their SMF. The same measured traffic, re-routed, leaves the hottest UPF **32%
lower on average and over the line 0.3% of the time**.

---

## 1. What is the data schema from C-DOT's two APIs?

**Two endpoints, two very different shapes.**

**Prometheus — `http://192.168.218.8:29090`.** Per-session-class packet
counters. The series we care about are the per-class uplink and downlink packet
totals, labelled by UPF, location (TAC), DNN and DSCP:

```
upf_class_ul_packets_total{upf="upf-1", loc="2", dnn="2", dscp="0"}
upf_class_dl_packets_total{upf="upf-1", loc="2", dnn="2", dscp="0"}
```

We query them through `/api/v1/query_range` wrapped server-side as
`rate(<metric>[60s])` at a 30-second step, so Prometheus does the counter
differencing rather than us. Also exported, and used display-only:
`pfcp_sessions_total`, `upf_cpu_usage_cores`, `upf_memory_usage_bytes`,
`upf_tsi`, drop rate and forwarding efficiency.

**These metric names are our guess.** C-DOT sent Grafana CSV exports, not a
metric catalogue. Getting the real names and label sets is blocking question 3
in our mail to Akash. `sources.py` normalises several label spellings
(`upf`/`upf_id`, `loc`/`tac`/`locid`, `dnn`/`dnnid`) so a near-miss still binds.

**SMF advisory REST — `http://192.168.218.8:30956`.** Cleartext HTTP/2 with
prior knowledge (`curl --http2-prior-knowledge`), single resource `/upf-admin`.
`GET` returns, and `POST` accepts, a **JSON array** of tuples keyed on
`(DNN, TAC)`:

```json
[{"dnn": "ims",      "tac": 2, "weights": {"UPF1": 1, "UPF2": 3}},
 {"dnn": "internet", "tac": 2, "weights": {"UPF1": 1, "UPF2": 2}},
 {"dnn": "ims",      "tac": 3, "weights": {"UPF1": 1, "UPF2": 2, "UPF3": 2}},
 {"dnn": "internet", "tac": 3, "weights": {"UPF1": 1, "UPF2": 3, "UPF3": 3}},
 {"dnn": "internet", "tac": 4, "weights": {"UPF1": 1, "UPF4": 4}}]
```

That block is the **actual live state** we captured on 2026-08-24. Note there is
no `ims/tac-1` and no `ims/tac-4` entry. An empty weight set clears an entry.

## 2. When I click "Load last 3 hours", what am I actually loading?

**In replay mode (the default): the CSVs in `cdot-upf-metrics-v02/metrics/`** —
C-DOT's own Grafana exports, unmodified. Specifically two files:

```
UPF wise Uplink — N3 (in PPS)-data-as-joinbyfield-2026-08-20 20_36_34.csv
UPF wise Downlink — N3 (in PPS)-data-as-joinbyfield-2026-08-20 20_36_58.csv
```

Each has a `Time` column plus 32 class columns named
`upf=upf-N:loc=L:dnn=D:dscp0` — 4 UPFs × 4 TACs × 2 DNNs — with values like
`6.55 kp/s`. 721 rows at a 20-second cadence, covering
**2026-08-20 16:36 to 20:36 IST (4.0 hours)**.

Nothing is synthesised. No number in the demo comes from our simulator.

**In live mode (`CDOT_LIVE_SOURCE=prometheus`): the same quantity from their
running Prometheus**, over the same interface. The rest of the pipeline cannot
tell which source it got.

## 3. Is this the same data as the `cdot-upf-metrics/` folder?

Yes — byte for byte. `diff -r cdot-upf-metrics cdot-upf-metrics-v02` returns
nothing. The "looped traffic pattern" second drop that Akash's mail described
either never landed or was copied over itself. **We have exactly one dataset**,
and re-sending the looped-pattern drop is question 8 in our mail.

## 4. Is the demo replaying a recording, or actually computing?

Both, in a specific way that matters.

The **computation is real and causal**. When you press "Load last 3 hours", the
backend walks the window forward and at each of 175 decision points it refits
the forecaster and solves the LP on **only the history available at that moment**
— never on anything after it. The resulting weights take effect from the *next*
sample. That takes about half a minute.

The **playback then reveals that finished result** frame by frame. Compressing
4 hours into 4–15 minutes changes only how fast the audience sees it, never what
the model could see.

We do it this way deliberately: nothing is solving live on stage, so there is no
stall risk, no websocket dependency, and the run is identical every time.
"Evaluate now" does run the pipeline live if you want to show that.

## 5. What are the KPI numbers I should care about?

All measured over the same window with the same demand — the only difference
between the two columns is the weight table. Scored from the moment the advisory
engages (frame 132 of 481), so it is a like-for-like comparison rather than one
arm being credited for a warmup it spent idle.

| KPI | Baseline | With forecaster + optimizer |
|---|---:|---:|
| **Time the hottest UPF is over capacity** | **70.2%** | **0.29%** |
| **UPF-seconds over capacity** | **7,350** | **30** |
| Hottest UPF, mean | 74,080 pps | 50,272 pps (**−32%**) |
| Hottest UPF, peak | 90,463 pps | 71,118 pps (**−21%**) |

The **first row is the headline.**

The counters on screen show something slightly different and more useful on
stage: they run cumulatively from frame 0, so they include the warmup. Baseline
finishes at **8,940 UPF-seconds**, ours at **1,620** — and every one of those
1,620 was accrued *before* the optimizer engaged. The two counters climb together
while the forecaster learns the cycle, then the baseline runs away while ours
**stops dead and never moves again.** That is the moment to point at.

Two supporting numbers if asked:
- **Forecast accuracy**: 0.144 WAPE ten minutes ahead versus **0.335** for
  persistence — a **57% error reduction** — on a proper walk-forward backtest
  with 175 decision points. Conformal p90 coverage 0.94.
- **Solve time**: 2 ms for the joint LP over all six active groups.

## 6. What is the "capacity line", and is it real?

**It is our placeholder: 70,000 pps per UPF, with a safe line at 80% of that.**
It is labelled `(placeholder)` on screen and listed in the unconfirmed-assumptions
ribbon.

We had to invent it because **C-DOT's telemetry never reports overload.** Drop
rate reads 0.000 on three of four pods (0.116% max on the fourth), DL forwarding
efficiency is ~100%, and CPU is pinned at 3.00 cores on every pod for the entire
run because DPDK poll-mode drivers busy-spin. Nothing in what they send us says
"this UPF is in trouble."

70,000 was chosen so that the observed static routing breaches it and a balanced
routing does not. Getting their real per-UPF N3 envelope — in pps, or Mbps plus
mean packet size — is **blocking question 1**. Every gauge, every LP capacity row
and the overload line move when that number arrives.

## 7. Isn't picking a capacity line that makes you look good circular?

It would be, and the previous implementation did exactly that — it set each UPF's
capacity to that UPF's **own observed p99**. That is genuinely circular: it makes
the idle upf-3 look as full as the saturated upf-1 and **inverts the entire
result**.

We use **one uniform ceiling for all four UPFs**, which is the honest choice when
you know the instances are identically provisioned but not what they can take.
And the headline claim does not actually depend on the exact value:

| capacity | baseline over the line | best achievable |
|---:|---:|---:|
| 60,000 | 71.1% | 1.2% |
| 70,000 | 60.1% | 0.8% |
| 80,000 | 45.3% | 0.2% |
| 90,000 | 6.2% | 0.0% |

The imbalance is real across the whole range.

## 8. What changed in the forecaster compared with the original?

**The original was not modified.** `forecasting/bundle.py` is untouched and the
synthetic demo still runs on it. The C-DOT path reimplements the same *algorithm*
against a different feature contract.

**A refit was unavoidable.** The frozen bundle predicts *new-session Mbps and
arrival counts* from **144 ten-minute buckets — exactly 24 hours** — keyed on
time-of-day and day-of-week. C-DOT gives four hours of *carried packet rate*.
Its `required_history_windows == 144` can never be satisfied and the calendar
features are meaningless over a four-hour window.

**Kept:** ridge regression with RMS feature scaling folded back into the
coefficients, `median_bias` recentering, split-conformal residual bands, and
adaptive conformal (ACI).

**Changed:**

| | original | C-DOT |
|---|---|---|
| target | new-session count + UL/DL Mbps | carried demand `D[dnn,tac]` in pps |
| sample step | 10 min | 30 s |
| history needed | 144 windows (24 h) | ~132 samples (66 min) |
| seasonality | fixed daily/weekly calendar | **period discovered by autocorrelation** (31.0 min) |
| horizon | 8 fixed steps | horizon path {1, 5, 10 min}, optimizer plans on the envelope |
| model | ridge only | **4 candidates chosen per series on held-out data** |
| calibration block | middle of history | **most recent block** |
| fitting | offline frozen artifact | refit online every decision |

## 9. And what changed in the optimizer?

**`optimization/highs.py` was reused without a single line changed.** It was
already the joint solve we needed: it accepts an iterable of forecasts and
couples them through per-UPF capacity rows.

Everything changed in the wrapper around it. The one that mattered:

**The previous code called the solver once per `(dnn, tac)` group.** Solving each
group independently means **no UPF ever sees its combined load**, so nothing can
appear overloaded and there is nothing to balance. That is why "Evaluate now"
used to error or produce a proposal that changed nothing useful. We now make one
joint call with all groups together.

Also changed: uniform capacity instead of per-UPF p99 (see Q7); TAC eligibility
moved out of hardcoded constants into config; `Quantiles` constructed by keyword
(the old code passed three positionals against a `(p50, p95, p90=None)` signature
and silently swapped p90 and p95); the ±10 percentage-point step cap relaxed, as
it took four or more decisions to move the load the demo needs to move.

## 10. Why does the forecaster plan on p50 rather than p95?

Counter-intuitive, and measured. Split-conformal inflates each group by **its
own** residual spread, so the volatile `internet` groups get inflated far more
than the near-constant `ims` groups. That distorts the *relative proportions*
that a min-max LP balances. With a 0.144-WAPE forecaster the p50 proportions are
the honest signal.

| planning quantile | advisory time over capacity | overload-seconds |
|---|---:|---:|
| **p50** | **0.0%** | **0** |
| p90 | 8.6% | 750 |
| p95 | 9.3% | 810 |

(Measured during tuning with a longer warmup, so the absolute values differ
slightly from Q5. The ordering is the point, and it is not close.)

The p90/p95 bands are still computed, still shown, and still used for the
robustness envelope — just not as the LP's demand vector.

## 11. What is the forecaster actually doing? Is it a deep model?

**No, and you should not let anyone believe it is.** The forecaster fits four
candidate families per series and picks the winner on a held-out block. On this
trace **a same-phase cycle lag wins every series, decisively** — held-out WAPE
around 0.02 against 0.19–0.60 for the ridge variants.

That is a property of C-DOT's synthetic load generator, not of real traffic. Its
load is a deterministic staircase that repeats every 31 minutes, and an exact
one-cycle-back lag reproduces a step edge perfectly while a linear model on smooth
features cannot.

**The honest framing on stage:** "the forecaster discovered a 31-minute cycle in
your load generator and selected a seasonal model." The candidate selection layer
is the actual product — it re-runs at every refit, so if the cycle changes, the
loop restarts, or this runs on non-looped traffic, it falls back to persistence or
the ridge automatically instead of collapsing.

## 12. You said it's a linear ramp. Is it?

No. The mail describes phases every 10 minutes, but the measured signal is a
**~31-minute repeating staircase** — autocorrelation peaks at 31, 62 and 93
minutes. Within a cycle it is flat plateaus with abrupt step transitions: flat at
182 kpps for seven minutes, hard drop to 92 kpps, flat six minutes, step to
169 kpps, step to 214 kpps. Total network load swings 92 k → 341 k pps.

This is **better** for the demo, not worse. The steps are the hard part of
forecasting, and they are exactly what a cycle-aware model exploits.

## 13. What is the "demand cube", and why does it matter?

It is the one idea the whole pipeline rests on. Rather than forecasting what each
UPF is *carrying* — which already depends on the routing you are trying to
choose, and is therefore circular — we derive the routing-invariant quantity:

```
D[dnn,tac](t) = Σ over UPFs of carried(upf, dnn, tac, t)     offered demand
L[upf](t)     = Σ over (dnn,tac) of w[dnn,tac,upf] · D       load under weights w
```

We forecast `D`, optimise `w`, and project `L`. This is what makes the
baseline-vs-advisory comparison exact: **both curves come from the same measured
`D`, and the only difference between them is the weight table.**

## 14. Why do both charts look identical for the first quarter of the run?

Because they are, and that is correct. The forecaster needs two full 31-minute
cycles behind it before its `lag_2P` feature exists at all — 132 of the 481
frames. Until then the advisory arm has made no decision and is doing exactly
what the baseline does.

The green `advisory ON` marker shows the moment it engages. On a 4-minute
playback that is at 66 seconds.

## 15. Both plots use the same y-axis. Why does that matter?

If each chart autoscaled independently, the advisory chart would be drawn just as
tall as the baseline chart and the comparison would be destroyed. Both are pinned
to the same ceiling (150,000 pps, from the baseline peak), so **the difference is
a difference in height** — readable from the back of a room without reading
either axis.

## 16. Has anything been written to C-DOT's live SMF?

**No.** Only read-only `GET /upf-admin` probes have ever touched their system.
Every apply and rollback has been exercised against an in-memory fake. We will
not POST to their SMF without asking Akash first.

When we do, the write path is: read state → compare hash → **one array POST for
the whole batch** → GET verify every tuple → roll back exactly if anything
mismatches. A joint solve's tuples are only correct together, which is why it is
one batch rather than one POST per tuple.

## 17. What happens if their lab is down on demo day?

Nothing. The demo runs entirely from the CSV replay with no live endpoint at all
— that is the default mode, and there is a test asserting it works with the SMF
refusing connections.

As of now **both their endpoints are refusing connections**: `:29090` and `:30956`
accept TCP (a NodePort listener) then reset on the first byte. They worked on
2026-08-24. This host *is* 192.168.218.8, so no routing or firewall work is needed
once their pods come back.

## 18. What assumptions are you making that C-DOT hasn't confirmed?

Four, all listed on screen in the assumptions ribbon and repeated in the review
drawer before any write:

1. **Per-UPF capacity** — 70,000 pps is ours, not theirs (Q6).
2. **UPF identity** — the Grafana panels `UPF-0…UPF-3` do not line up with labels
   `upf-1…upf-4`. Correlation says only pod `upf-1` matches label `upf-2`. We
   infer the permutation `upf-2→upf-3, upf-3→upf-4, upf-4→upf-2`, which is the
   only assignment consistent with their own TAC-constraint CSV.
3. **Prometheus metric names and labels** — guessed from CSV column headers.
4. **Weight semantics** — are the values relative scores that the SMF renormalises
   within the eligible set, or absolute percentages? We treat them as relative and
   normalise to 100.

Plus: their declared TAC constraints are violated by their own trace under literal
labels (upf-2 carries TAC 4, upf-4 carries TAC 2), TAC 1 carries zero traffic
across the whole run, and their session gauges reset downward repeatedly although
the mail says no subscriber detaches.

## 19. Is there overload your approach cannot remove?

Yes, and we say so rather than hiding it. Total network demand peaks at
**341,504 pps against 4 × 70,000 = 280,000 pps of capacity**. At the very top of
a cycle the network is over-subscribed by 22% and **no routing whatsoever can
avoid overload.**

Against the best possible allocation at every instant, 0.8% of the run is over
the line at a 70 k ceiling. Our advisory achieves 0.3% over the scored window.
**We remove all the avoidable overload** — we do not claim to remove the
unavoidable kind.

An earlier estimate of "51.3% → 0.0%" circulated internally; it came from a script
sampling 49 of 721 points and understated both sides. The full-resolution numbers
in Q5 are the correct ones.
