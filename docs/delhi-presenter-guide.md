# C-DOT Delhi 45-minute presenter guide

## Evidence language

Central claim: **Standards-grounded and statistically verified synthetic modeling at national scale, but not yet calibrated to C-DOT production traffic.**

Use the four labels exactly as shown on slides: `live`, `measured-synthetic`, `modeled-projection`, and `external-pending`.

## Run of show

- **0–3 min, slides 1–2:** define what is live, measured synthetic, projected, and pending.
- **3–11 min, slides 3–10:** establish 16M aggregate UEs, 24 UPFs, 8 zones, 96 groups, distribution checks, accounting, and telemetry quality.
- **11–21 min, slides 11–13:** explain offline provenance, the causal loop, and the same-state MPC certificate.
- **21–33 min, slides 14–20:** show failed one-window designs, forecast horizons, the forecast/control gap, the 30-pair outcome distribution, oracle headroom, and the packing gate.
- **33–40 min, slide 21:** run the live 3-UPF story, then switch to the frozen national evidence view.
- **40–45 min, slide 22:** request counters, topology/eligibility truth, capacity envelopes, and a supported new-session selection hook.

## Numbers that must remain distinct

- Guided campaign: **10.52%** mean-pair improvement.
- National MA6 MPC: **18.76%** mean-pair, **1.15%** severity-weighted, worst pair **-23.29%**.
- Fourteen-day forecaster MPC: **6.72%** mean-pair and **-3.60%** severity-weighted; gate failed.
- Oracle rows are non-deployable modeled projections, not controller results.

## Live demo checkpoints

1. Begin in normal state and say established sessions remain anchored.
2. Reveal pressure; do not imply the route has already changed.
3. Show the causal forecast and same-state certificate.
4. Reveal changed weights for future sessions only.
5. Show realized placement and later overload evidence.
6. Switch to frozen 24-UPF evidence and open the manifest/hash view.

## Failure rehearsal

- Browser reconnect: reload; server-side story state is authoritative.
- Telemetry gap: point to quality flags and the policy-hold behavior.
- Policy fallback: explain that static/last-safe remains active.
- Offline fallback: open `presentation/delhi/index.html` or the PDF; play `demo-reveal.gif` if the live UI is unavailable.
- PBS/internet: never required for the live story.

## Closing language

Do not claim C-DOT calibration, autonomous actuation, established-session migration, production readiness, or completed 2→4→12-node scaling. Propose advisory replay → shadow recommendations → bounded pilot.
