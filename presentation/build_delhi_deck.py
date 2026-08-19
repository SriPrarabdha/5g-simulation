#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import math
import os
import sys
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("MPLCONFIGDIR", "/tmp/cdot-delhi-mpl")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "presentation/delhi"
W, H = 1600, 900
NAVY = "#0B1F33"; INK = "#172B3A"; BLUE = "#1877F2"; CYAN = "#00A6A6"
GOLD = "#F4B400"; CORAL = "#F45B69"; GREEN = "#22A06B"; MUTED = "#66788A"
PALE = "#F2F6FA"; WHITE = "#FFFFFF"; GRID = "#D9E2EC"; PURPLE = "#7457D5"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_MONO if mono else (FONT_BOLD if bold else FONT_REG)
    return ImageFont.truetype(path, size)


def pct(value: float, digits: int = 2) -> str:
    return f"{value * 100:.{digits}f}%"


def read_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "presentation-evidence-manifest/1.0":
        raise ValueError("unsupported presentation evidence manifest")
    for key, source in manifest["sources"].items():
        source_path = Path(source["path"])
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if digest != source["sha256"]:
            raise ValueError(f"source hash mismatch: {key}")
    return manifest


def _chart_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 14,
        "axes.titleweight": "bold", "axes.edgecolor": GRID, "axes.labelcolor": INK,
        "xtick.color": MUTED, "ytick.color": MUTED, "grid.color": GRID,
        "figure.facecolor": "white", "axes.facecolor": "white", "legend.frameon": False,
    })


def charts(manifest: dict[str, Any], directory: Path) -> dict[str, Path]:
    _chart_style(); directory.mkdir(parents=True, exist_ok=True)
    output: dict[str, Path] = {}
    realism = manifest["display"]["realism"]

    fidelity = realism["distribution_fidelity"]
    fig, (left, right) = plt.subplots(1, 2, figsize=(12, 4.8))
    bins = fidelity["configured_rate_bins"]
    left.scatter([item["ul_mbps"] for item in bins], [item["dl_mbps"] for item in bins],
                 s=90, c=np.arange(16), cmap="viridis", edgecolor="white")
    left.set(xlabel="UL Mbps/session", ylabel="DL Mbps/session", title=f"{len(bins)} bounded joint rate bins")
    left.grid(True, alpha=.5)
    ccdf = fidelity["holding_time"]["sample_ccdf"]
    right.step([item["steps"] * .5 for item in ccdf], [item["ccdf"] for item in ccdf], color=BLUE, lw=2.5)
    right.set_yscale("log"); right.set(xlabel="Holding time (minutes)", ylabel="CCDF",
                                       title=f"Bounded {fidelity['holding_time']['distribution']} holding time")
    right.grid(True, which="both", alpha=.5)
    fig.tight_layout(); output["distribution"] = directory / "01_distribution_fidelity.png"
    fig.savefig(output["distribution"], dpi=180, bbox_inches="tight"); plt.close(fig)

    fingerprint = realism["traffic_fingerprint"]
    services = fingerprint["services"]
    matrix = np.zeros((len(services), 24))
    for item in fingerprint["service_by_hour"]:
        matrix[services.index(item["service"]), item["hour"]] = item["arrivals"]
    fig = plt.figure(figsize=(14, 7.2)); grid = fig.add_gridspec(2, 3, hspace=.48, wspace=.34)
    ax = fig.add_subplot(grid[0, :2]); image = ax.imshow(matrix, aspect="auto", cmap="YlGnBu")
    ax.set_yticks(range(len(services)), services); ax.set_xticks(range(0, 24, 2))
    ax.set(xlabel="Hour", title="Service-by-hour arrivals"); fig.colorbar(image, ax=ax, label="New sessions")
    profile_ax = fig.add_subplot(grid[0, 2]); hours = list(range(24))
    profile_ax.plot(hours, fingerprint["weekday_profile"], color=BLUE, label="weekday")
    profile_ax.plot(hours, fingerprint["weekend_profile"], color=GOLD, label="weekend")
    profile_ax.set(title="Day-type profiles", xlabel="Hour", ylabel="Relative demand"); profile_ax.legend(); profile_ax.grid(True, alpha=.35)
    acf_ax = fig.add_subplot(grid[1, 0]); phi = fingerprint["configured_ar1"]
    lags = np.arange(13); acf_ax.stem(lags, phi ** lags, linefmt=BLUE, markerfmt="o", basefmt=" ")
    acf_ax.set(title=f"Configured AR(1) ACF (φ={phi:.2f})", xlabel="Lag", ylabel="Correlation"); acf_ax.grid(True, alpha=.35)
    mix_ax = fig.add_subplot(grid[1, 1]); mix = fingerprint["class_mix"]
    mix_ax.bar(list(mix), list(mix.values()), color=CYAN); mix_ax.tick_params(axis="x", rotation=35)
    mix_ax.set(title="Class mix", ylabel="New sessions")
    mobility_ax = fig.add_subplot(grid[1, 2]); trajectories = realism["population"]["trajectories"]
    for zone in sorted(trajectories[0]["by_zone"]):
        mobility_ax.plot([item["step"] for item in trajectories], [item["by_zone"][zone]/1e6 for item in trajectories], marker="o", label=zone)
    mobility_ax.set(title="Conserving mobility flows", xlabel="Step", ylabel="Population (M)")
    mobility_ax.legend(fontsize=6, ncol=2); mobility_ax.grid(True, alpha=.35)
    fig.suptitle("Population and traffic fingerprint", fontweight="bold", fontsize=17); fig.tight_layout()
    output["fingerprint"] = directory / "02_traffic_fingerprint.png"
    fig.savefig(output["fingerprint"], dpi=180, bbox_inches="tight"); plt.close(fig)

    fig, axes = plt.subplots(3, 2, figsize=(13, 7), sharex=True, gridspec_kw={"width_ratios":[2.1,1]})
    for row_index, (upf_id, rows) in enumerate(realism["representative_upfs"].items()):
        ax = axes[row_index, 0]; state_ax = axes[row_index, 1]
        hours = [item["step"] * manifest["display"]["scale"]["step_seconds"] / 3600 for item in rows]
        ax.plot(hours, [item["offered_ul_mbps"] for item in rows], color=BLUE, lw=1, label="offered UL")
        ax.plot(hours, [item["carried_ul_mbps"] for item in rows], color=CYAN, lw=1, label="carried UL")
        ax.plot(hours, [item["safe_ul_mbps"] for item in rows], color=CORAL, lw=1.5, ls="--", label="safe capacity")
        ax.set_ylabel(upf_id.replace("upf-01-", "").title()); ax.grid(True, alpha=.35)
        state_ax.plot(hours, [item["active_sessions"] for item in rows], color=PURPLE, lw=1, label="active sessions")
        drops = np.asarray([item["dropped_ul_bytes"] for item in rows]); queues = np.asarray([item["queued_ul_bytes"] for item in rows])
        if drops.max(initial=0) > 0: state_ax.fill_between(hours, 0, drops/drops.max(), color=CORAL, alpha=.25, transform=state_ax.get_xaxis_transform(), label="drop event")
        if queues.max(initial=0) > 0: state_ax.fill_between(hours, 0, queues/queues.max(), color=GOLD, alpha=.18, transform=state_ax.get_xaxis_transform(), label="queue event")
        for index, item in enumerate(rows):
            if item["health"] not in {"healthy", "degraded"}: state_ax.axvspan(hours[index], hours[min(index+1,len(hours)-1)], color=CORAL, alpha=.2)
        state_ax.grid(True, alpha=.35)
    axes[0,0].legend(ncol=3, loc="upper left", fontsize=8); axes[0,1].legend(loc="upper left", fontsize=8)
    axes[-1,0].set_xlabel("Simulated hour"); axes[-1,1].set_xlabel("Simulated hour")
    fig.suptitle("Representative edge / regional / central UPF day", fontweight="bold")
    fig.tight_layout(); output["upf_day"] = directory / "03_representative_upf_day.png"
    fig.savefig(output["upf_day"], dpi=180, bbox_inches="tight"); plt.close(fig)

    totals = realism["accounting"]["totals"]["ul"]
    values = [totals["offered_bytes"], -totals["rejected_bytes"], -totals["carried_bytes"],
              -totals["dropped_bytes"], -totals["final_queued_bytes"]]
    labels = ["Offered", "Rejected", "Carried", "Dropped", "Final queue"]
    scale = max(1.0, totals["offered_bytes"])
    fig, ax = plt.subplots(figsize=(10.5, 4.6))
    ax.bar(labels, [value / scale * 100 for value in values], color=[BLUE, GOLD, GREEN, CORAL, PURPLE])
    ax.axhline(0, color=INK, lw=1); ax.set_ylabel("Percent of offered UL bytes")
    ax.set_title("Directional accounting closes at machine precision")
    ax.text(.99, .95, f"relative residual {realism['accounting']['maximum_relative_residual']:.1e}",
            transform=ax.transAxes, ha="right", va="top", color=GREEN, fontweight="bold")
    ax.grid(True, axis="y", alpha=.4); fig.tight_layout()
    output["accounting"] = directory / "04_accounting_waterfall.png"
    fig.savefig(output["accounting"], dpi=180, bbox_inches="tight"); plt.close(fig)

    telemetry = realism["telemetry_pathology"]
    fig, (left, middle, right) = plt.subplots(1, 3, figsize=(14, 4.8))
    raw = telemetry["raw_counter_example"]
    left.plot([item["minute"] for item in raw], [np.nan if item["value"] is None else item["value"] for item in raw],
              color=BLUE, marker="o", ms=2)
    left.axvline(9.5, color=CORAL, ls="--", label="reset")
    left.axvspan(5.5, 6.5, color=GOLD, alpha=.25, label="gap")
    left.set(title="Raw counter: reset and scrape gap", xlabel="Minute", ylabel="Counter value"); left.legend()
    buckets = telemetry["reconstructed_10_minute_buckets"]
    middle.bar([str(item["bucket_start_minute"]) for item in buckets],
               [0 if item["mean_rate"] is None else item["mean_rate"] for item in buckets], color=CYAN)
    for index,item in enumerate(buckets):
        if item["quality_flags"]: middle.text(index, 5, "quality\nflag", ha="center", color=CORAL, fontweight="bold")
    middle.set(title="Reconstructed 10-minute buckets", xlabel="Bucket start", ylabel="Mean rate")
    points = telemetry["points"]
    x = [item["missing_fraction"] * 100 for item in points]
    right.plot(x, [item["forecast_wape"] * 100 for item in points], color=BLUE, marker="o", label="forecast WAPE")
    right2 = right.twinx(); right2.plot(x, [item["policy_hold_fraction"] * 100 for item in points],
                                       color=CORAL, marker="s", label="policy hold")
    right.set(title="Missingness changes estimation and control", xlabel="Missing samples (%)", ylabel="WAPE (%)")
    right2.set_ylabel("Buckets held (%)", color=CORAL); right.grid(True, alpha=.4)
    fig.tight_layout(); output["telemetry"] = directory / "05_telemetry_pathology.png"
    fig.savefig(output["telemetry"], dpi=180, bbox_inches="tight"); plt.close(fig)

    models = manifest["display"]["forecast_models"]
    fig, (left, right) = plt.subplots(1, 2, figsize=(12, 4.8))
    for key, label, color in (("seven_day", "7-day", GOLD), ("fourteen_day", "14-day", BLUE)):
        rows = models[key]["by_horizon"]
        left.plot([r["horizon_minutes"] for r in rows], [r["macro_wape"] * 100 for r in rows],
                  marker="o", color=color, label=label)
        right.plot([r["horizon_minutes"] for r in rows], [r["mean_coverage_p90"] * 100 for r in rows],
                   marker="o", color=color, label=f"{label} p90")
    baseline_rows = models["causal_baselines"]["by_horizon"]
    left.plot([r["horizon_minutes"] for r in baseline_rows], [r["moving_average_6"]["macro_wape"]*100 for r in baseline_rows],
              color=GREEN, ls="--", marker=".", label="causal MA6")
    left.plot([r["horizon_minutes"] for r in baseline_rows], [r["seasonal_naive_daily"]["macro_wape"]*100 for r in baseline_rows],
              color=CORAL, ls=":", marker=".", label="causal daily naive")
    left.set(title="Forecast WAPE by horizon", xlabel="Horizon (minutes)", ylabel="Macro WAPE (%)")
    right.axhline(90, color=INK, ls="--", lw=1); right.set(title="Interval coverage", xlabel="Horizon (minutes)", ylabel="Empirical coverage (%)")
    for ax in (left, right): ax.grid(True, alpha=.4); ax.legend()
    fig.tight_layout(); output["forecast"] = directory / "06_forecast_horizon.png"
    fig.savefig(output["forecast"], dpi=180, bbox_inches="tight"); plt.close(fig)

    seven_wape = models["seven_day"]["overall"]["macro_wape"]
    fourteen_wape = models["fourteen_day"]["overall"]["macro_wape"]
    ma6 = manifest["display"]["national_ma6"]; control14 = manifest["display"]["fourteen_day_control"]
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    ax.scatter([seven_wape * 100, fourteen_wape * 100],
               [ma6["mean_pair_reduction"] * 100, control14["mean_pair_reduction"] * 100],
               s=[180, 180], c=[GOLD, BLUE])
    ax.annotate("7-day + MA6", (seven_wape * 100, ma6["mean_pair_reduction"] * 100), xytext=(8, 8), textcoords="offset points")
    ax.annotate("14-day ridge", (fourteen_wape * 100, control14["mean_pair_reduction"] * 100), xytext=(8, -18), textcoords="offset points")
    ax.axhline(ma6["mean_pair_gate"] * 100, color=CORAL, ls="--", label="control gate")
    ax.invert_xaxis(); ax.set(xlabel="Forecast macro WAPE (lower is better →)", ylabel="Mean-pair UL reduction (%)",
                              title="Better forecasts did not automatically improve control")
    ax.grid(True, alpha=.4); ax.legend(); fig.tight_layout()
    output["forecast_control"] = directory / "07_forecast_vs_control.png"
    fig.savefig(output["forecast_control"], dpi=180, bbox_inches="tight"); plt.close(fig)

    pairs = ma6["pairs_detail"]; order = ["surge", "scheduled_fault", "unannounced_outage", "mixed_stress"]
    fig, ax = plt.subplots(figsize=(11, 4.8))
    arrays = [[item["relative_reduction"] * 100 for item in pairs if item["scenario"] == scenario] for scenario in order]
    ax.boxplot(arrays, tick_labels=[item.replace("_", "\n") for item in order], patch_artist=True,
               boxprops={"facecolor": "#DCEEFF", "color": BLUE}, medianprops={"color": NAVY, "linewidth": 2})
    for idx, values in enumerate(arrays, 1): ax.scatter([idx] * len(values), values, color=BLUE, alpha=.65, s=25)
    ax.axhline(0, color=CORAL, ls="--"); ax.set(ylabel="Pair UL overload-area reduction (%)",
                                               title=f"{ma6['pairs']} paired outcomes: mean benefit with visible fault tails")
    ax.grid(True, axis="y", alpha=.4); fig.tight_layout()
    output["outcomes"] = directory / "08_pair_distribution.png"
    fig.savefig(output["outcomes"], dpi=180, bbox_inches="tight"); plt.close(fig)

    ladder = manifest["display"]["oracle_information_ladder"]
    label_map = {"arrival_only": "Arrival-only", "scheduled_fault": "Scheduled faults",
                 "clairvoyant_fault": "Clairvoyant", "bounded_migration_0.1_per_bucket": "+ migration relax."}
    fig, ax = plt.subplots(figsize=(10, 4.8))
    bars = ax.bar([label_map.get(item["regime"], item["regime"]) for item in ladder],
                  [item["mean"] * 100 for item in ladder], color=[GOLD, BLUE, CYAN, PURPLE])
    for bar, item in zip(bars, ladder):
        ax.vlines(bar.get_x() + bar.get_width()/2, item["minimum"]*100, item["maximum"]*100, color=INK, lw=2)
    ax.set(ylabel="UL overload-area headroom (%)", title="Information ladder: faults dominate controllability")
    ax.grid(True, axis="y", alpha=.4); fig.tight_layout()
    output["oracle"] = directory / "09_oracle_ladder.png"
    fig.savefig(output["oracle"], dpi=180, bbox_inches="tight"); plt.close(fig)

    packing = manifest["display"]["packing_ladder"]["rungs"]
    fig, ax = plt.subplots(figsize=(10, 4.8))
    workers = [item["workers"] for item in packing]
    cpu = [np.nan if item["cpu_efficiency_median"] is None else item["cpu_efficiency_median"]*100 for item in packing]
    colors = [GREEN if item["passed"] else CORAL for item in packing]
    ax.bar([str(v) for v in workers], cpu, color=colors)
    cpu_gate = manifest["display"]["packing_ladder"]["cpu_efficiency_gate"] * 100
    ax.axhline(cpu_gate, color=INK, ls="--", label="CPU gate")
    selected = manifest["display"]["packing_ladder"]["selected_workers"]
    ax.set(xlabel="Workers/node", ylabel="Median CPU efficiency (%)", title=f"Packing ladder selects {selected} workers; higher rungs are rejected")
    ax.legend(); ax.grid(True, axis="y", alpha=.4); fig.tight_layout()
    output["packing"] = directory / "10_packing_ladder.png"
    fig.savefig(output["packing"], dpi=180, bbox_inches="tight"); plt.close(fig)

    surface = realism["controllability_surface"]
    fig, ax = plt.subplots(figsize=(9, 5.2)); im = ax.imshow(surface["controllable_fraction"], origin="lower", aspect="auto", cmap="YlGnBu", vmin=0, vmax=1)
    ax.set_xticks(range(len(surface["lead_minutes"])), surface["lead_minutes"])
    ax.set_yticks(range(len(surface["mean_session_lifetime_minutes"])), surface["mean_session_lifetime_minutes"])
    ax.set(xlabel="Fault notice lead time (minutes)", ylabel="Mean session lifetime (minutes)",
           title="New-session-only controllability surface")
    fig.colorbar(im, ax=ax, label="Modeled controllable fraction"); fig.tight_layout()
    output["controllability"] = directory / "11_controllability_surface.png"
    fig.savefig(output["controllability"], dpi=180, bbox_inches="tight"); plt.close(fig)
    return output


class Slide:
    def __init__(self, number: int, title: str, section: str, label: str = "measured-synthetic", dark: bool = False):
        self.number = number; self.dark = dark
        self.image = Image.new("RGB", (W, H), NAVY if dark else PALE)
        self.d = ImageDraw.Draw(self.image)
        fg = WHITE if dark else INK
        self.d.text((70, 35), section.upper(), font=font(18, mono=True), fill=CYAN if dark else BLUE)
        self.text(title, 70, 78, 1190, 116, 40, fg, bold=True)
        self.pill(label, 1305, 38)

    def pill(self, value: str, x: int, y: int) -> None:
        colors = {"live": GREEN, "measured-synthetic": BLUE, "modeled-projection": PURPLE, "external-pending": GOLD}
        color = colors.get(value, MUTED); self.d.rounded_rectangle((x, y, x+225, y+38), radius=14, fill=color)
        self.d.text((x+112, y+19), value.upper(), anchor="mm", font=font(14, bold=True, mono=True), fill=WHITE)

    def text(self, value: str, x: int, y: int, width: int, height: int, size: int,
             color: str = INK, bold: bool = False, align: str = "left") -> None:
        average = max(8, int(width / (size * .56)))
        lines = []
        for paragraph in str(value).split("\n"):
            lines.extend(textwrap.wrap(paragraph, width=average) or [""])
        line_height = int(size * 1.28)
        for index, line in enumerate(lines[:max(1, height // line_height)]):
            if align == "center":
                self.d.text((x+width/2, y+index*line_height), line, anchor="ma", font=font(size, bold), fill=color)
            else:
                self.d.text((x, y+index*line_height), line, font=font(size, bold), fill=color)

    def bullets(self, items: list[str], x: int, y: int, width: int, size: int = 25, gap: int = 68,
                color: str | None = None) -> None:
        fg = color or (WHITE if self.dark else INK)
        for index, item in enumerate(items):
            yy = y + index * gap
            self.d.ellipse((x, yy+7, x+14, yy+21), fill=CYAN)
            self.text(item, x+28, yy, width-28, gap-4, size, fg)

    def card(self, x: int, y: int, w: int, h: int, title: str, value: str, detail: str,
             tone: str = BLUE) -> None:
        fill = "#18384A" if self.dark else WHITE
        self.d.rounded_rectangle((x, y, x+w, y+h), radius=18, fill=fill, outline=tone, width=3)
        self.d.rectangle((x, y, x+8, y+h), fill=tone)
        self.d.text((x+28, y+20), title.upper(), font=font(15, bold=True, mono=True), fill=MUTED if not self.dark else "#AFC6D1")
        self.d.text((x+28, y+55), value, font=font(38, bold=True), fill=tone if not self.dark else WHITE)
        self.text(detail, x+28, y+108, w-50, h-115, 18, WHITE if self.dark else MUTED)

    def chart(self, path: Path, x: int = 70, y: int = 180, w: int = 1460, h: int = 625) -> None:
        image = Image.open(path).convert("RGB"); image.thumbnail((w, h), Image.Resampling.LANCZOS)
        xx = x + (w-image.width)//2; yy = y + (h-image.height)//2
        self.d.rounded_rectangle((x, y, x+w, y+h), radius=18, fill=WHITE, outline=GRID, width=2)
        self.image.paste(image, (xx, yy))

    def footer(self, source: str) -> None:
        fg = "#AFC6D1" if self.dark else MUTED
        self.d.line((70, 850, 1530, 850), fill="#355268" if self.dark else GRID, width=2)
        self.text(source, 70, 862, 1330, 24, 13, fg)
        self.d.text((1515, 873), f"{self.number:02d}", anchor="rm", font=font(16, bold=True, mono=True), fill=fg)


def _architecture(slide: Slide, nodes: list[str], y: int, colors: list[str]) -> None:
    count = len(nodes); width = 1320 // count
    for index, (node, color) in enumerate(zip(nodes, colors)):
        x = 95 + index * width
        slide.d.rounded_rectangle((x, y, x+width-35, y+110), radius=18, fill=WHITE, outline=color, width=4)
        slide.text(node, x+10, y+30, width-55, 70, 21, INK, bold=True, align="center")
        if index < count-1:
            slide.d.line((x+width-30, y+55, x+width-4, y+55), fill=MUTED, width=5)
            slide.d.polygon(((x+width-4, y+55), (x+width-16, y+47), (x+width-16, y+63)), fill=MUTED)


def make_slides(m: dict[str, Any], chart_paths: dict[str, Path]) -> list[Image.Image]:
    slides: list[Slide] = []
    claims = {item["id"]: item for item in m["claims"]}; display = m["display"]
    scale = display["scale"]; guided = display["guided_campaign"]; national = display["national_ma6"]
    forecast = display["forecast_models"]; fourteen = display["fourteen_day_control"]

    def new(title: str, section: str, label: str = "measured-synthetic", dark: bool = False) -> Slide:
        slide = Slide(len(slides)+1, title, section, label, dark); slides.append(slide); return slide

    s = new("Predictive UPF steering: causal control, national-scale evidence", "C-DOT Delhi · 28 August 2026", "measured-synthetic", True)
    s.text("A 45-minute technical showcase", 75, 235, 900, 70, 31, CYAN, bold=True)
    s.text(m["claim_boundary"], 75, 330, 940, 180, 31, WHITE, bold=True)
    s.card(1120, 245, 390, 180, "Live loop", f"{display['live_demo']['upfs']} UPFs", "Causal, offline, new sessions only", GREEN)
    s.card(1120, 455, 390, 180, "Frozen scale", f"{scale['upfs']} UPFs", f"{scale['groups']} groups · {scale['aggregate_population']/1e6:.0f}M aggregate UEs", BLUE)
    s.footer("presentation/delhi_evidence_manifest.json")

    s = new("Start with the evidence boundary—not the promise", "0–3 min · boundary")
    s.bullets([claim["statement"] for claim in m["claims"][:6]], 90, 195, 1080, 23, 82)
    y = 205
    for label, color in (("live", GREEN), ("measured-synthetic", BLUE), ("modeled-projection", PURPLE), ("external-pending", GOLD)):
        s.d.rounded_rectangle((1240, y, 1515, y+64), radius=14, fill=color)
        s.d.text((1377, y+32), label.upper(), anchor="mm", font=font(15, bold=True, mono=True), fill=WHITE); y += 86
    s.footer("Manifest claims[] · evidence labels are mandatory on every result")

    s = new("Why predict? Long-lived sessions make late reaction expensive", "Problem")
    s.bullets(["Current utilization is not the future state: today’s admissions persist into later fault and surge windows.",
               "The actuator can change weights for new sessions; it cannot migrate established sessions.",
               "Forecasting matters only when optimization, certification and actuation convert information into safer placement."], 110, 225, 1380, 29, 120)
    _architecture(s, ["observe", "anticipate", "certify", "steer future sessions"], 600, [BLUE, CYAN, GREEN, GOLD])
    s.footer("simulator/macro/engine.py · optimization/cohort_mpc.py · steering/policy.py")

    s = new("National-scale synthetic topology, bounded into tractable cohorts", "3–11 min · scale")
    cards = [("Aggregate UE population", f"{scale['aggregate_population']/1e6:.0f}M", "exactly conserved"),
             ("UPFs / zones", f"{scale['upfs']} / {scale['zones']}", "edge · regional · central"),
             ("Traffic groups", str(scale['groups']), "12 services × 8 zones"),
             ("Telemetry cadence", f"{scale['step_seconds']} s", "causal event-time ticks")]
    for i, (title, value, detail) in enumerate(cards): s.card(75+i*380, 230, 340, 210, title, value, detail, [BLUE,CYAN,PURPLE,GREEN][i])
    s.text("Aggregate UE cohorts are modeled; persistent subscriber identities are not.", 110, 575, 1380, 70, 30, CORAL, bold=True, align="center")
    s.footer("realism.scale · realism.population.identity_boundary")

    s = new("Realism scorecard: strong structure, explicit calibration gap", "Traffic-model/2.0")
    score = display["realism"]["scorecard"]
    items = [
        ("Structurally aligned", "PASS" if score["structurally_aligned"] else "FAIL", GREEN if score["structurally_aligned"] else CORAL),
        ("Statistically verified", "PASS" if score["statistically_verified"] else "FAIL", GREEN if score["statistically_verified"] else CORAL),
        ("Scale + performance", "PASS" if score["scale_tested"] and score["performance_target_met"] else "FAIL", GREEN if score["scale_tested"] and score["performance_target_met"] else CORAL),
        ("Operator calibrated", "PASS" if score["operator_calibrated"] else "PENDING", GREEN if score["operator_calibrated"] else GOLD),
    ]
    for i, (title, value, color) in enumerate(items):
        s.card(105+i*370, 245, 325, 220, title, value, "synthetic evidence" if value=="PASS" else "requires C-DOT telemetry", color)
    s.text(m["claim_boundary"], 105, 565, 1390, 120, 28, INK, bold=True, align="center")
    s.footer("claims.realism-v2 · traffic-realism-evaluation/2.0")

    for title, section, key, source in [
        ("Distribution fidelity passes all configured acceptance checks", "Traffic-model/2.0", "distribution", "realism.distribution_fidelity"),
        ("Population is conserved while the traffic fingerprint changes by hour", "Traffic-model/2.0", "fingerprint", "realism.population · realism.traffic_fingerprint"),
        ("A representative UPF day exposes edge, regional and central behavior", "Traffic-model/2.0", "upf_day", "realism.representative_upfs"),
        ("Variable-rate accounting closes without reconstructing load from session counts", "Traffic-model/2.0", "accounting", "realism.accounting"),
        ("Telemetry pathologies are visible and quality flags propagate", "Traffic-model/2.0", "telemetry", "realism.telemetry_pathology"),
    ]:
        s = new(title, section); s.chart(chart_paths[key]); s.footer(source)

    s = new("Offline evidence plane: immutable inputs to frozen artifacts", "11–21 min · architecture")
    _architecture(s, ["registry", "manifest", "PBS shards", "Parquet", "train + evaluate", "freeze + hash"], 310,
                  [PURPLE, BLUE, CYAN, GREEN, GOLD, CORAL])
    s.bullets(["Every displayed result resolves to a source path and SHA-256 in one presentation manifest.",
               "Seed matrices, model/profile identity and source fingerprints remain separate per experiment."], 140, 560, 1320, 26, 86)
    s.footer("presentation/delhi_evidence_manifest.json · experiments/artifacts.py")

    s = new("Online loop: observe → forecast → optimize → certify → steer → measure", "Architecture", "live")
    _architecture(s, ["observe", "aggregate", "forecast", "MPC", "certificate", "new sessions", "feedback"], 280,
                  [BLUE, CYAN, PURPLE, GOLD, GREEN, CORAL, BLUE])
    s.bullets(["Policy is selected only after a bucket closes.", "Weights affect later arrivals; established sessions stay anchored.",
               "Fallback retains static or the last safe policy when state, telemetry or certificate quality is insufficient."], 130, 540, 1360, 25, 76)
    s.footer("demo_api/runtime.py · simulator/macro/engine.py::advance · steering/gate.py")

    horizon = m["experiments"]["national_ma6"]["profile"]["settings"]["horizon_windows"]
    s = new(f"MPC carries cohort state across {horizon} windows and proves the first move", "Controller anatomy")
    s.bullets(["State: active cohorts by group, UPF, rate bin and remaining lifetime.",
               "Action: normalized group→UPF weights for future sessions.",
               "Objective: directional overload + drops + terminal exposure + policy deviation.",
               "Constraints: UL/DL/session capacity, health, eligibility, locality and diversification.",
               "Certificate: compare MPC and static from the identical causal state; publish only a safe first action."], 110, 195, 1370, 27, 102)
    s.footer("optimization/cohort_mpc.py · configs/cohort_mpc_pilot_10pct_v2.json")

    s = new("The experiment journey includes the failures that changed the design", "21–33 min · journey")
    _architecture(s, ["deterministic simulator", "causal baselines", "myopic LP fails", "cohort MPC passes", "14-day forecast regresses"], 275,
                  [GREEN, BLUE, CORAL, GREEN, GOLD])
    s.text("The engineering conclusion came from negative results: one-window optimization concentrated persistent sessions, and forecast accuracy alone did not guarantee a better policy.",
           120, 545, 1360, 160, 29, INK, bold=True, align="center")
    s.footer("docs/extreme-optimizer-tuning-results.md · national MA6 + 14-day evaluations")

    s = new("Forecast accuracy and interval coverage improve in the fourteen-day corpus", "Forecast evidence")
    s.chart(chart_paths["forecast"]); s.footer("display.forecast_models · two frozen forecast records")

    s = new("Why the first optimizers failed: they optimized a window, not persistence", "Failed experiments")
    s.bullets(["One-window HiGHS reduced projected peak utilization but concentrated allocations across correlated groups.",
               "Many sessions lasted for hours, so an apparently good placement became tomorrow’s residual overload.",
               "Static capacity weighting remained a strong baseline because it diversified continuously.",
               "The fix was structural: explicit cohort survival, terminal exposure and a same-state static certificate."], 120, 220, 1360, 28, 112)
    s.footer("docs/extreme-optimizer-tuning-results.md · docs/cohort-mpc-development-results.md")

    s = new("Better forecasts did not automatically improve closed-loop control", "Forecast → control")
    s.chart(chart_paths["forecast_control"], 70, 170, 1460, 570)
    s.text(f"14-day WAPE {pct(forecast['fourteen_day']['overall']['macro_wape'])}; MPC mean-pair {pct(fourteen['mean_pair_reduction'])}; severity-weighted {pct(fourteen['severity_weighted_reduction'])}.",
           120, 780, 1360, 50, 20, CORAL, bold=True, align="center")
    s.footer("display.forecast_models · display.national_ma6 · display.fourteen_day_control")

    s = new("The accepted national MA6 result passes on average—and exposes fault tails", "Closed-loop evidence")
    s.chart(chart_paths["outcomes"])
    s.text(f"mean-pair {pct(national['mean_pair_reduction'])} · severity-weighted {pct(national['severity_weighted_reduction'])} · worst pair {pct(national['worst_pair_reduction'])}",
           120, 780, 1360, 45, 21, INK, bold=True, align="center")
    s.footer("display.national_ma6 · 30 exact paired seeds")

    s = new("Oracle headroom isolates the causal fault-information gap", "Controllability", "modeled-projection")
    s.chart(chart_paths["oracle"]); s.footer("display.oracle_information_ladder · non-deployable continuous relaxations")

    node_ladder = "→".join(str(item) for item in display["multinode"]["pending_node_counts"])
    selected_workers = display["packing_ladder"]["selected_workers"]
    s = new(f"HPC evidence: {selected_workers} workers selected; {node_ladder} chart remains withheld", "Scale execution")
    s.chart(chart_paths["packing"], 70, 180, 920, 610)
    s.d.rounded_rectangle((1040, 220, 1515, 690), radius=20, fill=WHITE, outline=GOLD, width=4)
    s.text(" → ".join(str(item) for item in display["multinode"]["pending_node_counts"]) + " nodes", 1090, 280, 375, 70, 35, GOLD, bold=True, align="center")
    s.text(display["multinode"]["publication_rule"], 1090, 385, 375, 180, 25, INK, bold=True, align="center")
    s.pill("external-pending", 1165, 610)
    s.footer("display.packing_ladder · display.multinode · worklists are not performance reports")

    s = new("Hybrid demonstration: live causality, then frozen national evidence", "33–40 min · demo", "live")
    rows = [("1", "Run reliable guided story", f"{display['live_demo']['upfs']}-UPF causal runtime"),
            ("2", "Expose the decision", "forecast → certificate → weights"),
            ("3", "Show realized effect", "future-session placement and later overload"),
            ("4", "Switch to frozen scale", f"{scale['upfs']} UPFs · {scale['groups']} groups · hashes")]
    for i, (num, head, detail) in enumerate(rows):
        y=200+i*145; s.d.ellipse((100,y,170,y+70),fill=[GREEN,BLUE,CYAN,PURPLE][i]); s.d.text((135,y+35),num,anchor="mm",font=font(28,True),fill=WHITE)
        s.text(head, 205, y, 500, 50, 27, INK, bold=True); s.text(detail, 205, y+50, 950, 50, 22, MUTED)
    s.footer("docs/delhi-presenter-guide.md · presentation/delhi/demo-reveal.gif")

    s = new("Conclusion: proven synthetic control loop; C-DOT co-design is the next evidence step", "40–45 min · close", "external-pending", True)
    s.bullets(["Proven here: causal new-session loop, deterministic v1/v2 simulation, hashed campaign evidence and same-state certification.",
               "Requested from C-DOT: UPF counters, topology/eligibility truth, capacity envelopes and a supported new-session selection hook.",
               "Proposed progression: advisory replay → shadow recommendations → bounded pilot with fail-closed rollback."], 100, 215, 1410, 28, 135, WHITE)
    s.text("No claim of production calibration, autonomous actuation, established-session migration or production readiness.", 105, 690, 1390, 90, 27, GOLD, bold=True, align="center")
    s.footer("claims.smf-hook · evidence boundary · co-design request")

    # Technical appendix
    s = new("Appendix: reveal sequence and offline fallback", "Technical appendix", "live")
    _architecture(s, ["normal", "pressure", "forecast", "certificate", "weight change", "realized placement"], 300,
                  [GREEN, GOLD, BLUE, GREEN, CORAL, CYAN])
    s.text("The GIF, slide images, HTML and PDF are fully offline. Browser reconnect does not alter server-side realized history.",
           120, 570, 1360, 120, 28, INK, bold=True, align="center")
    s.footer("presentation/delhi/demo-reveal.gif · index.html · Delhi deck PDF")

    s = new("Appendix: controllability depends on notice and session lifetime", "Technical appendix", "modeled-projection")
    s.chart(chart_paths["controllability"]); s.footer("realism.controllability_surface · modeled projection, not a controller result")

    s = new("Appendix: traceability is machine-verifiable", "Technical appendix")
    source_rows = list(m["sources"].items())[:10]
    y=190
    for key, source in source_rows:
        s.d.text((90,y),key,font=font(18,True,True),fill=BLUE)
        s.text(Path(source["path"]).name, 300, y, 620, 30, 18, INK)
        s.d.text((950,y),source["sha256"][:24]+"…",font=font(17,mono=True),fill=MUTED); y+=58
    s.footer("presentation/delhi_evidence_manifest.json · full paths and SHA-256 values")

    pilot = display["v2_controller_pilot"]
    s = new("Appendix: v2 controller pilot is gated and intentionally pending", "Technical appendix", "external-pending")
    s.card(90, 220, 410, 230, "Fresh pilot", f"{pilot['required_pairs']} pairs", "four seeds × four stress classes", GOLD)
    s.card(590, 220, 410, 230, "Advance threshold", pct(pilot['advance_gate']['mean_pair_ul_reduction'],0), "positive severity-weighted + no guardrail regression", BLUE)
    s.card(1090, 220, 410, 230, "Current status", "NOT RUN", "accepted v1 profile remains unchanged", CORAL)
    s.text("If realism changes controller behavior, that result will be presented as evidence—not used to overwrite the accepted v1 campaign.",
           130, 585, 1340, 130, 29, INK, bold=True, align="center")
    s.footer("display.v2_controller_pilot · configs/delhi_traffic_v2.json")
    return [slide.image for slide in slides]


def _pptx(slides: list[Path], output: Path) -> None:
    ns_a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    ns_p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    ns_r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    content = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
               '<Default Extension="xml" ContentType="application/xml"/>',
               '<Default Extension="png" ContentType="image/png"/>',
               '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
               '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
               '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
               '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>']
    content += [f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for i in range(1,len(slides)+1)]
    content += ['</Types>']
    presentation = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="{ns_a}" xmlns:r="{ns_r}" xmlns:p="{ns_p}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>{''.join(f'<p:sldId id="{255+i}" r:id="rId{i+1}"/>' for i in range(1,len(slides)+1))}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'''
    pres_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>']
    pres_rels += [f'<Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>' for i in range(1,len(slides)+1)]
    pres_rels += ['</Relationships>']
    master = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldMaster xmlns:a="{ns_a}" xmlns:r="{ns_r}" xmlns:p="{ns_p}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>'''
    layout = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldLayout xmlns:a="{ns_a}" xmlns:r="{ns_r}" xmlns:p="{ns_p}" type="blank"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'''
    theme = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="{ns_a}" name="C-DOT Delhi"><a:themeElements><a:clrScheme name="C-DOT"><a:dk1><a:srgbClr val="0B1F33"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="172B3A"/></a:dk2><a:lt2><a:srgbClr val="F2F6FA"/></a:lt2><a:accent1><a:srgbClr val="1877F2"/></a:accent1><a:accent2><a:srgbClr val="00A6A6"/></a:accent2><a:accent3><a:srgbClr val="22A06B"/></a:accent3><a:accent4><a:srgbClr val="F4B400"/></a:accent4><a:accent5><a:srgbClr val="F45B69"/></a:accent5><a:accent6><a:srgbClr val="7457D5"/></a:accent6><a:hlink><a:srgbClr val="1877F2"/></a:hlink><a:folHlink><a:srgbClr val="7457D5"/></a:folHlink></a:clrScheme><a:fontScheme name="C-DOT"><a:majorFont><a:latin typeface="DejaVu Sans"/></a:majorFont><a:minorFont><a:latin typeface="DejaVu Sans"/></a:minorFont></a:fontScheme><a:fmtScheme name="C-DOT"><a:fillStyleLst/><a:lnStyleLst/><a:effectStyleLst/><a:bgFillStyleLst/></a:fmtScheme></a:themeElements></a:theme>'''
    template = ROOT / "presentation/CDOT_Predictive_UPF_Steering_Technical_Review.pptx"
    if template.is_file():
        with zipfile.ZipFile(template) as existing:
            theme = existing.read("ppt/theme/theme1.xml")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(content))
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>')
        z.writestr("ppt/presentation.xml", presentation); z.writestr("ppt/_rels/presentation.xml.rels", "".join(pres_rels))
        z.writestr("ppt/slideMasters/slideMaster1.xml", master)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>')
        z.writestr("ppt/slideLayouts/slideLayout1.xml", layout)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>')
        z.writestr("ppt/theme/theme1.xml", theme)
        for i, path in enumerate(slides, 1):
            slide = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:a="{ns_a}" xmlns:r="{ns_r}" xmlns:p="{ns_p}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr><p:pic><p:nvPicPr><p:cNvPr id="2" name="Slide image {i}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="12192000" cy="6858000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'''
            z.writestr(f"ppt/slides/slide{i}.xml", slide)
            z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image{i}.png"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>')
            z.write(path, f"ppt/media/image{i}.png")


def _html(slides: list[Path], output: Path, title: str) -> None:
    encoded = [base64.b64encode(path.read_bytes()).decode() for path in slides]
    figures = "".join(f'<section id="s{i}"><img src="data:image/png;base64,{data}" alt="Slide {i}"></section>' for i,data in enumerate(encoded,1))
    output.write_text(f'''<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>body{{margin:0;background:#061522;color:white;font-family:sans-serif}}section{{min-height:100vh;display:flex;align-items:center;justify-content:center}}img{{max-width:100vw;max-height:100vh;box-shadow:0 0 40px #0008}}nav{{position:fixed;right:16px;bottom:16px;background:#0B1F33dd;padding:10px 14px;border-radius:12px}}</style></head><body>{figures}<nav>Offline fallback · ↑ ↓ / PageUp PageDown</nav><script>addEventListener('keydown',e=>{{if(['ArrowDown','PageDown',' '].includes(e.key))scrollBy(0,innerHeight);if(['ArrowUp','PageUp'].includes(e.key))scrollBy(0,-innerHeight)}})</script></body></html>''', encoding="utf-8")


def _guide(m: dict[str, Any], output: Path) -> None:
    d=m["display"]; n=d["national_ma6"]; g=d["guided_campaign"]; f=d["fourteen_day_control"]
    output.write_text(f'''# C-DOT Delhi 45-minute presenter guide

## Evidence language

Central claim: **{m['claim_boundary']}**

Use the four labels exactly as shown on slides: `live`, `measured-synthetic`, `modeled-projection`, and `external-pending`.

## Run of show

- **0–3 min, slides 1–2:** define what is live, measured synthetic, projected, and pending.
- **3–11 min, slides 3–10:** establish {d['scale']['aggregate_population']/1e6:.0f}M aggregate UEs, {d['scale']['upfs']} UPFs, {d['scale']['zones']} zones, {d['scale']['groups']} groups, distribution checks, accounting, and telemetry quality.
- **11–21 min, slides 11–13:** explain offline provenance, the causal loop, and the same-state MPC certificate.
- **21–33 min, slides 14–20:** show failed one-window designs, forecast horizons, the forecast/control gap, the 30-pair outcome distribution, oracle headroom, and the packing gate.
- **33–40 min, slide 21:** run the live {d['live_demo']['upfs']}-UPF story, then switch to the frozen national evidence view.
- **40–45 min, slide 22:** request counters, topology/eligibility truth, capacity envelopes, and a supported new-session selection hook.

## Numbers that must remain distinct

- Guided campaign: **{pct(g['mean_pair_reduction'])}** mean-pair improvement.
- National MA6 MPC: **{pct(n['mean_pair_reduction'])}** mean-pair, **{pct(n['severity_weighted_reduction'])}** severity-weighted, worst pair **{pct(n['worst_pair_reduction'])}**.
- Fourteen-day forecaster MPC: **{pct(f['mean_pair_reduction'])}** mean-pair and **{pct(f['severity_weighted_reduction'])}** severity-weighted; gate failed.
- Oracle rows are non-deployable modeled projections, not controller results.

## Live demo checkpoints

1. Begin in normal state and say established sessions remain anchored.
2. Reveal pressure; do not imply the route has already changed.
3. Show the causal forecast and same-state certificate.
4. Reveal changed weights for future sessions only.
5. Show realized placement and later overload evidence.
6. Switch to frozen {d['scale']['upfs']}-UPF evidence and open the manifest/hash view.

## Failure rehearsal

- Browser reconnect: reload; server-side story state is authoritative.
- Telemetry gap: point to quality flags and the policy-hold behavior.
- Policy fallback: explain that static/last-safe remains active.
- Offline fallback: open `presentation/delhi/index.html` or the PDF; play `demo-reveal.gif` if the live UI is unavailable.
- PBS/internet: never required for the live story.

## Closing language

Do not claim C-DOT calibration, autonomous actuation, established-session migration, production readiness, or completed 2→4→12-node scaling. Propose advisory replay → shadow recommendations → bounded pilot.
''', encoding="utf-8")


def build(manifest_path: Path, output: Path) -> dict[str, Any]:
    manifest = read_manifest(manifest_path); output.mkdir(parents=True, exist_ok=True)
    chart_paths = charts(manifest, output/"charts")
    images = make_slides(manifest, chart_paths)
    rendered = output/"rendered"; rendered.mkdir(exist_ok=True)
    paths=[]
    for i,image in enumerate(images,1):
        path=rendered/f"slide-{i:02d}.png"; image.save(path, optimize=True); paths.append(path)
    stem="CDOT_Predictive_UPF_Steering_Delhi_2026"
    pptx=output/f"{stem}.pptx"; pdf=output/f"{stem}.pdf"
    _pptx(paths,pptx)
    images[0].save(pdf, "PDF", resolution=110, save_all=True, append_images=images[1:])
    _html(paths, output/"index.html", stem)
    reveal=[images[index].resize((960,540),Image.Resampling.LANCZOS) for index in (0,10,16,20)]
    reveal[0].save(output/"demo-reveal.gif", save_all=True, append_images=reveal[1:], duration=[1800,1800,2200,2600], loop=0)
    _guide(manifest, ROOT/"docs/delhi-presenter-guide.md")
    report={"schema_version":"delhi-presentation-build/1.0","created_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
            "manifest":str(manifest_path.resolve()),"manifest_sha256":hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "slides":len(paths),"main_slides":22,"appendix_slides":len(paths)-22,
            "outputs":{p.name:{"path":str(p.resolve()),"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"bytes":p.stat().st_size}
                       for p in (pptx,pdf,output/"index.html",output/"demo-reveal.gif")},
            "charts":{key:str(path.relative_to(output)) for key,path in chart_paths.items()}}
    (output/"build-report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return report


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--manifest",type=Path,default=ROOT/"presentation/delhi_evidence_manifest.json")
    parser.add_argument("--output",type=Path,default=OUT); args=parser.parse_args()
    print(json.dumps(build(args.manifest,args.output),sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
