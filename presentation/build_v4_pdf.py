"""Rebuild CDOT_UPF_Steering_Evidence_Review.pdf with the v4 slides spliced in.

There is no LibreOffice on this cluster, so the deck's own PPTX->PDF path is
unavailable. Instead the eight new slides are drawn directly to PDF pages at
the deck's exact geometry, inserted at the same indices used in the PPTX, and
the page numbers already printed on the original pages are restamped so the
whole document stays consistently numbered.

Georgia, Segoe UI and Consolas are Microsoft fonts and are not installed here.
Gelasio is metric-compatible with Georgia and Cascadia Mono is Consolas' direct
descendant; Open Sans stands in for Segoe UI. Re-exporting the PPTX from
PowerPoint will reproduce these pages in the original faces.
"""
from __future__ import annotations

import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
FIG = ROOT / "generated_assets" / "v4"
PPTX_PDF = ROOT / "CDOT_UPF_Steering_Evidence_Review.pdf"
DATA = json.load(open(FIG / "deck-data.json"))
FONTS = Path("/home/prarabdhas/.fonts")

PT = 72.0
PW, PH = 13.3330708661 * PT, 7.5 * PT

BG = HexColor("#FAFAFB"); CARD = HexColor("#FFFFFF"); BORDER = HexColor("#DEE3E8")
RULE = HexColor("#EBEEF1"); TEAL = HexColor("#0F4C5C"); INK = HexColor("#111721")
BODY = HexColor("#3C4653"); MUTED = HexColor("#6C7683"); FAINT = HexColor("#98A2AE")
GREEN = HexColor("#2E7D5B"); RED = HexColor("#9C3B35"); AMBER = HexColor("#B5761F")
SOFT = HexColor("#ECF3F0")

M, W = 0.72, 11.89


def register() -> tuple[str, str, str, str, str]:
    pdfmetrics.registerFont(TTFont("Gelasio", str(FONTS / "Gelasio.ttf")))
    pdfmetrics.registerFont(TTFont("CascadiaMono", str(FONTS / "CascadiaMono.ttf")))
    pdfmetrics.registerFont(TTFont("CascadiaMono-Bd", str(FONTS / "CascadiaMono-SemiBold.ttf")))
    import matplotlib.font_manager as fm
    reg = fm.findfont(fm.FontProperties(family="Open Sans", weight="regular"))
    bold = fm.findfont(fm.FontProperties(family="Open Sans", weight="bold"))
    pdfmetrics.registerFont(TTFont("OpenSans", reg))
    pdfmetrics.registerFont(TTFont("OpenSans-Bd", bold))
    return "Gelasio", "OpenSans", "OpenSans-Bd", "CascadiaMono", "CascadiaMono-Bd"


SERIF, SANS, SANS_B, MONO, MONO_B = register()


def X(v: float) -> float:  return v * PT
def Y(v: float) -> float:  return PH - v * PT          # top-left -> PDF origin


def rect(c, l, t, w, h, fill=None, stroke=None, lw=0.6):
    if fill: c.setFillColor(fill)
    if stroke: c.setStrokeColor(stroke); c.setLineWidth(lw)
    c.rect(X(l), Y(t + h), X(w), X(h), fill=1 if fill else 0, stroke=1 if stroke else 0)


def wrap(c, text, font, size, width_in):
    """Greedy wrap at the deck's box width."""
    out, limit = [], X(width_in)
    for para in text.split("\n"):
        words, line = para.split(), ""
        for word in words:
            trial = f"{line} {word}".strip()
            if c.stringWidth(trial, font, size) <= limit:
                line = trial
            else:
                if line: out.append(line)
                line = word
        out.append(line)
    return out


def text(c, l, t, w, body, *, font=SANS, size=12.0, color=BODY, leading=None,
         align="l"):
    c.setFont(font, size); c.setFillColor(color)
    lead = leading or size * 1.30
    y = t * PT + size * 0.92
    for line in wrap(c, body, font, size, w):
        if align == "r":
            c.drawRightString(X(l + w), PH - y, line)
        else:
            c.drawString(X(l), PH - y, line)
        y += lead
    return y / PT


def chrome(c, eyebrow, title, section, *, intro=None, intro_w=9.28, page=None):
    rect(c, 0, 0, 13.333, 7.5, fill=BG)
    text(c, M, 0.52, W, eyebrow, font=MONO_B, size=10.5, color=TEAL)
    text(c, M, 0.80, W, title, font=SERIF, size=30, color=INK, leading=34)
    if intro:
        text(c, M, 1.62, intro_w, intro, size=13, color=BODY, leading=17.5)
    rect(c, M, 6.98, W, 0.012, fill=RULE)
    text(c, M, 7.08, 7.14, section, font=MONO, size=8.5, color=FAINT)
    if page is not None:
        c.setFont(MONO, 8.5); c.setFillColor(FAINT)
        c.drawRightString(X(M + W), PH - (7.04 * PT + 8.0), f"{page:02d}")


def card(c, l, t, w, h, label, *, accent=None):
    rect(c, l, t, w, h, fill=CARD, stroke=BORDER)
    if accent: rect(c, l, t, 0.04, h, fill=accent)
    if label: text(c, l + 0.22, t + 0.18, w - 0.44, label, font=MONO_B, size=9.19, color=MUTED)


def stat(c, l, t, w, value, caption, color=TEAL, size=26):
    rect(c, l, t, w, 1.28, fill=CARD, stroke=BORDER)
    text(c, l + 0.18, t + 0.16, w - 0.36, value, font=SERIF, size=size, color=color)
    text(c, l + 0.18, t + 0.80, w - 0.36, caption, font=MONO_B, size=8.2, color=MUTED, leading=10.5)


def bullet(c, l, t, w, head, body):
    rect(c, l, t + 0.07, 0.07, 0.07, fill=TEAL)
    text(c, l + 0.20, t, w - 0.20, head, font=SANS_B, size=11.4, color=INK)
    text(c, l + 0.20, t + 0.27, w - 0.20, body, size=10.7, color=BODY, leading=13.5)


def image(c, name, l, t, w):
    img = ImageReader(str(FIG / name))
    iw, ih = img.getSize()
    h = w * ih / iw
    c.drawImage(img, X(l), Y(t + h), X(w), X(h), mask="auto")
    return h


# ============================== the eight pages ============================
def p_headline(c, n):
    chrome(c, "THE V4 RESULT", "Predictive steering wins where the loss is declared",
           "00 · THE BIG PICTURE", page=n, intro_w=11.4,
           intro="Re-run after an audit of the evaluator, on 25 000 paired simulated days. The "
                 "controllers now beat the simple rule — but only for capacity loss that is announced "
                 "in advance, and the boundary is as important as the gain.")
    tiles = [("+24.0%", "OVERLOAD REMOVED\nON UNSEEN SEEDS", TEAL),
             ("0 / 473", "REGRESSIONS ON\nDECLARED EVENTS", GREEN),
             ("36 / 160", "CONFIGURATIONS PASSED\nEVERY GATE", GREEN),
             ("3 / 4", "SURVIVED FRESH-SEED\nVALIDATION", GREEN),
             ("25 000", "PAIRED SIMULATED\nDAYS", MUTED)]
    for i, (v, cap, col) in enumerate(tiles):
        stat(c, M + i * 2.41, 2.42, 2.21, v, cap, col)
    card(c, M, 3.98, 5.78, 2.62, "WHAT THE CONTROLLERS NOW WIN")
    text(c, M + 0.22, 4.50, 5.34,
         "On declared maintenance the guarded pre-drain removes 55.2% of uplink overload, and on "
         "maintenance followed by a stadium-scale surge, 52.2%. Across 473 pairs of those two "
         "families it did not lose a single one.\n\n"
         "Cohort MPC clears every gate too, at 33.1% and 32.4%, with a tenth of the collateral harm "
         "and a tenth of the routing churn.", size=12.4, leading=16.5)
    card(c, 6.83, 3.98, 5.78, 2.62, "WHAT IT STILL COSTS")
    text(c, 7.05, 4.50, 5.34,
         "When maintenance is announced and then a second, unannounced brownout lands on a box the "
         "controller has already drained onto, pre-drain loses in 30.5% of cases. The family is still "
         "net positive, but the worst cases are severe.\n\n"
         "MPC cuts that exposure to 6.6% of cases. That is the trade C-DOT chooses between.",
         size=12.4, leading=16.5)


CAVEATS = [
    ("This is a shadow controller in simulation.",
     "No policy touched live traffic. Every record in the project is labelled synthetic and none of "
     "it is calibrated to real C-DOT capacity figures."),
    ("Only about 3% of load is controllable.",
     "The lever is where NEW sessions are placed. Sessions already established are never migrated, so "
     "a controller can only steer the margin."),
    ("Gains are for announced capacity loss only.",
     "With no declared event the controller publishes bit-exact static output. Both pure-surprise "
     "families are deliberate ties, verified byte for byte."),
    ("One stress family produced no measurable stress.",
     "An unannounced demand surge never overloaded any box, even at 4x arrivals across a zone. That "
     "family scores a guaranteed zero, so the headline is conservative."),
    ("Chosen on one seed pool, judged on another.",
     "The headline numbers come from a pool never inspected before the candidates were frozen. "
     "Screening numbers are 3-4 points higher and are not the claim."),
]


def p_caveats(c, n):
    chrome(c, "READ THIS FIRST", "Five caveats that bound every number in this deck",
           "00 · THE BIG PICTURE", page=n, intro_w=11.4,
           intro="None of these are disclaimers added afterwards. Each one changes how a figure on a "
                 "later slide should be read.")
    for i, (head, body) in enumerate(CAVEATS):
        col, row = divmod(i, 3)
        l = M if col == 0 else 6.83
        t = 2.40 + row * 1.44
        card(c, l, t, 5.78, 1.30, "", accent=TEAL)
        text(c, l + 0.26, t + 0.15, 5.30, f"CAVEAT {i+1}", font=MONO_B, size=8.6, color=MUTED)
        text(c, l + 0.26, t + 0.42, 5.30, head, font=SANS_B, size=11.4, color=INK)
        text(c, l + 0.26, t + 0.72, 5.30, body, size=10.2, color=BODY, leading=12.6)
    card(c, 6.83, 5.28, 5.78, 1.30, "", accent=AMBER)
    text(c, 7.09, 5.43, 5.30, "HOW TO READ THE OPTIMIZER SECTION", font=MONO_B, size=8.6, color=AMBER)
    text(c, 7.09, 5.72, 5.30, "Experiments 1-6 are the earlier campaign.", font=SANS_B, size=11.4,
         color=INK)
    text(c, 7.09, 6.02, 5.30, "They remain on the record for method and for the negative results. "
         "Experiment 7 supersedes their conclusion.", size=10.2, color=BODY, leading=12.6)


def p_audit(c, n):
    chrome(c, "EXPERIMENT 7", "Re-running the campaign after auditing the evaluator",
           "03 · THE OPTIMIZER", page=n, intro_w=11.4,
           intro="The earlier campaign concluded no controller could beat the simple rule. An audit "
                 "found three defects — all in the scoring code and the scenario generator, none in "
                 "the controllers themselves. Fixing them changed the answer.")
    cards = [("DEFECT 1 · UNITS", "The optimiser and the scoreboard disagreed",
              "The simulator scores overload as load / capacity - 1, a relative quantity. The safety "
              "guard and the pre-drain solver both worked in absolute Mbps. On a box cut to a tenth "
              "of its envelope those differ tenfold, so the controller undervalued exactly the action "
              "it exists to take."),
             ("DEFECT 2 · METRIC", "One stress family scored infinity",
              "An unavailable box has zero capacity, so those pairs scored infinity for both "
              "controllers — an exact tie that still failed a finite-metric check on all 160 "
              "configurations. That single check made the contract unpassable before any controller "
              "ran."),
             ("DEFECT 3 · SIZING", "The MPC horizon was set in windows, not hours",
              "Halving the control cadence quintupled the optimisation, so every 2-minute "
              "configuration hit a 2-second solver budget that the acceptance rules allowed to be "
              "120 seconds. The reported cadence finding was measuring a sizing bug.")]
    for i, (label, head, body) in enumerate(cards):
        l = M + i * 4.03
        card(c, l, 2.62, 3.83, 2.72, label, accent=TEAL)
        text(c, l + 0.26, 2.94, 3.33, head, font=SANS_B, size=12.6, color=INK, leading=15.5)
        text(c, l + 0.26, 3.56, 3.33, body, size=10.6, color=BODY, leading=13.2)
    rect(c, M, 5.62, W, 1.00, fill=SOFT)
    rect(c, M, 5.62, 0.04, 1.00, fill=TEAL)
    text(c, M + 0.26, 5.78, W - 0.52, "WHAT CHANGED AS A RESULT", font=MONO_B, size=9.5, color=TEAL)
    text(c, M + 0.26, 6.06, W - 0.52,
         "Configurations passing every gate: 0 of 160 to 36 of 160.    Worst stress family: -1.7% to "
         "+24.2%.    MPC solver timeouts: 40% at 10-minute cadence and 100% at 2-minute, now zero.",
         size=11.4, color=BODY, leading=14)


def band(c, label, body, *, t=5.86, accent=TEAL):
    """Bottom strip used to close a chart slide, matching the deck's callout."""
    rect(c, M, t, W, 0.80, fill=SOFT)
    rect(c, M, t, 0.04, 0.80, fill=accent)
    text(c, M + 0.26, t + 0.13, W - 0.52, label, font=MONO_B, size=9.5, color=accent)
    text(c, M + 0.26, t + 0.38, W - 0.52, body, size=10.8, color=BODY, leading=13.4)


def _notes(c, l, w, notes, top=2.52):
    t = top
    for head, body in notes:
        bullet(c, l, t, w, head, body)
        t += 1.12
        if t < 5.6: rect(c, l, t - 0.20, w, 0.012, fill=RULE)


def p_discovery(c, n):
    chrome(c, "EXPERIMENT 7 · DISCOVERY", "160 configurations, 20 000 paired simulated days",
           "03 · THE OPTIMIZER", page=n, intro_w=8.6,
           intro="Every configuration is one point. To pass it must clear a 10% minimum gain and add "
                 "no more than a quarter of the overload it removes — plus nine further checks.")
    image(c, "fig_frontier.png", M, 2.32, 7.60)
    _notes(c, 8.58, 4.03, [
        ("36 of 160 cleared every check",
         "against 0 of 160 in the earlier campaign, on the same simulator and the same 24-hour scenarios."),
        ("Pre-drain reaches further",
         "the strongest passing configuration removes 27.7% of overload while adding 9% of what it removes."),
        ("MPC sits low and left",
         "less gain, but collateral harm near zero — the conservative end of the same frontier.")])
    band(c, 'THE SHAPE OF THE CAMPAIGN',
         'Each configuration ran the same 125 paired days: five stress families x 25 seeds, one per CPU. 20 000 paired 24-hour simulations in total, 160 of 160 arms valid, zero non-finite results.')


def p_validation(c, n):
    chrome(c, "EXPERIMENT 7 · VALIDATION", "Frozen candidates, on seeds never inspected",
           "03 · THE OPTIMIZER", page=n, intro_w=8.6,
           intro="Configurations that look good on the data that chose them are not evidence. The four "
                 "leaders were frozen by a rule written down in advance — ranked on the lower "
                 "confidence bound, not the headline — then re-run on a separate seed pool.")
    image(c, "fig_shrink.png", M, 2.42, 7.60)
    _notes(c, 8.58, 4.03, [
        ("Three of four held every gate",
         "arm 143 fell just below the 10% bar at 9.8% and was not promoted."),
        ("The drop is 3-4 points, not a collapse",
         "that gap is the selection bias being removed. A candidate that fell apart here would have "
         "been a screening artifact; none did."),
        ("Seeds are provably disjoint",
         "from the screening pool, from every earlier campaign, and from the protected test set.")])
    band(c, 'WHY THE SECOND POOL EXISTS',
         '1 250 fresh paired days per candidate, 250 per stress family, drawn from seeds 81 000 and above — disjoint from the 80 000-80 124 screening pool, from every earlier campaign, and from the protected test set.')


def p_families(c, n):
    chrome(c, "WHERE IT WINS AND WHERE IT LOSES", "Three states, not two",
           "03 · THE OPTIMIZER", page=n, intro_w=11.4,
           intro="A claim that predictive steering is simply better would not survive scrutiny, and it "
                 "is not what the data says. Bars right of the line are overload removed; bars left "
                 "are overload the controller added. Both panels share one scale.")
    image(c, "fig_families.png", M, 2.30, 11.30)
    band = [("Declared loss — wins, never loses",
             "473 informative pairs, 0 regressions. Median case removes 72% (pre-drain) and 40% (MPC) "
             "of uplink overload.", GREEN),
            ("Pure surprise — bit-exact ties",
             "With no declared event the published policy is byte-for-byte identical to the simple "
             "rule. Verified, not assumed.", MUTED),
            ("Declared loss, then a surprise — can lose",
             "Pre-drain regresses in 74 of 243 pairs; MPC in 16 of 243. Still net positive, but this "
             "is the real exposure.", RED)]
    for i, (head, body, col) in enumerate(band):
        l = M + i * 4.03
        card(c, l, 5.76, 3.83, 0.92, "", accent=col)
        text(c, l + 0.24, 5.88, 3.35, head, font=SANS_B, size=11.0, color=INK)
        text(c, l + 0.24, 6.17, 3.35, body, size=9.9, color=BODY, leading=12.2)


def p_notice(c, n):
    chrome(c, "ADVANCE NOTICE", "The resource that actually matters",
           "03 · THE OPTIMIZER", page=n, intro_w=8.6,
           intro="Gain scales with how much warning the maintenance window carries. Notice is a "
                 "property of the scenario, drawn from its seed, so every configuration faced the "
                 "same schedule.")
    image(c, "fig_notice.png", M, 2.44, 7.30)
    _notes(c, 8.30, 4.31, [
        ("Four hours is worth 4x thirty minutes",
         "pre-drain removes 42.2% of overload with four hours' warning and 10.9% with thirty minutes."),
        ("MPC saturates around 24%",
         "its two-hour planning horizon cannot exploit three or four hours of notice; pre-drain keeps "
         "climbing."),
        ("This is a scheduling lever, not a model",
         "the single cheapest way to increase the benefit is to announce maintenance earlier.")])
    band(c, 'WHAT THIS MEANS OPERATIONALLY',
         'Announcing a maintenance window three hours ahead instead of thirty minutes roughly triples the overload the controller can remove. That is a change to scheduling practice, not to any model.')


def p_cadence(c, n):
    cd = DATA["cadence"]
    chrome(c, "CADENCE", "Two minutes buys churn, not benefit", "03 · THE OPTIMIZER", page=n,
           intro_w=11.4,
           intro="Running the controller every 2 minutes instead of every 10 was tested across all 160 "
                 "configurations, with the optimisation held to the same size so cadence trades "
                 "lookahead against reactivity rather than against solver feasibility.")
    image(c, "fig_cadence.png", M, 2.28, 10.10)
    rows = [("Pre-drain", "10 min", cd["predrain_10"]), ("Pre-drain", "2 min", cd["predrain_2"]),
            ("Cohort MPC", "10 min", cd["mpc_10"]), ("Cohort MPC", "2 min", cd["mpc_2"])]
    text(c, 10.98, 2.42, 1.63, "PASSED", font=MONO_B, size=8.2, color=MUTED)
    for i, (name, cad, v) in enumerate(rows):
        y = 2.72 + i * 0.52
        text(c, 10.98, y, 1.63, f"{name} · {cad}", font=MONO, size=8.6, color=MUTED)
        text(c, 10.98, y + 0.19, 1.63, f"{v['passing']} / {v['n']}", font=SERIF, size=15,
             color=GREEN if v["passing"] else RED)
    rect(c, M, 5.74, W, 0.88, fill=SOFT)
    rect(c, M, 5.74, 0.04, 0.88, fill=AMBER)
    text(c, M + 0.26, 5.88, W - 0.52, "THE SAME CONFIGURATION, RUN AT BOTH CADENCES",
         font=MONO_B, size=9.5, color=AMBER)
    text(c, M + 0.26, 6.16, W - 0.52,
         "Arm 60 at 10 minutes removes 27.67% of overload with 0.192 churn and passes every check. "
         "Arm 124 — identical apart from cadence — removes 27.69% with 0.702 churn and fails on churn "
         "alone. Faster control bought two hundredths of a point for 3.7x the routing change.",
         size=11.2, color=BODY, leading=14)


# insertion index in the FINAL document -> page builder
NEW = [(6, p_headline), (7, p_caveats), (56, p_audit), (57, p_discovery),
       (58, p_validation), (59, p_families), (60, p_notice), (61, p_cadence)]


def render_new(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=(PW, PH))
    for idx, fn in NEW:
        fn(c, idx + 1)
        c.showPage()
    c.save()
    return path


def restamp(page, number: int, overlay_dir: Path):
    """Cover the original page number and print the new one."""
    stamp = overlay_dir / f"stamp-{number}.pdf"
    c = canvas.Canvas(str(stamp), pagesize=(PW, PH))
    c.setFillColor(BG)
    c.rect(X(11.62), Y(7.32), X(1.05), X(0.30), fill=1, stroke=0)
    c.setFont(MONO, 8.5); c.setFillColor(FAINT)
    c.drawRightString(X(M + W), PH - (7.04 * PT + 8.0), f"{number:02d}")
    c.save()
    page.merge_page(PdfReader(str(stamp)).pages[0])
    return page


def main() -> int:
    out_dir = FIG / "pdfparts"; out_dir.mkdir(parents=True, exist_ok=True)
    new_pdf = render_new(out_dir / "v4-new-pages.pdf")
    new_pages = PdfReader(str(new_pdf)).pages
    old_pages = list(PdfReader(str(PPTX_PDF)).pages)
    assert len(old_pages) == 61, f"expected 61 original pages, found {len(old_pages)}"

    writer = PdfWriter()
    inserts = {idx: new_pages[i] for i, (idx, _) in enumerate(NEW)}
    src = iter(old_pages)
    for out_idx in range(len(old_pages) + len(NEW)):
        writer.add_page(inserts[out_idx] if out_idx in inserts else next(src))

    for i, page in enumerate(writer.pages):
        if i == 0:
            continue                       # the title slide carries no page number
        restamp(page, i + 1, out_dir)

    with open(PPTX_PDF, "wb") as fh:
        writer.write(fh)
    print(f"PDF pages: {len(old_pages)} -> {len(writer.pages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
