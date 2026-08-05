# Synthetic mobile-traffic model specification

Status: frozen for `traffic-model/1.0`  
Purpose: training, evaluation, and demonstration only  
Measurement claim: **none**. Every generated record is labelled synthetic.

## Evidence boundary

The generator uses published work to constrain plausible shapes, service mixes,
and event lifecycles. It does not convert activity indices, application examples,
or requirements into asserted C-DOT throughput. Absolute traffic and UPF capacity
remain unsupported scenario assumptions until calibrated with operator data.

Primary references:

- 3GPP [TR 38.913](https://www.etsi.org/deliver/etsi_tr/138900_138999/138913/18.00.00_60/tr_138913v180000p.pdf) for eMBB, URLLC, and mMTC scenario families.
- 3GPP [TS 23.501](https://www.etsi.org/deliver/etsi_TS/123500_123599/123501/18.11.00_60/ts_123501v181100p.pdf) for standardized 5QI characteristics and example services.
- 3GPP [TS 22.261](https://www.etsi.org/deliver/etsi_ts/122200_122299/122261/17.14.00_60/ts_122261v171400p.pdf) for industrial communication requirements.
- The [Telecom Italia Milan dataset paper](https://www.nature.com/articles/sdata201555) for spatial and diurnal activity envelopes only; its activity index is never interpreted as throughput.
- [Mobile communication measurements](https://arxiv.org/abs/1101.0377) for heavy-tailed human inter-event behaviour after removing daily and weekly seasonality.
- 3GPP [TR 25.933](https://www.etsi.org/deliver/etsi_tr/125900_125999/125933/05.03.00_60/tr_125933v050300p.pdf) for web/file object-size and reading-time shapes.
- 3GPP [TR 26.925](https://www.etsi.org/deliver/etsi_tr/126900_126999/126925/18.01.00_60/tr_126925v180100p.pdf) and [TR 26.926](https://www.etsi.org/deliver/etsi_tr/126900_126999/126926/18.02.00_60/tr_126926v180200p.pdf) for gaming and video traffic structure.
- [Large-event cellular measurements](https://pmc.ncbi.nlm.nih.gov/articles/PMC8153328/) for the qualitative ingress/match/halftime/final-whistle/egress lifecycle and uplink pressure.

The exact executable values, units, ranges, correlations, and evidence labels are
frozen in [`configs/traffic_model_registry.json`](../configs/traffic_model_registry.json).

## Population and controllable groups

Approximately 30,000 UEs are represented as cohorts. Session state is retained
inside a shard while only 30-second telemetry and canonical 10-minute buckets are
persisted. A forecast key is `(zone, DNN, S-NSSAI, 5QI)`; the steering selection
key intentionally omits 5QI because it is not assumed to be an SMF UPF-selection
key.

The primary topology has residential, business, metro, and stadium zones and six
service families: streaming/video, social/live upload, gaming/voice,
enterprise/web/file, industrial URLLC, and mMTC sensors. Each family declares its
5QI, directionality, arrival process, holding-time distribution, rate distribution,
and event response in the registry.

## Generation order

For every 30-second tick, a shard performs the following causal sequence:

1. Evaluate calendar-only Fourier features and the current mobility state.
2. Apply event lifecycle inputs that were scheduled before the prediction issue time.
3. Advance an AR(1) latent load term and a class-specific Markov burst state.
4. Draw non-homogeneous Poisson arrivals and class-specific holding times.
5. Draw session demand, preserving UL/DL correlation and heavy tails.
6. Place new sessions using the policy active at that instant.
7. Apply faults and capacity envelopes, then account independently for offered,
   admitted, carried, queued, dropped, and rejected traffic.
8. Emit counters and gauges, including explicit reset, restart, missing, and stale flags.

Mobility uses a time-varying origin/destination matrix. Departures from one zone and
arrivals at the next are generated from the same transition draw, so population is
conserved except for declared ingress/egress boundaries.

## Stadium event template

An event template is sampled once per seed, before any forecast is issued. Attendance,
start time, magnitude, match duration, weather modifier, and class mix vary by seed.
The lifecycle comprises ingress ramp, kickoff spike, match activity, halftime upload
spike, final-whistle spike, and a longer egress tail. Social/live upload receives the
largest correlated UL response; enterprise load falls during the match and recovers
afterward; industrial traffic stays independent except for separately sampled alarms.

## Operational disturbances and telemetry quality

Fault regimes are sampled independently from demand and include UPF degradation,
unavailability, capacity loss, path-latency increase, restart, counter reset, and
telemetry gaps. Counters never bridge a reset or restart. Buckets are half-open,
event-time based, and carry completeness plus quality flags; invalid counter deltas
are excluded rather than clipped into plausible traffic.

## Leakage controls and splits

Feature records carry `available_at`; training rejects any record whose availability
is later than its forecast issue time. Train, validation, and test are chronological.
Event templates, random seeds, and fault regimes are assigned as indivisible units,
so variants of the same event cannot cross a split. Validation selects ensemble
weights and conformal parameters. Test data is used once for release reporting.

## Artifact identity

Every shard manifest records generator and schema versions, scenario family, seed,
Git commit, start/end timestamps, split, source citations, parameters, row counts,
and SHA-256 checksums. A shard writes to a temporary directory, validates schemas and
statistics, then atomically publishes its manifest. A published manifest is immutable;
retrying the same shard either verifies the identical artifact or fails loudly.

## Known unsupported assumptions

- Absolute offered Mbps, session counts, UPF capacity, and queue size are illustrative.
- Cohort aggregation does not model packets or radio scheduling.
- Bounded migration and replica scale-out are simulation-only actions.
- Oracle policies are evaluator upper bounds and are never deployable.
- No autonomous C-DOT SMF/EMS claim is made without a supported operator interface.

