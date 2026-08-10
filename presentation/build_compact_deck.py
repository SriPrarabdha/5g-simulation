#!/usr/bin/env python3
"""Build the concise 14-slide C-DOT predictive UPF steering review deck."""

from __future__ import annotations

from pathlib import Path

import uno

import build_deck as b


ROOT = Path(__file__).resolve().parents[1]


def build(deck: b.Deck) -> None:
    # 1 — Cover
    page = deck.new_slide("", "", dark=True)
    deck.text(page, b.mm(10), b.mm(9), b.mm(100), b.mm(7),
              "C-DOT · PREDICTIVE USER PLANE", 9, b.CYAN, bold=True,
              font=b.MONO, spacing=1.4)
    deck.text(page, b.mm(10), b.mm(42), b.mm(257), b.mm(28),
              "Predictive UPF Steering\nSimulation to Working Demo",
              31, b.WHITE, bold=True)
    deck.text(page, b.mm(10), b.mm(82), b.mm(235), b.mm(12),
              "Data modelling, forecasting, controller experiments and evidence",
              15, 0xC4D3D9)
    deck.text(page, b.mm(10), b.mm(103), b.mm(180), b.mm(7),
              "Concise technical review · 09 August 2026", 10, 0x9FB4BE)
    labels = [
        ("01", "MODEL", "traffic + events", b.TEAL),
        ("02", "GENERATE", "30 s simulation", b.TEAL_2),
        ("03", "FORECAST", "10–80 min", b.PURPLE),
        ("04", "OPTIMIZE", "2 h cohort MPC", b.GREEN),
        ("05", "STEER", "new sessions", b.AMBER),
        ("06", "DEMO", "evidence + outcome", b.RED),
    ]
    for i, (num, head, detail, color) in enumerate(labels):
        x = 10 + i * 52.3
        deck.card(page, b.mm(x), b.mm(132), b.mm(46), b.mm(25),
                  fill=0x193541, line=0x385662, accent=color)
        deck.text(page, b.mm(x + 5), b.mm(137), b.mm(35), b.mm(4), num,
                  7.2, 0x9CB3BD, font=b.MONO, bold=True)
        deck.text(page, b.mm(x + 5), b.mm(143), b.mm(35), b.mm(5), head,
                  10.3, b.WHITE, bold=True)
        deck.text(page, b.mm(x + 5), b.mm(150), b.mm(35), b.mm(4), detail,
                  7.7, 0xB8CBD2)
        if i < len(labels) - 1:
            deck.arrow(page, b.mm(x + 46), b.mm(145), b.mm(x + 52.3), color=0x4D6A75)
    deck.text(page, b.mm(10), b.mm(170), b.mm(250), b.mm(5),
              "Synthetic, reproducible and explicitly outside a production-release claim.",
              9.5, 0xF0C36D, bold=True)
    deck.footer(page, "Implementation + documentation + frozen experiment review", dark=True)

    # 2 — Executive summary / system story
    page = deck.new_slide("A complete predictive steering loop is implemented and measurable",
                          "Executive summary")
    deck.metric(page, b.mm(9), b.mm(39), b.mm(72), "Training data", "1.55M",
                "group × 10-minute rows", tone=b.TEAL)
    deck.metric(page, b.mm(87), b.mm(39), b.mm(72), "Forecast WAPE", "7.63%",
                "held-out macro mean", tone=b.PURPLE)
    deck.metric(page, b.mm(165), b.mm(39), b.mm(76), "Mean-pair UL gain", "10.52%",
                "30 matched scenarios", tone=b.GREEN)
    deck.metric(page, b.mm(247), b.mm(39), b.mm(79), "Weighted UL gain", "2.84%",
                "tail-sensitive total", tone=b.AMBER)
    stages = [
        ("Traffic model", "zones, services, probability and faults", b.TEAL),
        ("Synthetic history", "30-second causal cohort simulation", b.TEAL_2),
        ("Forecaster", "demand and uncertainty by group", b.PURPLE),
        ("Controller", "cohort MPC certified against static", b.GREEN),
        ("Steering", "weighted placement for future sessions", b.AMBER),
        ("Demo", "actual outcomes and frozen evidence", b.RED),
    ]
    for i, (head, body, color) in enumerate(stages):
        row, col = divmod(i, 3)
        x, y = 9 + col * 106, 82 + row * 38
        deck.card(page, b.mm(x), b.mm(y), b.mm(98), b.mm(30), fill=b.WHITE, accent=color)
        deck.text(page, b.mm(x + 6), b.mm(y + 5), b.mm(84), b.mm(5),
                  f"0{i + 1} · {head}", 10, color, bold=True)
        deck.text(page, b.mm(x + 6), b.mm(y + 14), b.mm(84), b.mm(8), body,
                  8.8, b.INK, bold=True)
    deck.card(page, b.mm(9), b.mm(162), b.mm(317), b.mm(13),
              fill=b.PALE_AMBER, line=None, accent=b.AMBER)
    deck.text(page, b.mm(16), b.mm(165), b.mm(303), b.mm(7),
              "Claim: reduced modeled overload exposure for future sessions—not guaranteed prevention, migration or live C-DOT actuation.",
              9, b.INK, bold=True)
    deck.footer(page, "README.md · docs/extreme-forecaster-v1-results.md · docs/cohort-mpc-full-campaign-results.md")

    # 3 — Data model
    page = deck.new_slide("Stage 1 — model diverse traffic as controllable groups",
                          "Simulation data · modelling")
    deck.card(page, b.mm(9), b.mm(40), b.mm(150), b.mm(122), fill=b.WHITE, accent=b.TEAL)
    deck.text(page, b.mm(16), b.mm(47), b.mm(136), b.mm(6),
              "Dimensions", 15, b.INK, bold=True)
    deck.metric(page, b.mm(16), b.mm(59), b.mm(60), "Zones", "8",
                "urban · airport · stadium · industrial · rural", tone=b.TEAL)
    deck.metric(page, b.mm(84), b.mm(59), b.mm(67), "Groups", "96",
                "zone × DNN × slice × 5QI", tone=b.PURPLE)
    services = [
        "Consumer video", "Social/live", "Gaming", "IMS voice",
        "Enterprise", "Video conference", "Industrial URLLC", "Massive IoT",
        "Connected vehicle", "Edge AI", "Cloud backup", "Public safety",
    ]
    deck.text(page, b.mm(16), b.mm(97), b.mm(136), b.mm(5),
              "12 SERVICE CLASSES", 8, b.TEAL, bold=True, font=b.MONO)
    for i, service in enumerate(services):
        col, row = i % 2, i // 2
        x, y = 16 + col * 66, 108 + row * 8
        deck.circle(page, b.mm(x), b.mm(y + 1), b.mm(2.2), b.TEAL if col == 0 else b.PURPLE)
        deck.text(page, b.mm(x + 5), b.mm(y), b.mm(57), b.mm(4), service,
                  8.1, b.INK, bold=True)
    deck.card(page, b.mm(168), b.mm(40), b.mm(158), b.mm(122), fill=b.NAVY_2,
              line=None, accent=b.PURPLE)
    deck.text(page, b.mm(176), b.mm(48), b.mm(142), b.mm(5),
              "Executable probability model", 14, b.WHITE, bold=True)
    deck.text(page, b.mm(176), b.mm(64), b.mm(142), b.mm(10),
              "N(g,t) ~ Poisson(λg × Fg,t)", 19, b.CYAN, bold=True,
              font=b.MONO, align=b.ParagraphAdjust.CENTER)
    deck.text(page, b.mm(176), b.mm(79), b.mm(142), b.mm(12),
              "Fg,t = daily curve × weekend factor × weekly noise × surge multiplier",
              9.1, 0xD1E0E5, align=b.ParagraphAdjust.CENTER)
    deck.bullet_list(page, b.mm(176), b.mm(99), b.mm(142), [
        "independent SHA-derived arrival and lifetime streams",
        "uniform integer session lifetime by class",
        "fixed class-level UL/DL Mbps per active session",
        "sessions retained as anchored cohorts after placement",
        "5QI is forecast/QoS metadata, not a separate v1 steering key",
    ], font_size=8.8, gap=9.6, color=b.WHITE, bullet_color=b.PURPLE)
    deck.footer(page, "configs/extreme_training_profile.json · simulator/macro/engine.py · docs/extreme-data-spec-and-cdot-gap-analysis.md")

    # 4 — Data generation experiments and scale
    page = deck.new_slide("Stage 1 — generate normal regimes, stochastic surges and network stress",
                          "Simulation data · experiments")
    events = [
        ("Daily / weekly", "258,048 factors", "service curves + weekend + U(0.86,1.16)", b.TEAL),
        ("Flash crowds", "192 episodes", "12/week · random group · 2–10 h · ×2.5–8", b.PURPLE),
        ("Brownouts", "128 episodes", "8/week · UL capacity ×0.18–0.70", b.AMBER),
        ("Near outages", "48 episodes", "3/week · degraded + 1% capacity", b.RED),
        ("Latency", "80 episodes", "5/week · +25–140 ms", b.GREEN),
    ]
    for i, (head, count, model, color) in enumerate(events):
        y = 41 + i * 20.5
        deck.card(page, b.mm(9), b.mm(y), b.mm(204), b.mm(16), fill=b.WHITE, accent=color)
        deck.text(page, b.mm(16), b.mm(y + 4), b.mm(47), b.mm(4), head.upper(),
                  7.5, color, bold=True, font=b.MONO)
        deck.text(page, b.mm(67), b.mm(y + 3), b.mm(41), b.mm(5), count,
                  9.7, b.INK, bold=True)
        deck.text(page, b.mm(111), b.mm(y + 3), b.mm(94), b.mm(6), model,
                  8.5, b.SLATE)
    deck.card(page, b.mm(222), b.mm(41), b.mm(104), b.mm(98), fill=b.WHITE, accent=b.GREEN)
    deck.text(page, b.mm(229), b.mm(48), b.mm(90), b.mm(6),
              "16-week corpus", 14, b.INK, bold=True)
    metrics = [
        ("112 days", "322,560 ticks"),
        ("16,128", "10-minute buckets"),
        ("1,548,288", "group observations"),
        ("24 UPFs", "16 edge · 4 regional · 4 central"),
        ("≈4.39 B", "projected session arrivals"),
        ("≈9.6 GiB", "projected artifact volume"),
    ]
    for i, (value, label) in enumerate(metrics):
        y = 63 + i * 12
        deck.text(page, b.mm(229), b.mm(y), b.mm(34), b.mm(5), value,
                  10.7, b.GREEN, bold=True)
        deck.text(page, b.mm(266), b.mm(y + 0.5), b.mm(52), b.mm(5), label,
                  7.9, b.SLATE)
    deck.card(page, b.mm(222), b.mm(146), b.mm(104), b.mm(20), fill=b.PALE_TEAL,
              line=None, accent=b.TEAL)
    deck.text(page, b.mm(229), b.mm(150), b.mm(90), b.mm(12),
              "Outputs: run.parquet, group/UPF buckets, selection audits and hashed metadata.",
              8.6, b.INK, bold=True)
    deck.card(page, b.mm(9), b.mm(151), b.mm(204), b.mm(15), fill=b.PALE_AMBER,
              line=None, accent=b.AMBER)
    deck.text(page, b.mm(16), b.mm(154), b.mm(190), b.mm(8),
              "Boundary: no mobility, persistent UE identity, heavy-tailed rates, packets or generated telemetry faults in the extreme profile.",
              8.5, b.INK, bold=True)
    deck.footer(page, "experiments/build_extreme_history_manifest.py · docs/extreme-data-spec-and-cdot-gap-analysis.md §§3–8")

    # 5 — Forecast training
    page = deck.new_slide("Stage 2 — transform the history into causal demand forecasts",
                          "Forecaster · training")
    flow = [
        ("30 s data", "20 rows", b.TEAL),
        ("10 min bucket", "arrivals + mean residual", b.TEAL_2),
        ("Observation", "sessions · UL · DL", b.PURPLE),
        ("Direct models", "8 horizons", b.PURPLE),
        ("Calibration", "p50 · p90 · p95", b.GREEN),
        ("Bundle", "checksum + metrics", b.AMBER),
    ]
    for i, (head, body, color) in enumerate(flow):
        x = 9 + i * 52.8
        deck.card(page, b.mm(x), b.mm(46), b.mm(46), b.mm(30), fill=b.WHITE, accent=color)
        deck.text(page, b.mm(x + 5), b.mm(52), b.mm(36), b.mm(5), head,
                  9.3, color, bold=True)
        deck.text(page, b.mm(x + 5), b.mm(62), b.mm(36), b.mm(7), body,
                  8.3, b.INK, bold=True)
        if i < 5:
            deck.arrow(page, b.mm(x + 46), b.mm(61), b.mm(x + 52.8), color=b.SLATE)
    deck.card(page, b.mm(9), b.mm(89), b.mm(155), b.mm(73), fill=b.WHITE, accent=b.PURPLE)
    deck.text(page, b.mm(16), b.mm(96), b.mm(140), b.mm(6),
              "Model", 14, b.INK, bold=True)
    deck.text(page, b.mm(16), b.mm(107), b.mm(140), b.mm(10),
              "Calendar-ridge direct multi-horizon", 14, b.PURPLE, bold=True)
    deck.bullet_list(page, b.mm(16), b.mm(123), b.mm(140), [
        "96 groups × 3 targets × 8 horizons = 2,304 models",
        "last, MA(6), trend, daily lag and calendar Fourier features",
        "70% train · 15% calibration · 15% chronological test",
        "median bias correction + split conformal uncertainty",
    ], font_size=8.8, gap=8.8, bullet_color=b.PURPLE)
    deck.card(page, b.mm(173), b.mm(89), b.mm(153), b.mm(73), fill=b.WHITE, accent=b.GREEN)
    deck.text(page, b.mm(180), b.mm(96), b.mm(139), b.mm(6),
              "Training optimizations", 14, b.INK, bold=True)
    deck.bullet_list(page, b.mm(180), b.mm(110), b.mm(139), [
        "vectorized O(n) feature construction",
        "RMS scaling for national-scale numerical stability",
        "immutable JSON bundle with internal SHA-256",
        "adaptive conformal alpha after realized coverage",
    ], font_size=9, gap=10.2, bullet_color=b.GREEN)
    deck.text(page, b.mm(180), b.mm(150), b.mm(139), b.mm(6),
              "3:03 total · ~13 s fitting · ~1.8 GiB RSS", 9.3, b.GREEN, bold=True)
    deck.footer(page, "experiments/train_forecaster.py · forecasting/bundle.py · docs/extreme-forecaster-v1-results.md")

    # 6 — Forecast evaluation
    page = deck.new_slide("Stage 2 — forecasting beats causal baselines, but surges remain difficult",
                          "Forecaster · evaluation")
    horizons = [10, 20, 30, 40, 50, 60, 70, 80]
    wape = [4.48, 6.09, 7.35, 7.95, 7.42, 7.63, 9.34, 10.73]
    deck.card(page, b.mm(9), b.mm(40), b.mm(190), b.mm(93), fill=b.WHITE, accent=b.PURPLE)
    deck.text(page, b.mm(16), b.mm(47), b.mm(176), b.mm(6),
              "Held-out WAPE by horizon", 13, b.INK, bold=True)
    x0, y0, cw, ch = b.mm(26), b.mm(66), b.mm(156), b.mm(45)
    points = []
    for pct in [0, 4, 8, 12]:
        yy = y0 + ch - ch * pct / 12
        deck.line(page, x0, yy, x0 + cw, yy, b.GRID, 0.25)
        deck.text(page, b.mm(14), yy - b.mm(2), b.mm(10), b.mm(4), f"{pct}%",
                  6.8, b.MUTED, align=b.ParagraphAdjust.RIGHT, font=b.MONO)
    for i, (horizon, value) in enumerate(zip(horizons, wape)):
        x = x0 + cw * i / 7
        y = y0 + ch - ch * value / 12
        if points:
            deck.line(page, points[-1][0], points[-1][1], x, y, b.PURPLE, 1.1)
        points.append((x, y))
        deck.circle(page, x - b.mm(1.6), y - b.mm(1.6), b.mm(3.2), b.PURPLE)
        deck.text(page, x - b.mm(8), y0 + ch + b.mm(4), b.mm(16), b.mm(4),
                  f"{horizon}m", 6.8, b.MUTED, align=b.ParagraphAdjust.CENTER, font=b.MONO)
        deck.text(page, x - b.mm(8), y - b.mm(6), b.mm(16), b.mm(4),
                  f"{value:.1f}", 6.8, b.PURPLE, bold=True,
                  align=b.ParagraphAdjust.CENTER, font=b.MONO)
    deck.text(page, b.mm(16), b.mm(121), b.mm(176), b.mm(5),
              "Overall p90 coverage 94.21% · p95 coverage 96.69%", 8.5,
              b.GREEN, bold=True)
    deck.card(page, b.mm(208), b.mm(40), b.mm(118), b.mm(93), fill=b.WHITE, accent=b.TEAL)
    deck.text(page, b.mm(215), b.mm(47), b.mm(104), b.mm(6),
              "Baseline comparison", 13, b.INK, bold=True)
    methods = [("Calendar ridge", 7.63, b.PURPLE),
               ("Daily seasonal", 13.71, b.TEAL_2),
               ("MA(6)", 14.30, b.AMBER)]
    for i, (label, value, color) in enumerate(methods):
        y = 65 + i * 18
        deck.text(page, b.mm(215), b.mm(y), b.mm(55), b.mm(4), label,
                  8.5, b.INK, bold=True)
        deck.bar(page, b.mm(272), b.mm(y + 1), b.mm(33), b.mm(5), value, 15,
                 color=color, label=f"{value:.2f}%")
    deck.text(page, b.mm(215), b.mm(119), b.mm(103), b.mm(7),
              "44.36% WAPE reduction vs seasonal naive", 8.7, b.GREEN, bold=True)
    regimes = [
        ("Normal", "6.51% WAPE", "94.19% p90", b.GREEN),
        ("Surge", "11.08% WAPE", "30.73% p90", b.RED),
        ("Brownout", "5.09% WAPE", "87.88% p90", b.AMBER),
        ("Outage", "5.29% WAPE", "73.96% p90", b.PURPLE),
    ]
    for i, (name, value, coverage, color) in enumerate(regimes):
        x = 9 + i * 79.3
        deck.card(page, b.mm(x), b.mm(143), b.mm(72), b.mm(22), fill=b.WHITE, accent=color)
        deck.text(page, b.mm(x + 6), b.mm(148), b.mm(27), b.mm(4), name.upper(),
                  7.4, color, bold=True, font=b.MONO)
        deck.text(page, b.mm(x + 35), b.mm(146.5), b.mm(31), b.mm(5), value,
                  8.4, b.INK, bold=True, align=b.ParagraphAdjust.RIGHT)
        deck.text(page, b.mm(x + 35), b.mm(154), b.mm(31), b.mm(4), coverage,
                  7.3, b.SLATE, align=b.ParagraphAdjust.RIGHT)
    deck.footer(page, "docs/extreme-forecaster-v1-results.md · docs/extreme-optimizer-tuning-results.md")

    # 7 — Optimizer experiment journey
    page = deck.new_slide("Stage 3 — experiments showed why a one-window optimizer was not enough",
                          "Optimizer · experiment progression")
    experiment_rows = [
        ["EXPERIMENT", "WHAT CHANGED", "RESULT / DECISION"],
        ["One-day predictive pilot", "trained p95 forecast + one-window HiGHS LP", "+2.40% UL area; insufficient for campaign"],
        ["Eight-profile tuning", "gate, costs, safety factors and static blends", "all validation profiles lost to static"],
        ["Mechanism isolation", "scheduled hints, anomaly, lifetime and concentration cap", "lifetime helped, but no profile passed"],
        ["Oracle action-space bound", "full-day cohort survival + perfect fault knowledge", "100% modeled UL reduction was feasible"],
        ["Cohort MPC development", "two-hour state transition + static certificate", "~25.09% mean on five dev seeds"],
        ["First 12-pair pilot", "broader surge/fault/outage/mixed matrix", "failed: 4.74% mean; profile rejected"],
        ["Frozen MA6 MPC", "50% static blend + unknown-fault fallback", "advanced to fresh 30-pair campaign"],
    ]
    fills = [b.NAVY_2, b.WHITE, b.PALE_RED, b.PALE_RED, b.PALE_TEAL,
             b.PALE_GREEN, b.PALE_RED, b.PALE_GREEN]
    deck.table(page, b.mm(9), b.mm(41), [b.mm(66), b.mm(133), b.mm(118)],
               experiment_rows, row_h=15.2, font_size=8.1, fills=fills)
    deck.card(page, b.mm(9), b.mm(165), b.mm(317), b.mm(10), fill=b.NAVY_2,
              line=None, accent=b.PURPLE)
    deck.text(page, b.mm(16), b.mm(167.5), b.mm(303), b.mm(5),
              "Lesson: placements persist for hours; optimizing only the next bucket can create residual concentrations that later decisions cannot undo.",
              8.4, b.WHITE, bold=True, align=b.ParagraphAdjust.CENTER)
    deck.footer(page, "docs/extreme-optimizer-pilot-results.md · extreme-optimizer-tuning-results.md · extreme-oracle-bound-results.md · cohort-mpc-pilot-results.md")

    # 8 — MPC + steering
    page = deck.new_slide("Stage 3 — the accepted controller plans cohorts, certifies the action and steers new sessions",
                          "Optimizer · current control loop")
    flow = [
        ("CLOSED HISTORY", "causal MA(6) p95", b.PURPLE),
        ("COHORT STATE", "UPF + remaining lifetime", b.TEAL),
        ("2 h MPC", "12 future windows", b.GREEN),
        ("STATIC REPLAY", "same state + events", b.AMBER),
        ("CERTIFICATE", "no worse guardrails", b.RED),
        ("FIRST ACTION", "publish or exact static", b.TEAL_2),
    ]
    for i, (head, body, color) in enumerate(flow):
        x = 9 + i * 52.8
        deck.card(page, b.mm(x), b.mm(43), b.mm(46), b.mm(31), fill=b.WHITE, accent=color)
        deck.text(page, b.mm(x + 5), b.mm(49), b.mm(36), b.mm(5), head,
                  7.4, color, bold=True, font=b.MONO)
        deck.text(page, b.mm(x + 5), b.mm(59), b.mm(36), b.mm(7), body,
                  8.4, b.INK, bold=True)
        if i < 5:
            deck.arrow(page, b.mm(x + 46), b.mm(59), b.mm(x + 52.8), color=b.SLATE)
    deck.card(page, b.mm(9), b.mm(87), b.mm(155), b.mm(75), fill=b.WHITE, accent=b.GREEN)
    deck.text(page, b.mm(16), b.mm(94), b.mm(141), b.mm(6),
              "Frozen MPC profile", 14, b.INK, bold=True)
    deck.bullet_list(page, b.mm(16), b.mm(108), b.mm(141), [
        "12 × 10-minute receding horizon",
        "exact anchored cohort state and expected survival",
        "known future capacity path only after known_at_step",
        "50% optimized action + 50% contemporaneous static",
        "fallback to static during unplanned capacity state",
        "UL, DL, drop and session guardrails",
    ], font_size=8.8, gap=8.0, bullet_color=b.GREEN)
    deck.card(page, b.mm(173), b.mm(87), b.mm(153), b.mm(75), fill=b.PALE_AMBER,
              line=0xE5C78D, accent=b.AMBER)
    deck.text(page, b.mm(180), b.mm(94), b.mm(139), b.mm(6),
              "How steering works", 14, b.INK, bold=True)
    deck.bullet_list(page, b.mm(180), b.mm(108), b.mm(139), [
        "MPC outputs normalized weights per traffic group and eligible UPF.",
        "Unhealthy or ineligible destinations are removed and weights renormalized.",
        "Each arriving session uses deterministic weighted rendezvous hashing.",
        "Admission checks the selected UPF session limit.",
        "The session stays anchored until its lifetime ends—no migration.",
    ], font_size=8.8, gap=9.2, bullet_color=b.AMBER)
    deck.footer(page, "optimization/cohort_mpc.py · simulator/macro/controllers.py · simulator/macro/engine.py · steering/hashing.py")

    # 9 — Campaign evaluation
    page = deck.new_slide("Stage 3 — the 30-pair campaign passes the demo gate, with visible tail risk",
                          "Optimizer · final evaluation")
    deck.metric(page, b.mm(9), b.mm(39), b.mm(72), "Mean-pair UL", "+10.52%",
                "95% CI 4.81–16.93%", tone=b.GREEN)
    deck.metric(page, b.mm(87), b.mm(39), b.mm(72), "Weighted UL", "+2.84%",
                "total overload area", tone=b.AMBER)
    deck.metric(page, b.mm(165), b.mm(39), b.mm(76), "UL dropped", "+12.42%",
                "aggregate reduction", tone=b.TEAL)
    deck.metric(page, b.mm(247), b.mm(39), b.mm(79), "DL dropped", "+9.34%",
                "aggregate reduction", tone=b.PURPLE)
    scenarios = [
        ("Demand surge", "8 pairs", 10.42, 2.57, b.GREEN),
        ("Scheduled fault", "8 pairs", 19.01, -23.50, b.AMBER),
        ("Unannounced outage", "7 pairs", 0.71, -9.84, b.RED),
        ("Mixed stress", "7 pairs", 1.92, -8.28, b.PURPLE),
    ]
    deck.card(page, b.mm(9), b.mm(81), b.mm(204), b.mm(81), fill=b.WHITE, accent=b.GREEN)
    deck.text(page, b.mm(16), b.mm(88), b.mm(190), b.mm(5),
              "AGGREGATE UL OVERLOAD-AREA REDUCTION", 7.8, b.MUTED, bold=True, font=b.MONO)
    for i, (label, pairs, agg, worst, color) in enumerate(scenarios):
        y = 103 + i * 13
        deck.text(page, b.mm(16), b.mm(y), b.mm(59), b.mm(4), label,
                  8.7, b.INK, bold=True)
        deck.text(page, b.mm(76), b.mm(y), b.mm(20), b.mm(4), pairs,
                  7, b.MUTED, font=b.MONO)
        deck.bar(page, b.mm(101), b.mm(y + 1), b.mm(63), b.mm(5), agg, 20,
                 color=color, label=f"+{agg:.2f}%")
    deck.card(page, b.mm(222), b.mm(81), b.mm(104), b.mm(81), fill=b.PALE_RED,
              line=0xECC3BF, accent=b.RED)
    deck.text(page, b.mm(229), b.mm(88), b.mm(90), b.mm(6),
              "Tail and release boundary", 13, b.RED, bold=True)
    deck.text(page, b.mm(229), b.mm(103), b.mm(90), b.mm(9),
              "Worst pair: −23.50%", 16, b.RED, bold=True)
    deck.bullet_list(page, b.mm(229), b.mm(120), b.mm(90), [
        "all aggregate directional guardrails pass",
        "fault-heavy individual regressions remain",
        "working demo candidate—not production-ready",
    ], font_size=8.5, gap=9.3, bullet_color=b.RED)
    deck.footer(page, "docs/cohort-mpc-full-campaign-results.md · demo_api/data/cohort_mpc_full_campaign_evidence_v1.json")

    # 10 — End-to-end data and steering flow
    page = deck.new_slide("From incoming data to a steering decision: one causal 10-minute iteration",
                          "End-to-end loop")
    steps = [
        ("01", "Collect", "30-second class arrivals and per-UPF sessions, UL/DL, capacity, health and quality", b.TEAL),
        ("02", "Close bucket", "aggregate 20 ticks; history becomes available only after the window closes", b.TEAL_2),
        ("03", "Forecast", "estimate future new-session demand and upper uncertainty by controllable group", b.PURPLE),
        ("04", "Optimize", "project anchored cohorts and future arrivals across the 12-window capacity path", b.GREEN),
        ("05", "Certify", "compare MPC with static from the identical state and retain static on any failure", b.AMBER),
        ("06", "Steer + observe", "new sessions use certified weights; realized placement and loss return as the next history", b.RED),
    ]
    for i, (num, head, body, color) in enumerate(steps):
        row, col = divmod(i, 2)
        x, y = 9 + col * 160.5, 41 + row * 40
        deck.card(page, b.mm(x), b.mm(y), b.mm(151), b.mm(33), fill=b.WHITE, accent=color)
        deck.circle(page, b.mm(x + 7), b.mm(y + 8), b.mm(15), color)
        deck.text(page, b.mm(x + 7), b.mm(y + 12), b.mm(15), b.mm(5), num,
                  8.5, b.WHITE, bold=True, align=b.ParagraphAdjust.CENTER, font=b.MONO)
        deck.text(page, b.mm(x + 28), b.mm(y + 6), b.mm(42), b.mm(5), head,
                  11, color, bold=True)
        deck.text(page, b.mm(x + 73), b.mm(y + 5), b.mm(70), b.mm(18), body,
                  8.4, b.INK, bold=True)
    deck.card(page, b.mm(9), b.mm(166), b.mm(317), b.mm(9), fill=b.PALE_TEAL,
              line=None, accent=b.TEAL)
    deck.text(page, b.mm(16), b.mm(168), b.mm(303), b.mm(5),
              "The dashboard displays this same loop; it does not substitute a prerecorded result for the live causal state.",
              8.4, b.INK, bold=True, align=b.ParagraphAdjust.CENTER)
    deck.footer(page, "simulator/macro/engine.py · experiments/train_forecaster.py · optimization/cohort_mpc.py · demo_api/runtime.py")

    # 11 — Demo features and screenshots
    page = deck.new_slide("The demo focuses on traffic pressure, steering and evidence",
                          "Working dashboard")
    shots = ROOT / "frontend/tests/demo.spec.ts-snapshots"
    prediction = b.crop_image(shots / "prediction-checkpoint-linux.png",
                              "compact-prediction.jpg", 1.75, focus=(0.47, 0.45))
    evidence = b.crop_image(shots / "evidence-ending-linux.png",
                            "compact-evidence.jpg", 1.75, focus=(0.5, 0.34))
    deck.image(page, prediction, b.mm(9), b.mm(41), b.mm(153), b.mm(87))
    deck.image(page, evidence, b.mm(173), b.mm(41), b.mm(153), b.mm(87))
    deck.card(page, b.mm(9), b.mm(137), b.mm(317), b.mm(29), fill=b.WHITE, accent=b.TEAL)
    features = [
        ("LIVE STATE", "UPF load, capacity, health, headroom and loss"),
        ("STEERING PROOF", "previous vs candidate weights and actual new-session placement"),
        ("FORECAST CHECK", "p50/p90 versus realized class demand"),
        ("EVIDENCE", "30-pair metrics, scenario tails and release boundary"),
    ]
    for i, (head, body) in enumerate(features):
        x = 16 + i * 76
        deck.text(page, b.mm(x), b.mm(143), b.mm(68), b.mm(4), head,
                  7.5, [b.TEAL, b.GREEN, b.PURPLE, b.AMBER][i], bold=True, font=b.MONO)
        deck.text(page, b.mm(x), b.mm(151), b.mm(68), b.mm(9), body,
                  8.1, b.INK, bold=True)
    deck.footer(page, "frontend/tests/demo.spec.ts-snapshots/ · docs/presenter-guide.md")

    # 12 — Demo story / interpretation
    page = deck.new_slide("A short guided scenario explains what changes—and what does not",
                          "Working demo · walkthrough")
    rows = [
        ["STEP", "AUDIENCE SEES", "INTERPRETATION"],
        ["1 · Normal", "all UPFs inside safe envelopes", "existing sessions are anchored"],
        ["2 · Pressure", "class demand rises; capacity threat becomes visible", "forecast inputs are still causal"],
        ["3 · Predict", "future demand + same-state static comparison", "forecast estimates; MPC chooses"],
        ["4 · Divert", "future-session route widths change", "weights change, not existing sessions"],
        ["5 · Result", "actual placement, error, loss and frozen campaign", "reduced exposure—not perfect prevention"],
    ]
    deck.table(page, b.mm(9), b.mm(42), [b.mm(52), b.mm(135), b.mm(130)],
               rows, row_h=19.2, font_size=8.8)
    deck.card(page, b.mm(9), b.mm(164), b.mm(317), b.mm(11), fill=b.PALE_AMBER,
              line=None, accent=b.AMBER)
    deck.text(page, b.mm(16), b.mm(166.5), b.mm(303), b.mm(6),
              "Key language: ‘future sessions redirected’ and ‘modeled exposure reduced’; never ‘traffic migrated’ or ‘overload prevented.’",
              8.6, b.INK, bold=True, align=b.ParagraphAdjust.CENTER)
    deck.footer(page, "docs/presenter-guide.md · demo_api/story.py · demo_api/runtime.py")

    # 13 — boundary and next steps
    page = deck.new_slide("What is ready today—and what C-DOT must provide for a calibrated pilot",
                          "Integration boundary")
    deck.card(page, b.mm(9), b.mm(40), b.mm(151), b.mm(117), fill=b.PALE_GREEN,
              line=0xB9DACA, accent=b.GREEN)
    deck.text(page, b.mm(16), b.mm(47), b.mm(136), b.mm(6),
              "Ready in the repository", 15, b.GREEN, bold=True)
    deck.bullet_list(page, b.mm(16), b.mm(62), b.mm(136), [
        "deterministic multi-class traffic and fault simulation",
        "canonical artifacts and reproducible experiment evaluation",
        "forecast bundle with uncertainty and causal inference",
        "static-certified cohort MPC and safe fallback",
        "new-session-only simulated steering",
        "working evidence-oriented dashboard",
    ], font_size=9.2, gap=12.3, bullet_color=b.GREEN)
    deck.card(page, b.mm(169), b.mm(40), b.mm(157), b.mm(117), fill=b.PALE_AMBER,
              line=0xE5C78D, accent=b.AMBER)
    deck.text(page, b.mm(176), b.mm(47), b.mm(142), b.mm(6),
              "Needed from C-DOT / testbed", 15, b.AMBER, bold=True)
    deck.bullet_list(page, b.mm(176), b.mm(62), b.mm(142), [
        "live telemetry names, labels, quality and counter semantics",
        "measured directional and session capacity envelopes",
        "real topology, locality and UPF eligibility rules",
        "authenticated SMF/EMS policy publication interface",
        "decision on established-session migration capability",
        "shadow evaluation, untouched release seeds and tail gate",
    ], font_size=9.2, gap=12.3, bullet_color=b.AMBER)
    deck.card(page, b.mm(9), b.mm(165), b.mm(317), b.mm(10), fill=b.NAVY_2,
              line=None, accent=b.TEAL)
    deck.text(page, b.mm(16), b.mm(167.5), b.mm(303), b.mm(5),
              "Recommended sequence: map telemetry → calibrate capacities → advisory replay → shadow recommendations → bounded new-session pilot.",
              8.6, b.WHITE, bold=True, align=b.ParagraphAdjust.CENTER)
    deck.footer(page, "docs/extreme-data-spec-and-cdot-gap-analysis.md · docs/cdot-session-migration-decision.md · docs/end-to-end-runbook.md")

    # 14 — close
    page = deck.new_slide("The evidence supports a credible working demo and a clear next pilot",
                          "Conclusion", dark=True)
    deck.text(page, b.mm(10), b.mm(47), b.mm(197), b.mm(26),
              "Model the traffic.\nForecast the pressure.\nCertify before steering.",
              28, b.WHITE, bold=True)
    deck.text(page, b.mm(10), b.mm(91), b.mm(197), b.mm(18),
              "The strongest result is not one headline number—it is an end-to-end loop whose assumptions, experiments, gains and limitations remain traceable.",
              12.8, 0xC2D3DA)
    deck.card(page, b.mm(221), b.mm(45), b.mm(105), b.mm(91),
              fill=0x193541, line=0x395663, accent=b.GREEN)
    deck.text(page, b.mm(229), b.mm(53), b.mm(89), b.mm(5),
              "EVIDENCE SNAPSHOT", 8.3, b.CYAN, bold=True, font=b.MONO)
    deck.bullet_list(page, b.mm(229), b.mm(68), b.mm(88), [
        "112-day synthetic history",
        "7.63% forecast WAPE",
        "10.52% mean-pair UL gain",
        "2.84% severity-weighted gain",
        "all aggregate guardrails pass",
        "fault-tail robustness still open",
        "new-session steering only",
    ], font_size=9.5, gap=9.0, color=b.WHITE, bullet_color=b.GREEN)
    deck.text(page, b.mm(10), b.mm(145), b.mm(198), b.mm(6),
              "Proposed discussion", 9, b.CYAN, bold=True, font=b.MONO)
    deck.text(page, b.mm(10), b.mm(156), b.mm(198), b.mm(10),
              "Which C-DOT telemetry and actuation interfaces can support a calibrated shadow pilot?",
              14, b.WHITE, bold=True)
    deck.footer(page, "C-DOT predictive UPF steering · concise technical review", dark=True)


def main() -> int:
    b.PPTX = b.OUT / "CDOT_Predictive_UPF_Steering_Concise_14_Slide_Deck.pptx"
    b.PDF = b.OUT / "CDOT_Predictive_UPF_Steering_Concise_14_Slide_Deck.pdf"
    ctx = b.connect()
    deck = b.Deck(ctx)
    build(deck)
    deck.save()
    print(b.PPTX)
    print(b.PDF)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
