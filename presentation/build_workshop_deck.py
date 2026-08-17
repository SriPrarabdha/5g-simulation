#!/usr/bin/env python3
"""Build the seven-slide interactive C-DOT UPF workshop deck."""

from __future__ import annotations

from pathlib import Path

import build_deck as b


ROOT = Path(__file__).resolve().parents[1]


def stage_label(deck: b.Deck, page, text: str, *, dark: bool = False) -> None:
    deck.text(page, b.mm(10), b.mm(31), b.mm(210), b.mm(6), text, 8.5,
              b.CYAN if dark else b.TEAL, bold=True, font=b.MONO, spacing=1.3)


def build(deck: b.Deck) -> None:
    # 1 — Cold open
    page = deck.new_slide("", "", dark=True)
    deck.text(page, b.mm(10), b.mm(9), b.mm(210), b.mm(6),
              "C-DOT · UPF CONTROL-ROOM LAB · 28TH 11:30–13:00", 8.5,
              b.CYAN, bold=True, font=b.MONO, spacing=1.3)
    deck.text(page, b.mm(10), b.mm(35), b.mm(166), b.mm(27),
              "You are the\nnetwork controller.", 30, b.WHITE, bold=True)
    deck.text(page, b.mm(10), b.mm(72), b.mm(160), b.mm(14),
              "A stadium surge is approaching. Choose before you see the result.",
              13, 0xC7D8DE)
    upfs = [
        ("UPF-A", "UL headroom 42", 218, 49, b.AMBER),
        ("UPF-B", "UL headroom 96", 268, 92, b.TEAL_2),
        ("UPF-C", "UL headroom 131", 216, 102, b.GREEN),
    ]
    for name, detail, x, y, color in upfs:
        deck.circle(page, b.mm(x), b.mm(y), b.mm(31), color)
        deck.text(page, b.mm(x), b.mm(y + 7), b.mm(31), b.mm(5), name,
                  10, b.NAVY, bold=True, align=b.ParagraphAdjust.CENTER)
        deck.text(page, b.mm(x - 6), b.mm(y + 33), b.mm(43), b.mm(5), detail,
                  7.3, 0xC3D5DB, align=b.ParagraphAdjust.CENTER, font=b.MONO)
    deck.line(page, b.mm(176), b.mm(104), b.mm(215), b.mm(64), b.PURPLE, 2.2)
    deck.line(page, b.mm(176), b.mm(104), b.mm(266), b.mm(107), b.PURPLE, 2.2)
    deck.line(page, b.mm(176), b.mm(104), b.mm(231), b.mm(117), b.PURPLE, 2.2)
    deck.text(page, b.mm(146), b.mm(96), b.mm(35), b.mm(10), "STADIUM\nSURGE", 8,
              b.PURPLE, bold=True, font=b.MONO, align=b.ParagraphAdjust.CENTER)
    votes = [("A", "Keep static"), ("B", "React after overload"), ("C", "Steer predictively")]
    for i, (letter, label) in enumerate(votes):
        x = 10 + i * 104
        deck.card(page, b.mm(x), b.mm(139), b.mm(96), b.mm(24), fill=0x193541,
                  line=0x3B5965, accent=[b.TEAL, b.AMBER, b.PURPLE][i])
        deck.text(page, b.mm(x + 6), b.mm(145), b.mm(12), b.mm(8), letter, 13,
                  [b.TEAL, b.AMBER, b.PURPLE][i], bold=True, font=b.MONO)
        deck.text(page, b.mm(x + 21), b.mm(145), b.mm(69), b.mm(7), label, 10.5,
                  b.WHITE, bold=True)
    deck.text(page, b.mm(10), b.mm(171), b.mm(300), b.mm(5),
              "Vote now. We reveal the matched result at minute 72.", 9.4, b.CYAN, bold=True)
    deck.footer(page, "Synthetic, deterministic simulation · no live C-DOT actuation", dark=True)

    # 2 — Architecture loop
    page = deck.new_slide("One causal loop—from observed pressure to later evidence",
                          "Workshop control loop")
    stage_label(deck, page, "02 · SYSTEM STORY")
    stages = [
        ("TRAFFIC", "event + offered demand", b.TEAL),
        ("SIMULATE", "carried · overload · loss", b.TEAL_2),
        ("FORECAST", "closed history · p50/p90", b.PURPLE),
        ("CERTIFY", "eligibility · capacity · gate", b.AMBER),
        ("STEER", "future sessions only", b.GREEN),
        ("MEASURE", "later telemetry + evidence", b.RED),
    ]
    for i, (head, body, color) in enumerate(stages):
        x = 9 + i * 52.8
        deck.card(page, b.mm(x), b.mm(57), b.mm(46), b.mm(39), fill=b.WHITE, accent=color)
        deck.text(page, b.mm(x + 5), b.mm(64), b.mm(36), b.mm(5), f"0{i+1}", 7.5,
                  color, bold=True, font=b.MONO)
        deck.text(page, b.mm(x + 5), b.mm(75), b.mm(36), b.mm(5), head, 9.5,
                  b.INK, bold=True)
        deck.text(page, b.mm(x + 5), b.mm(84), b.mm(36), b.mm(8), body, 7.8,
                  b.SLATE)
        if i < 5:
            deck.arrow(page, b.mm(x + 46), b.mm(76), b.mm(x + 52.8), color=b.SLATE)
    deck.line(page, b.mm(32), b.mm(104), b.mm(305), b.mm(104), b.TEAL, 1.3)
    deck.text(page, b.mm(28), b.mm(101), b.mm(4), b.mm(6), "‹", 18, b.TEAL,
              bold=True, align=b.ParagraphAdjust.CENTER)
    deck.text(page, b.mm(97), b.mm(108), b.mm(146), b.mm(6),
              "realized outcome becomes closed history for the next decision", 8,
              b.TEAL, bold=True, align=b.ParagraphAdjust.CENTER, font=b.MONO)
    deck.card(page, b.mm(9), b.mm(132), b.mm(151), b.mm(34), fill=b.PALE_TEAL,
              line=None, accent=b.TEAL)
    deck.text(page, b.mm(16), b.mm(139), b.mm(137), b.mm(5),
              "Participant workspace", 11, b.INK, bold=True)
    deck.text(page, b.mm(16), b.mm(150), b.mm(137), b.mm(9),
              "Produces a WorkshopDecision record. It has no presenter credentials and cannot publish policy.",
              8.7, b.INK, bold=True)
    deck.card(page, b.mm(169), b.mm(132), b.mm(157), b.mm(34), fill=b.PALE_AMBER,
              line=None, accent=b.AMBER)
    deck.text(page, b.mm(176), b.mm(139), b.mm(143), b.mm(5),
              "Authoritative presenter runtime", 11, b.INK, bold=True)
    deck.text(page, b.mm(176), b.mm(150), b.mm(143), b.mm(9),
              "Translates one team recommendation into controlled dashboard inputs and records the causal run.",
              8.7, b.INK, bold=True)
    deck.footer(page, "workshop/CDOT_UPF_Closed_Loop_Lab.ipynb · demo_api/runtime.py · steering/policy.py")

    # 3 — Traffic semantics
    page = deck.new_slide("Demand is what arrived—not only what the network managed to carry",
                          "Traffic semantics")
    stage_label(deck, page, "03 · TRAFFIC → SIMULATE")
    x0, y0, cw, ch = b.mm(20), b.mm(54), b.mm(190), b.mm(74)
    for tick in range(5):
        yy = y0 + ch * tick / 4
        deck.line(page, x0, yy, x0 + cw, yy, b.GRID, .35)
    offered_points = [(20, 108), (48, 104), (76, 101), (104, 91), (132, 48), (160, 42), (188, 50), (210, 97)]
    carried_points = [(20, 108), (48, 104), (76, 101), (104, 91), (132, 73), (160, 73), (188, 73), (210, 97)]
    for values, color in ((offered_points, b.PURPLE), (carried_points, b.TEAL)):
        for i in range(1, len(values)):
            deck.line(page, b.mm(values[i-1][0]), b.mm(values[i-1][1]),
                      b.mm(values[i][0]), b.mm(values[i][1]), color, 1.8)
    deck.text(page, b.mm(124), b.mm(38), b.mm(45), b.mm(5), "OFFERED", 8,
              b.PURPLE, bold=True, font=b.MONO)
    deck.text(page, b.mm(174), b.mm(64), b.mm(45), b.mm(5), "CARRIED", 8,
              b.TEAL, bold=True, font=b.MONO)
    deck.text(page, b.mm(137), b.mm(79), b.mm(36), b.mm(12), "OVERLOAD\n+ LOSS", 8,
              b.RED, bold=True, font=b.MONO, align=b.ParagraphAdjust.CENTER)
    deck.line(page, b.mm(150), b.mm(57), b.mm(150), b.mm(72), b.RED, 1.2)
    deck.card(page, b.mm(222), b.mm(44), b.mm(104), b.mm(93), fill=b.WHITE, accent=b.TEAL)
    semantics = [
        ("OFFERED", "attempted session demand", b.PURPLE),
        ("CARRIED", "successfully transported", b.TEAL),
        ("OVERLOAD", "above service envelope", b.AMBER),
        ("LOSS", "offered but not carried", b.RED),
    ]
    for i, (head, detail, color) in enumerate(semantics):
        y = 53 + i * 19
        deck.text(page, b.mm(230), b.mm(y), b.mm(34), b.mm(4), head, 7.5,
                  color, bold=True, font=b.MONO)
        deck.text(page, b.mm(266), b.mm(y), b.mm(51), b.mm(5), detail, 8.1,
                  b.INK, bold=True)
    deck.card(page, b.mm(9), b.mm(148), b.mm(317), b.mm(18), fill=b.PALE_AMBER,
              line=None, accent=b.AMBER)
    deck.text(page, b.mm(16), b.mm(153), b.mm(303), b.mm(8),
              "If you train demand on carried throughput during constraint, the label hides the demand you most need to predict.",
              9.6, b.INK, bold=True, align=b.ParagraphAdjust.CENTER)
    deck.footer(page, "Notebook TODO 1 · offered, carried, overload and loss remain separate")

    # 4 — Forecast choice
    page = deck.new_slide("Forecast from closed history; choose how much uncertainty to carry",
                          "Forecast choice")
    stage_label(deck, page, "04 · FORECAST")
    for i in range(6):
        x = 12 + i * 32
        deck.card(page, b.mm(x), b.mm(56), b.mm(27), b.mm(35), fill=b.WHITE,
                  accent=b.TEAL if i < 5 else b.PURPLE)
        deck.text(page, b.mm(x + 5), b.mm(63), b.mm(17), b.mm(5), f"t−{5-i}", 8,
                  b.MUTED, font=b.MONO, align=b.ParagraphAdjust.CENTER)
        deck.text(page, b.mm(x + 5), b.mm(75), b.mm(17), b.mm(5),
                  [166, 174, 181, 188, 196, 204][i].__str__(), 10,
                  b.INK, bold=True, align=b.ParagraphAdjust.CENTER)
    deck.arrow(page, b.mm(204), b.mm(73), b.mm(230), color=b.SLATE)
    deck.card(page, b.mm(238), b.mm(47), b.mm(88), b.mm(52), fill=b.NAVY_2,
              line=None, accent=b.PURPLE)
    deck.text(page, b.mm(246), b.mm(54), b.mm(72), b.mm(5), "TARGET t", 8,
              b.CYAN, bold=True, font=b.MONO)
    deck.text(page, b.mm(246), b.mm(67), b.mm(31), b.mm(8), "p50\n185", 14,
              b.WHITE, bold=True, align=b.ParagraphAdjust.CENTER)
    deck.text(page, b.mm(283), b.mm(67), b.mm(31), b.mm(8), "p90\n222", 14,
              b.AMBER, bold=True, align=b.ParagraphAdjust.CENTER)
    deck.text(page, b.mm(12), b.mm(105), b.mm(190), b.mm(7),
              "SOURCE WINDOW ENDS HERE", 8, b.TEAL, bold=True, font=b.MONO)
    deck.line(page, b.mm(12), b.mm(116), b.mm(205), b.mm(116), b.TEAL, 1.3)
    deck.text(page, b.mm(238), b.mm(105), b.mm(88), b.mm(7),
              "TARGET STARTS HERE", 8, b.PURPLE, bold=True, font=b.MONO)
    deck.line(page, b.mm(238), b.mm(116), b.mm(326), b.mm(116), b.PURPLE, 1.3)
    deck.card(page, b.mm(9), b.mm(137), b.mm(99), b.mm(29), fill=b.WHITE, accent=b.TEAL)
    deck.text(page, b.mm(16), b.mm(144), b.mm(85), b.mm(5), "p50", 13, b.TEAL, bold=True)
    deck.text(page, b.mm(16), b.mm(154), b.mm(85), b.mm(6), "central estimate · less reserve", 8.2, b.INK, bold=True)
    deck.card(page, b.mm(117), b.mm(137), b.mm(99), b.mm(29), fill=b.WHITE, accent=b.PURPLE)
    deck.text(page, b.mm(124), b.mm(144), b.mm(85), b.mm(5), "p90", 13, b.PURPLE, bold=True)
    deck.text(page, b.mm(124), b.mm(154), b.mm(85), b.mm(6), "conservative · more headroom", 8.2, b.INK, bold=True)
    deck.card(page, b.mm(225), b.mm(137), b.mm(101), b.mm(29), fill=b.PALE_AMBER, line=None, accent=b.AMBER)
    deck.text(page, b.mm(232), b.mm(144), b.mm(87), b.mm(5), "No guarantee", 12, b.AMBER, bold=True)
    deck.text(page, b.mm(232), b.mm(154), b.mm(87), b.mm(6), "unannounced surge may miss both", 8.2, b.INK, bold=True)
    deck.footer(page, "Forecast/1.0 enforces source_window_end ≤ target_window.start · Notebook TODO 2")

    # 5 — Safety constraints
    page = deck.new_slide("A useful recommendation is still rejected unless it is safe to publish",
                          "Policy certification")
    stage_label(deck, page, "05 · CERTIFY / OPTIMIZE")
    checks = [
        ("CAUSAL", "features stop before target", b.PURPLE),
        ("ELIGIBLE", "group + locality path exist", b.TEAL),
        ("HEALTHY", "unavailable UPFs get no weight", b.GREEN),
        ("NORMALIZED", "finite [0,1] · sum exactly 1", b.AMBER),
        ("CAPACITY", "UL · DL · session envelope", b.RED),
        ("ANCHORED", "new sessions only", b.TEAL_2),
    ]
    for i, (head, detail, color) in enumerate(checks):
        row, col = divmod(i, 3)
        x, y = 9 + col * 106, 45 + row * 43
        deck.card(page, b.mm(x), b.mm(y), b.mm(98), b.mm(34), fill=b.WHITE, accent=color)
        deck.circle(page, b.mm(x + 7), b.mm(y + 8), b.mm(12), color)
        deck.text(page, b.mm(x + 7), b.mm(y + 10), b.mm(12), b.mm(5), "✓", 10,
                  b.WHITE, bold=True, align=b.ParagraphAdjust.CENTER)
        deck.text(page, b.mm(x + 25), b.mm(y + 7), b.mm(65), b.mm(5), head, 9.2,
                  color, bold=True, font=b.MONO)
        deck.text(page, b.mm(x + 25), b.mm(y + 17), b.mm(65), b.mm(8), detail, 8.2,
                  b.INK, bold=True)
    deck.card(page, b.mm(9), b.mm(139), b.mm(151), b.mm(28), fill=b.PALE_RED,
              line=0xECC3BF, accent=b.RED)
    deck.text(page, b.mm(16), b.mm(146), b.mm(137), b.mm(5), "INVALID / UNSAFE", 8,
              b.RED, bold=True, font=b.MONO)
    deck.text(page, b.mm(16), b.mm(156), b.mm(137), b.mm(6),
              "Reject recommendation · preserve last safe/static policy", 9.2,
              b.INK, bold=True)
    deck.arrow(page, b.mm(160), b.mm(153), b.mm(177), color=b.SLATE)
    deck.card(page, b.mm(177), b.mm(139), b.mm(149), b.mm(28), fill=b.PALE_GREEN,
              line=0xB9DACA, accent=b.GREEN)
    deck.text(page, b.mm(184), b.mm(146), b.mm(135), b.mm(5), "VALID", 8,
              b.GREEN, bold=True, font=b.MONO)
    deck.text(page, b.mm(184), b.mm(156), b.mm(135), b.mm(6),
              "Publish weights for sessions arriving after activation", 9.2,
              b.INK, bold=True)
    deck.footer(page, "schemas/policy.py · steering/policy.py · Notebook TODO 3 + required safety drill")

    # 6 — Evidence
    page = deck.new_slide("The frozen campaign clears the demo gate—and keeps tail risk visible",
                          "Matched evidence")
    stage_label(deck, page, "06 · REVEAL + COMPARE")
    deck.metric(page, b.mm(9), b.mm(42), b.mm(72), "Mean-pair UL", "+10.52%",
                "95% CI 4.81–16.93%", tone=b.GREEN)
    deck.metric(page, b.mm(88), b.mm(42), b.mm(72), "Weighted UL", "+2.84%",
                "severity-weighted total", tone=b.AMBER)
    deck.metric(page, b.mm(167), b.mm(42), b.mm(72), "Matched pairs", "30",
                "static vs frozen MPC", tone=b.TEAL)
    deck.metric(page, b.mm(246), b.mm(42), b.mm(80), "Worst pair", "−23.50%",
                "fault-heavy regression", tone=b.RED)
    scenarios = [
        ("Demand surge", 10.42, 2.57, b.GREEN),
        ("Scheduled fault", 19.01, -23.50, b.AMBER),
        ("Unannounced outage", .71, -9.84, b.RED),
        ("Mixed stress", 1.92, -8.28, b.PURPLE),
    ]
    deck.card(page, b.mm(9), b.mm(93), b.mm(204), b.mm(69), fill=b.WHITE, accent=b.GREEN)
    deck.text(page, b.mm(16), b.mm(100), b.mm(190), b.mm(5),
              "AGGREGATE GAIN / WORST PAIR", 7.8, b.MUTED, bold=True, font=b.MONO)
    for i, (name, aggregate, worst, color) in enumerate(scenarios):
        y = 114 + i * 11
        deck.text(page, b.mm(16), b.mm(y), b.mm(62), b.mm(4), name, 8.4,
                  b.INK, bold=True)
        deck.text(page, b.mm(82), b.mm(y), b.mm(42), b.mm(4), f"+{aggregate:.2f}%", 8.2,
                  color, bold=True, font=b.MONO)
        deck.text(page, b.mm(139), b.mm(y), b.mm(58), b.mm(4), f"worst {worst:+.2f}%", 8.2,
                  b.RED if worst < 0 else b.GREEN, bold=True, font=b.MONO)
    deck.card(page, b.mm(222), b.mm(93), b.mm(104), b.mm(69), fill=b.NAVY_2,
              line=None, accent=b.AMBER)
    deck.text(page, b.mm(229), b.mm(101), b.mm(90), b.mm(6), "Say this", 13,
              b.WHITE, bold=True)
    deck.text(page, b.mm(229), b.mm(117), b.mm(90), b.mm(22),
              "Reduced modeled exposure for future sessions.", 13, b.CYAN, bold=True)
    deck.text(page, b.mm(229), b.mm(145), b.mm(90), b.mm(10),
              "Not overload prevented.\nNot production ready.", 9, b.AMBER, bold=True)
    deck.footer(page, "demo_api/data/cohort_mpc_full_campaign_evidence_v1.json · frozen profile and checksums")

    # 7 — Integration boundary + close
    page = deck.new_slide("The next step is a bounded C-DOT advisory pilot—not autonomous control",
                          "Integration boundary", dark=True)
    stage_label(deck, page, "07 · C-DOT CO-DESIGN + CLOSE", dark=True)
    steps = [
        ("PROMETHEUS", "metrics · labels · quality", b.TEAL),
        ("CALIBRATE", "UL/DL/session envelopes", b.PURPLE),
        ("ELIGIBILITY", "slice · DNN · locality", b.AMBER),
        ("SMF HOOK", "new-session selection", b.GREEN),
        ("PUBLISH", "auth · atomicity · audit", b.RED),
        ("ROLL BACK", "expiry · last safe policy", b.TEAL_2),
    ]
    for i, (head, body, color) in enumerate(steps):
        x = 9 + i * 52.8
        deck.card(page, b.mm(x), b.mm(52), b.mm(46), b.mm(37), fill=0x193541,
                  line=0x3D5B67, accent=color)
        deck.text(page, b.mm(x + 5), b.mm(59), b.mm(36), b.mm(5), head, 7.2,
                  color, bold=True, font=b.MONO)
        deck.text(page, b.mm(x + 5), b.mm(71), b.mm(36), b.mm(9), body, 8,
                  b.WHITE, bold=True)
        if i < 5:
            deck.arrow(page, b.mm(x + 46), b.mm(70), b.mm(x + 52.8), color=0x66808A)
    deck.text(page, b.mm(10), b.mm(108), b.mm(245), b.mm(6),
              "RECOMMENDED PILOT SEQUENCE", 8.5, b.CYAN, bold=True, font=b.MONO)
    deck.text(page, b.mm(10), b.mm(121), b.mm(300), b.mm(12),
              "map telemetry → calibrate capacity → advisory replay → shadow recommendations → bounded new-session pilot",
              14, b.WHITE, bold=True)
    deck.card(page, b.mm(9), b.mm(148), b.mm(317), b.mm(22), fill=0x213E48,
              line=0x48636D, accent=b.AMBER)
    deck.text(page, b.mm(17), b.mm(154), b.mm(302), b.mm(8),
              "We would deploy this in advisory mode only after ________________________________.",
              12, b.WHITE, bold=True, align=b.ParagraphAdjust.CENTER)
    deck.footer(page, "Observe → predict → certify → steer future sessions → measure", dark=True)


def main() -> int:
    b.PPTX = b.OUT / "CDOT_UPF_Closed_Loop_Workshop_7_Slides.pptx"
    b.PDF = b.OUT / "CDOT_UPF_Closed_Loop_Workshop_7_Slides.pdf"
    context = b.connect()
    deck = b.Deck(context)
    build(deck)
    deck.save()
    print(b.PPTX)
    print(b.PDF)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
