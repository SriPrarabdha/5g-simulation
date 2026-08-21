Subject: Provisional 10-minute UPF weight advisory for the next controlled SMF run

Dear Akash and Team,

Thank you for sharing the updated four-UPF metrics and the details of the
three-phase traffic scenario. We reviewed the complete CSV package and replayed
the available four-hour trace through our forecasting and optimization
pipeline.

Please find attached `smf-advisory-10min-v0.1.csv`. It contains one 30-minute
advisory cycle at ten-minute resolution. The offsets are relative to the start
of each traffic-generator pass:

| Interval | upf-1 | upf-2 | upf-3 | upf-4 | Action |
|---|---:|---:|---:|---:|---|
| T+00 to T+10 | 25.0% | 25.0% | 25.0% | 25.0% | Keep the baseline while the first window closes |
| T+10 to T+20 | 12.5% | 25.0% | 37.5% | 25.0% | Apply the forecast advisory |
| T+20 to T+30 | 12.5% | 25.0% | 37.5% | 25.0% | Maintain the advisory through Phase 3 |

The advisory ratio after T+10 is therefore:

`upf-1 : upf-2 : upf-3 : upf-4 = 1 : 2 : 3 : 2`

We have treated these as **global UPF selection scores**. Our assumption is
that SMF renormalizes the scores over the UPFs eligible for each TAC. With the
TAC constraints in the supplied CSV, the resulting effective distribution is:

| TAC | Effective eligible-UPF distribution |
|---|---|
| TAC 1 | upf-1 33.3%, upf-4 66.7% |
| TAC 2 | upf-1 33.3%, upf-2 66.7% |
| TAC 3 | upf-1 16.7%, upf-2 33.3%, upf-3 50.0% |
| TAC 4 | upf-1 16.7%, upf-3 50.0%, upf-4 33.3% |

This removes the structural bias of equal global scores: upf-1 is eligible for
all four TACs and therefore receives a disproportionately large fraction of
sessions under equal weights. Under the stated equal offered load from TACs
1–4, the 1:2:3:2 score makes the projected load contribution equal across the
four UPFs.

Forecast and optimizer basis
----------------------------

The supplied trace covers four hours and provides 23 complete ten-minute
windows. It shows a strong 30-minute repeating pattern. On rolling replay, the
existing three-window seasonal forecaster produced 13.30% carried-PPS WAPE,
compared with 18.97% for MA3, 19.78% for MA6, and 23.09% for last-value
persistence. The trace-conditioned diagnostic uses p95 planning demand and the
existing HiGHS allocation optimizer with the supplied TAC eligibility
constraints. Because the requested deliverable is one global score per UPF,
we then projected that result onto the global-score form under the stated equal
TAC demand. This produces the 1:2:3:2 topology-balanced score.

For the representative forecast window 20:40–20:50 IST, classified traffic was
forecast at approximately:

- UL: 87.7 kpps p50 and 141.8 kpps p95;
- DL: 117.1 kpps p50 and 216.3 kpps p95.

An important qualification is that our frozen 7-day and 14-day synthetic
calendar-ridge bundles cannot be transferred directly to this trace. They were
trained for 96 different `(zone, DNN, S-NSSAI)` group identities and require
144 completed history windows. The C-DOT drop contains approximately 24
`(TAC, DNN, DSCP)` windows. The production loading path correctly rejects this
group mismatch. We therefore used the repository's causal short-history
seasonal fallback with the same optimizer interface. The CSV must be described
as a provisional test advisory, not as a production-validated trained-model
policy.

Why cohort MPC is not included yet
----------------------------------

The cohort MPC is intentionally fail-closed for this data drop. It requires
per-class session arrivals, active cohort age or remaining lifetime, calibrated
directional and session capacities, and an unambiguous UPF identity. These
inputs cannot be safely reconstructed from aggregate active-session gauges and
packet-rate measurements. Running MPC by inventing them would produce a
numerical result without a defensible operational meaning.

Issues found in the supplied package
------------------------------------

Before applying the attached CSV, please confirm the following points:

1. **UPF identity mismatch.** Under the literal metric labels, 42.8% of
   per-class PPS appears on UPF/TAC pairs that are forbidden by the supplied
   constraint CSV. The permutation `metric upf-1→constraint upf-1`,
   `metric upf-2→constraint upf-3`, `metric upf-3→constraint upf-4`, and
   `metric upf-4→constraint upf-2` removes all such violations. This strongly
   suggests a dashboard/export label-order mismatch, but we will not assume
   that permutation without your confirmation.
2. **TAC 1 traffic is absent.** Both UL and DL per-session-class series for
   `loc=1` are zero throughout the drop, although the scenario states that TACs
   1–4 each generate equal subscriber load.
3. **Sessions are being removed between passes.** The active-session gauges
   contain repeated large downward changes. This is consistent with a loop
   teardown/restart, but it conflicts with the statement that sessions are
   never detached. Please distinguish "no detach within one 30-minute pass"
   from cleanup between passes and provide an explicit loop/pass identifier.
4. **`smf.yaml` is missing.** It was mentioned in the earlier mail but is not
   present in this directory. We need it to confirm the actual eligibility,
   score range, normalization, and update mechanism.
5. **Capacity is not calibrated.** PPS alone cannot be converted into Mbps
   without packet or byte counters. The drop also does not identify safe UL,
   DL, or active-session capacity for each UPF. CPU is nearly constant around
   three cores and therefore does not currently provide a useful saturation
   envelope.
6. **Session-class schema differs from the description.** The files contain
   DSCP 0 but no 5QI field. Please confirm whether DSCP is the intended class
   key or provide the 5QI mapping.
7. **Several health CSV headers appear copied from another instance.** For
   example, multiple CPU headers reference the same pod/namespace, and the
   UPF3 memory file references the UPF0 pod. Please provide a canonical mapping
   from every exported series to `upf-1` through `upf-4`.

Confirmation required before application
----------------------------------------

Please confirm all of the following before loading the CSV into SMF:

- the canonical metric-label to UPF-ID mapping;
- whether SMF weights are global scores or independently scoped by TAC/DNN;
- whether SMF renormalizes the scores over the eligible UPFs;
- the accepted weight range and precision (for example 0–1, 0–100, or integer
  scores);
- activation timing, atomic update behavior, and the rollback procedure.

If SMF expects integer scores rather than percentages, the two CSV policies are
equivalent to `1:1:1:1` during T+00–T+10 and `1:2:3:2` from T+10 onward.

Requested measurements for the next paired test
------------------------------------------------

For both the baseline run and advisory run, please provide:

- an explicit run ID, pass ID, and absolute timestamp for T, T+10, T+20, and
  every policy activation;
- per `(TAC, DNN, 5QI/DSCP, UPF)` session-create and session-delete counters;
- per-class active sessions and, if possible, session-age/lifetime buckets;
- UL/DL packet and byte counters, not only PPS;
- per-UPF calibrated safe and physical UL, DL, PPS, and session capacities;
- session-establishment attempts, successes, rejections, and failures;
- forwarding efficiency, drops, CPU, memory, TSI, and health/fault state;
- the exact SMF configuration before and after each weight update.

Suggested test procedure
------------------------

1. Run the original scenario with equal weights and no advisory changes.
2. Reset to the same initial state and traffic-generator parameters.
3. Run the scenario again, holding equal weights for T+00–T+10, atomically
   applying 1:2:3:2 at T+10, and retaining it through T+30.
4. If the traffic loop restarts, return to the T+00 row only after confirming
   teardown has completed; repeat the three-row schedule for each pass.
5. Keep an immediate rollback to equal scores available. Roll back on any
   increase in session-establishment failure, unexpected ineligibility, UPF
   health degradation, or missing/stale telemetry.

We propose comparing maximum and variance of per-UPF TSI/utilization, UL and DL
drop rate, forwarding efficiency, session-establishment failures, and the
distribution of newly created sessions. Established sessions are not migrated
by this advisory, so improvement should be evaluated on sessions admitted after
the T+10 activation timestamp.

Once the identity mapping, SMF semantics, and missing counters are confirmed,
we can regenerate the CSV against the exact interface and then enable the
cohort MPC path for a later controlled run.

Regards,

Prarabhda
