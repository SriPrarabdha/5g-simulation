#!/usr/bin/env python3
"""Build the dependency-free SVG evidence figures for the C-DOT experiment report.

The source of truth is the compact, post-campaign analysis JSON.  The renderer
uses only the Python standard library so the report can be rebuilt on a compute
node without installing plotting packages.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "presentation" / "cdot-experiment-report-v2"
FIG = OUT / "figures"
DATA = OUT / "data"
ANALYSIS = ROOT / "output" / "mixed-stress-discovery-v3-analysis-v2.json"

BG = "#07111f"
PANEL = "#0d1b2d"
PANEL_2 = "#10243a"
INK = "#ecf5ff"
MUTED = "#9ab0c8"
GRID = "#243a52"
CYAN = "#24d3c1"
BLUE = "#5aa9ff"
AMBER = "#ffbd59"
CORAL = "#ff6b6b"
GREEN = "#4ade80"
PURPLE = "#b48cff"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


class SVG:
    def __init__(self, width: int = 1600, height: int = 900, title: str = "") -> None:
        self.width = width
        self.height = height
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
            f"<title>{esc(title)}</title>",
            "<defs>",
            '<filter id="shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="10" stdDeviation="16" flood-color="#000" flood-opacity=".32"/></filter>',
            '<linearGradient id="cyanBlue" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#24d3c1"/><stop offset="1" stop-color="#5aa9ff"/></linearGradient>',
            '<linearGradient id="amberCoral" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffbd59"/><stop offset="1" stop-color="#ff6b6b"/></linearGradient>',
            '<style>text{font-family:Inter,Segoe UI,Arial,sans-serif}.smallcaps{letter-spacing:2.4px;font-weight:700}</style>',
            "</defs>",
            f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        ]

    def raw(self, value: str) -> None:
        self.parts.append(value)

    def rect(self, x: float, y: float, w: float, h: float, fill: str, radius: float = 0,
             stroke: str = "none", sw: float = 1, opacity: float = 1, shadow: bool = False) -> None:
        filt = ' filter="url(#shadow)"' if shadow else ""
        self.parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{radius:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"{filt}/>'
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, stroke: str = GRID,
             sw: float = 1, dash: str | None = None, opacity: float = 1) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"{d}/>'
        )

    def circle(self, cx: float, cy: float, r: float, fill: str, stroke: str = "none",
               sw: float = 1, opacity: float = 1) -> None:
        self.parts.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"/>'
        )

    def text(self, x: float, y: float, value: object, size: float = 24, fill: str = INK,
             weight: int = 400, anchor: str = "start", opacity: float = 1,
             css: str = "") -> None:
        cls = f' class="{css}"' if css else ""
        self.parts.append(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" fill="{fill}" font-weight="{weight}" text-anchor="{anchor}" opacity="{opacity}"{cls}>{esc(value)}</text>'
        )

    def multiline(self, x: float, y: float, lines: list[str], size: float = 24,
                  fill: str = INK, weight: int = 400, gap: float = 1.25,
                  anchor: str = "start") -> None:
        self.parts.append(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">'
        )
        for idx, value in enumerate(lines):
            dy = 0 if idx == 0 else size * gap
            self.parts.append(f'<tspan x="{x:.2f}" dy="{dy:.2f}">{esc(value)}</tspan>')
        self.parts.append("</text>")

    def finish(self, path: Path) -> None:
        path.write_text("\n".join(self.parts + ["</svg>"]) + "\n", encoding="utf-8")


def title(s: SVG, kicker: str, headline: str, subhead: str) -> None:
    s.text(72, 66, kicker.upper(), 17, CYAN, 700, css="smallcaps")
    s.text(72, 119, headline, 40, INK, 750)
    s.text(72, 158, subhead, 20, MUTED, 400)


def fmt_pct(value: float, digits: int = 1, signed: bool = False) -> str:
    p = value * 100
    return f"{p:+.{digits}f}%" if signed else f"{p:.{digits}f}%"


def arm_map(data: dict) -> dict[int, dict]:
    return {int(a["arm"]["index"]): a for a in data["arms"]}


def cadence_summary(data: dict, controller: str) -> dict:
    groups: dict[tuple, dict[int, dict]] = {}
    for a in data["arms"]:
        p = a["arm"]
        if p["controller"] != controller:
            continue
        key = (p["horizon_hours"], p["maximum_blend"], p["destination_reserve"], p["surprise_capacity_factor"])
        groups.setdefault(key, {})[p["cadence_minutes"]] = a
    pairs = [v for v in groups.values() if 10 in v and 2 in v]
    fields = [
        "mean_pair_ul_gain", "bootstrap_95pct_lower", "severity_weighted_ul_gain",
        "worst_pair_gain", "churn_l1_per_group_hour", "latency_max_ms",
        "latency_fraction_within_500ms",
    ]
    out = {"matched_profiles": len(pairs), "cadence": {}}
    for cadence in (10, 2):
        out["cadence"][str(cadence)] = {f: mean(p[cadence][f] for p in pairs) for f in fields}
        out["cadence"][str(cadence)]["families"] = {
            fam: mean(p[cadence]["family"][fam]["aggregate_gain"] for p in pairs)
            for fam in pairs[0][cadence]["family"]
        }
    out["two_min_better_mean_count"] = sum(
        p[2]["mean_pair_ul_gain"] > p[10]["mean_pair_ul_gain"] for p in pairs
    )
    return out


def campaign_summary(data: dict) -> dict:
    gates = list(data["arms"][0]["gates"])
    selected = [3, 4, 60, 116, 132, 143, 144]
    amap = arm_map(data)
    return {
        "arm_count": data["arm_count"],
        "pair_count": data["pair_count"],
        "passing_arm_count": data["passing_arm_count"],
        "work_fingerprint": data["work_fingerprint"],
        "input_inventory_sha256": data["input_inventory_sha256"],
        "gate_pass_counts": {g: sum(bool(a["gates"][g]) for a in data["arms"]) for g in gates},
        "selected_arms": {str(i): amap[i] for i in selected},
        "cadence": {
            "predrain": cadence_summary(data, "predrain"),
            "mpc": cadence_summary(data, "mpc"),
        },
    }


def plot_verdict(data: dict) -> None:
    s = SVG(title="The 160-arm mixed-stress verdict")
    title(s, "20,000 paired simulations", "A real mechanism win — but no promotable controller",
          "Every configuration was paired with Static across five stress families and evaluated against 13 frozen gates.")
    cards = [
        ("160", "candidate arms", BLUE),
        ("20,000", "paired evaluations", CYAN),
        ("5", "stress families", PURPLE),
        ("0 / 160", "passed every gate", CORAL),
    ]
    for i, (big, label, color) in enumerate(cards):
        x = 72 + i * 370
        s.rect(x, 205, 330, 144, PANEL, 18, GRID, 1, shadow=True)
        s.text(x + 28, 270, big, 43, color, 800)
        s.text(x + 28, 313, label, 19, MUTED, 500)

    gate_order = [
        ("pure_surprise_exact_static", "Pure surprises exactly Static"),
        ("no_invalid_capacity_slack", "No invalid capacity slack"),
        ("decision_latency_within_120s", "Decision latency ≤120 s"),
        ("churn_within_0_30_l1_per_group_hour", "Churn ≤0.30 / group-hour"),
        ("bootstrap_95pct_lower_above_zero", "95% lower bound >0"),
        ("severity_weighted_ul_gain_positive", "Severity-weighted gain >0"),
        ("no_family_aggregate_ul_regression", "No family regression"),
        ("worst_pair_above_minus_10pct", "Worst pair >−10%"),
        ("mean_pair_ul_gain_at_least_10pct", "Mean gain ≥10%"),
        ("all_overload_metrics_finite", "All overload metrics finite"),
    ]
    s.text(72, 408, "GATE SURVIVAL", 16, MUTED, 700, css="smallcaps")
    x0, x1 = 590, 1512
    for i, (key, label) in enumerate(gate_order):
        y = 452 + i * 39
        count = sum(bool(a["gates"][key]) for a in data["arms"])
        frac = count / data["arm_count"]
        color = GREEN if frac == 1 else (AMBER if frac >= .7 else CORAL)
        s.text(72, y + 6, label, 17, INK, 500)
        s.rect(x0, y - 13, x1 - x0, 18, PANEL_2, 9)
        s.rect(x0, y - 13, (x1 - x0) * frac, 18, color, 9)
        s.text(1530, y + 5, f"{count}/160", 16, color, 700, anchor="end")
    s.text(72, 865, "Decision: retain Static.  The discovery result is a mechanism map, not a release claim.", 21, INK, 650)
    s.finish(FIG / "01_campaign_verdict.svg")


def plot_gain_risk(data: dict) -> None:
    s = SVG(title="Mean gain versus worst-pair tail risk")
    title(s, "Benefit × reliability", "Headline gain and tail safety pull in opposite directions",
          "Upper-right is the frozen promotion region. No one of the 160 arms enters it.")
    left, top, width, height = 145, 235, 1320, 535
    xmin, xmax, ymin, ymax = -1.0, 15.5, -160.0, 5.0
    def px(x: float) -> float: return left + (x - xmin) / (xmax - xmin) * width
    def py(y: float) -> float: return top + height - (y - ymin) / (ymax - ymin) * height

    # Target quadrant and axes.
    s.rect(px(10), py(5), px(15.5) - px(10), py(-10) - py(5), "#17382e", 0, opacity=.72)
    s.text(px(12.75), py(-4.1), "PROMOTION REGION", 15, GREEN, 700, anchor="middle", css="smallcaps")
    for x in [0, 5, 10, 15]:
        s.line(px(x), top, px(x), top + height, GRID, 1)
        s.text(px(x), top + height + 34, f"{x}%", 16, MUTED, 500, anchor="middle")
    for y in [-150, -100, -50, -10, 0]:
        s.line(left, py(y), left + width, py(y), GRID, 1, dash="5 8" if y == -10 else None)
        s.text(left - 18, py(y) + 6, f"{y}%", 16, MUTED, 500, anchor="end")
    s.line(px(10), top, px(10), top + height, AMBER, 2, "7 7")
    s.line(left, py(-10), left + width, py(-10), AMBER, 2, "7 7")

    styles = {
        ("predrain", 10): (CYAN, "●"), ("predrain", 2): (BLUE, "●"),
        ("mpc", 10): (AMBER, "●"), ("mpc", 2): (PURPLE, "●"),
    }
    for a in data["arms"]:
        p = a["arm"]
        x = 100 * a["mean_pair_ul_gain"]
        y = max(ymin, 100 * a["worst_pair_gain"])
        color, _ = styles[(p["controller"], p["cadence_minutes"])]
        s.circle(px(x), py(y), 5.2 if p["controller"] == "predrain" else 6.4, color, BG, 1, .72)

    labels = {3: (34, -26), 4: (28, -28), 60: (-82, -18), 116: (16, 32), 132: (22, -24), 143: (25, -25)}
    amap = arm_map(data)
    for idx, (dx, dy) in labels.items():
        a = amap[idx]
        x, y = 100 * a["mean_pair_ul_gain"], max(ymin, 100 * a["worst_pair_gain"])
        cx, cy = px(x), py(y)
        s.circle(cx, cy, 9, BG, INK, 2)
        s.circle(cx, cy, 5.5, CORAL if idx in (60, 116) else GREEN if idx == 3 else INK)
        s.line(cx, cy, cx + dx * .75, cy + dy * .75, MUTED, 1)
        s.text(cx + dx, cy + dy, f"arm {idx}", 15, INK, 700, anchor="middle")

    s.text(left + width / 2, 842, "Mean paired UL overload-area improvement →", 20, INK, 600, anchor="middle")
    s.raw(f'<text x="34" y="{top + height/2:.2f}" font-size="20" fill="{INK}" font-weight="600" text-anchor="middle" transform="rotate(-90 34 {top + height/2:.2f})">Worst individual pair improvement →</text>')
    legend = [("Pre-drain · 10 min", CYAN), ("Pre-drain · 2 min", BLUE), ("MPC · 10 min", AMBER), ("MPC · 2 min", PURPLE)]
    for i, (lab, color) in enumerate(legend):
        x = 760 + i * 190
        s.circle(x, 196, 6, color)
        s.text(x + 14, 202, lab, 14, MUTED, 600)
    s.finish(FIG / "02_gain_vs_tail_risk.svg")


def heat_color(value: float) -> str:
    # value is percentage points, range displayed around [-3, 85].
    if value < 0:
        intensity = min(1, abs(value) / 3)
        return f"rgb({int(110 + 120*intensity)},{int(43 + 32*(1-intensity))},{int(58 + 30*(1-intensity))})"
    intensity = min(1, value / 80)
    return f"rgb({int(18 + 18*(1-intensity))},{int(55 + 115*intensity)},{int(70 + 70*(1-intensity))})"


def plot_family_heatmap(data: dict) -> None:
    s = SVG(title="Selected controller outcomes by stress family")
    title(s, "Stress-family transfer", "Pre-drain wins on declared events; independent outages expose the tail",
          "Cells show aggregate UL overload-area improvement versus paired Static. Zero means exact fallback or no measurable change.")
    amap = arm_map(data)
    rows = [3, 4, 60, 116, 132, 143, 144]
    families = [
        ("declared_maintenance", ["Declared", "maintenance"]),
        ("maintenance_then_stadium", ["Maintenance +", "surprise stadium"]),
        ("maintenance_then_outage", ["Maintenance +", "surprise outage"]),
        ("surprise_demand", ["Pure surprise", "demand"]),
        ("surprise_outage", ["Pure surprise", "outage"]),
    ]
    x0, y0, cw, rh = 555, 265, 190, 72
    for j, (_, lines) in enumerate(families):
        s.multiline(x0 + j*cw + cw/2, 208, lines, 16, MUTED, 650, 1.1, "middle")
    for i, idx in enumerate(rows):
        a = amap[idx]; p = a["arm"]; y = y0 + i*rh
        label = f"arm {idx} · {p['controller']} · {p['cadence_minutes']}m · {int(p['maximum_blend']*100)}%"
        s.text(72, y + 36, label, 18, INK, 600)
        for j, (fam, _) in enumerate(families):
            v = 100*a["family"][fam]["aggregate_gain"]
            fill = heat_color(v)
            s.rect(x0 + j*cw, y, cw-8, rh-8, fill, 10, BG, 2)
            color = "#fff" if abs(v) > .02 else MUTED
            s.text(x0 + j*cw + (cw-8)/2, y + 39, f"{v:+.1f}%", 20, color, 750, anchor="middle")
    s.rect(72, 797, 1428, 60, PANEL, 14, GRID, 1)
    s.text(96, 834, "Key readout", 17, CYAN, 750)
    s.text(230, 834, "The guard guarantees exact Static when no event is declared, but cannot undo persistent sessions committed before an independent surprise outage.", 17, INK, 500)
    s.finish(FIG / "03_family_heatmap.svg")


def plot_cadence(data: dict) -> None:
    s = SVG(title="Two-minute versus ten-minute controller cadence")
    title(s, "Matched cadence experiment", "2-minute pre-drain is statistically neutral — and 3.6× more churn-heavy",
          "The observation window stays fixed at 10 minutes; each comparison matches blend, horizon, reserve and surprise envelope.")
    pre = cadence_summary(data, "predrain")
    mpc = cadence_summary(data, "mpc")
    ten, two = pre["cadence"]["10"], pre["cadence"]["2"]
    cards = [
        ("Mean UL gain", 100*ten["mean_pair_ul_gain"], 100*two["mean_pair_ul_gain"], "%"),
        ("95% lower bound", 100*ten["bootstrap_95pct_lower"], 100*two["bootstrap_95pct_lower"], "%"),
        ("Churn / group-hour", ten["churn_l1_per_group_hour"], two["churn_l1_per_group_hour"], ""),
        ("≤500 ms decisions", 100*ten["latency_fraction_within_500ms"], 100*two["latency_fraction_within_500ms"], "%"),
    ]
    for i, (label, a, b, suffix) in enumerate(cards):
        x = 72 + (i%2)*740; y = 225 + (i//2)*240
        s.rect(x, y, 700, 200, PANEL, 18, GRID, 1, shadow=True)
        s.text(x+28, y+39, label, 18, MUTED, 650)
        if suffix:
            av, bv = f"{a:.2f}{suffix}", f"{b:.2f}{suffix}"
        else:
            av, bv = f"{a:.3f}", f"{b:.3f}"
        s.text(x+28, y+104, av, 39, CYAN, 800)
        s.text(x+355, y+104, bv, 39, BLUE, 800)
        s.text(x+28, y+143, "10-minute", 15, CYAN, 650)
        s.text(x+355, y+143, "2-minute", 15, BLUE, 650)
        delta = b-a
        if label.startswith("Churn"):
            summary = f"{b/a:.2f}× higher at 2 minutes"
            color = CORAL
        else:
            summary = f"Δ {delta:+.3f}{suffix}"
            color = GREEN if delta > 0 and label != "≤500 ms decisions" else MUTED
        s.text(x+665, y+173, summary, 16, color, 700, anchor="end")

    s.rect(72, 725, 700, 105, PANEL_2, 16)
    s.text(98, 761, "Pre-drain matched profiles", 16, MUTED, 650)
    s.text(98, 803, f"{pre['two_min_better_mean_count']} / {pre['matched_profiles']}", 31, INK, 800)
    s.text(290, 803, "had higher mean gain at 2 min", 17, MUTED, 500)
    s.rect(812, 725, 700, 105, PANEL_2, 16)
    s.text(838, 761, "MPC matched profiles", 16, MUTED, 650)
    s.text(838, 803, "2.90% → 0.00%", 31, CORAL, 800)
    s.text(1118, 803, f"({mpc['matched_profiles']} profiles; event solves timed out)", 17, MUTED, 500)
    s.finish(FIG / "04_cadence_comparison.svg")


def plot_funnel(data: dict) -> None:
    s = SVG(title="Guard action and fallback funnels")
    title(s, "Causal exposure guard", "Most decision epochs deliberately publish Static",
          "Counts cover all 125 paired scenarios per arm. Narrow execution is a safety feature; timeout fallback is not.")
    amap = arm_map(data)
    rows = [
        (4, "Balanced pre-drain", CYAN),
        (60, "Aggressive pre-drain", BLUE),
        (143, "10m MPC", AMBER),
        (144, "2m MPC", PURPLE),
    ]
    x0, y0, w, rh = 370, 260, 1090, 128
    for i, (idx, label, color) in enumerate(rows):
        a=amap[idx]; f=a["decision_funnel"]; y=y0+i*rh
        requested=f.get("requested",0); proposed=f.get("proposed",0)
        executed=f.get("executed",0)
        timeout=f.get("solver:timeout",0)
        guard_reject=sum(v for k,v in f.items() if k.startswith("rejected:exposure_guard"))
        s.text(72, y+30, label, 19, INK, 700)
        s.text(72, y+58, f"arm {idx} · {requested:,} epochs", 15, MUTED, 500)
        # Scale segment values against requested; render at least 3px for visible non-zero values.
        segments=[("No declared event", max(0, requested-proposed-timeout), PANEL_2),
                  ("Guard rejected", guard_reject, CORAL),
                  ("Executed", executed, color),
                  ("Timeout", timeout, PURPLE)]
        cursor=x0
        for _,value,fill in segments:
            if value<=0: continue
            sw=max(3,w*value/requested)
            s.rect(cursor,y,sw,rh-52,fill,8 if cursor==x0 else 0)
            cursor+=sw
        s.text(x0, y+102, f"proposed {proposed:,}", 14, MUTED, 600)
        s.text(x0+260, y+102, f"guard rejected {guard_reject:,}", 14, CORAL, 600)
        s.text(x0+560, y+102, f"executed {executed:,}", 14, color, 700)
        s.text(x0+w, y+102, f"timeouts {timeout:,}", 14, PURPLE if timeout else MUTED, 700, anchor="end")
    s.rect(72, 783, 1440, 57, PANEL, 14)
    s.text(96, 819, "Safety invariant", 16, GREEN, 750)
    s.text(250, 819, "All 160 arms returned exact Static throughout both pure-surprise families when no future event was declared.", 17, INK, 550)
    s.finish(FIG / "05_guard_action_funnel.svg")


def plot_journey() -> None:
    s = SVG(title="Scientific experiment journey")
    title(s, "Evidence, not a single benchmark", "What each experiment taught us",
          "Positive mechanism results, negative selection results and invalidated runs are kept distinct.")
    stages = [
        ("1", "Digital twin", "PASS", "Accounting closes exactly; causal 30 s simulation; bounded streaming memory.", GREEN),
        ("2", "Forecasting", "NO PROMOTION", "Best challengers improved WAPE ≈11.6%, below the frozen 15% gate.", AMBER),
        ("3", "Survival", "PASS", "Distribution-blind calibration converges; stale telemetry falls back exactly.", GREEN),
        ("4", "Oracle bounds", "HEADROOM", "Perfect causal arrivals recover ≈55%; fault knowledge is the dominant axis.", BLUE),
        ("5", "MPC", "REJECT", "Development gains did not transfer; 128-seed production result was −13.3%.", CORAL),
        ("6", "Pre-drain", "MECHANISM WIN", "Large declared-maintenance gains, but mixed-stress tail losses remain.", CYAN),
        ("7", "Exposure guard", "REJECT", "20,000 pairs: 0/160 full-gate passes; exact surprise fallback proven.", CORAL),
        ("8", "2-minute cadence", "NO BENEFIT", "+0.08 pp mean gain for pre-drain, 3.6× churn; MPC timed out.", PURPLE),
    ]
    x0, y0, cw, ch = 72, 218, 704, 137
    for i,(num,name,status,desc,color) in enumerate(stages):
        col=i%2; row=i//2; x=x0+col*752; y=y0+row*153
        s.rect(x,y,cw,ch,PANEL,17,GRID,1,shadow=True)
        s.circle(x+45,y+43,23,color)
        s.text(x+45,y+51,num,20,BG,800,anchor="middle")
        s.text(x+84,y+38,name,22,INK,750)
        s.text(x+cw-24,y+38,status,14,color,800,anchor="end",css="smallcaps")
        s.multiline(x+84,y+76,[desc[:72],desc[72:] if len(desc)>72 else ""],16,MUTED,500,1.25)
    s.text(800, 858, "CURRENT PRODUCTION DECISION  ·  STATIC", 20, INK, 800, anchor="middle", css="smallcaps")
    s.finish(FIG / "06_experiment_journey.svg")


def write_index(summary: dict) -> None:
    figures = [
        ("01_campaign_verdict.svg", "Campaign verdict"),
        ("02_gain_vs_tail_risk.svg", "Benefit × tail risk"),
        ("03_family_heatmap.svg", "Stress-family transfer"),
        ("04_cadence_comparison.svg", "Cadence comparison"),
        ("05_guard_action_funnel.svg", "Guard action funnel"),
        ("06_experiment_journey.svg", "Experiment journey"),
    ]
    cards = "\n".join(
        f'<section><h2>{esc(label)}</h2><img src="figures/{esc(file)}" alt="{esc(label)}"></section>'
        for file,label in figures
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>C-DOT optimizer experiment evidence</title>
<style>
body{{margin:0;background:{BG};color:{INK};font-family:Inter,Segoe UI,Arial,sans-serif}}main{{max-width:1500px;margin:auto;padding:48px 28px 80px}}
h1{{font-size:clamp(32px,5vw,64px);margin:0 0 12px}}p{{color:{MUTED};font-size:20px;line-height:1.55;max-width:1050px}}section{{margin:48px 0 72px}}h2{{font-size:24px;margin:0 0 18px}}img{{width:100%;height:auto;border-radius:18px;box-shadow:0 18px 45px #0008}}a{{color:{CYAN}}}.pill{{display:inline-block;color:{CYAN};letter-spacing:2px;font-weight:800;font-size:14px;margin-bottom:18px}}
</style></head><body><main><span class="pill">C-DOT · SYNTHETIC SHADOW-CONTROLLER EVIDENCE</span>
<h1>The experiments found the opportunity — and the safety boundary.</h1>
<p>20,000 paired mixed-stress evaluations confirm strong declared-maintenance gains, exact Static fallback for pure surprises, and no configuration that clears the complete release gate. The detailed audit is in <a href="REPORT.md">REPORT.md</a>.</p>
{cards}</main></body></html>"""
    (OUT / "index.html").write_text(page, encoding="utf-8")


def write_manifest() -> None:
    paths = sorted(p for p in OUT.rglob("*") if p.is_file() and p.name != "artifact-manifest.json")
    sources = [Path(__file__).resolve(), ANALYSIS]
    manifest = {
        "schema_version": "cdot-experiment-report-manifest/1.0",
        "sources": [
            {"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size,
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sources
        ],
        "artifacts": [
            {"path": str(p.relative_to(OUT)), "bytes": p.stat().st_size,
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in paths
        ],
    }
    (OUT / "artifact-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n", encoding="utf-8")


def main() -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    data = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    summary = campaign_summary(data)
    (DATA / "mixed-stress-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    plot_verdict(data)
    plot_gain_risk(data)
    plot_family_heatmap(data)
    plot_cadence(data)
    plot_funnel(data)
    plot_journey()
    write_index(summary)
    write_manifest()
    print(f"wrote {len(list(FIG.glob('*.svg')))} SVG figures to {FIG}")


if __name__ == "__main__":
    main()
