#!/usr/bin/env python3
"""Build the C-DOT predictive UPF steering technical-review deck with LibreOffice UNO."""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

import uno
from PIL import Image, ImageOps
from com.sun.star.awt import Point, Size
from com.sun.star.beans import PropertyValue


class _FillStyle:
    SOLID = uno.Enum("com.sun.star.drawing.FillStyle", "SOLID")
    NONE = uno.Enum("com.sun.star.drawing.FillStyle", "NONE")


class _LineStyle:
    SOLID = uno.Enum("com.sun.star.drawing.LineStyle", "SOLID")
    DASH = uno.Enum("com.sun.star.drawing.LineStyle", "DASH")
    NONE = uno.Enum("com.sun.star.drawing.LineStyle", "NONE")


class _TextVerticalAdjust:
    TOP = uno.Enum("com.sun.star.drawing.TextVerticalAdjust", "TOP")
    CENTER = uno.Enum("com.sun.star.drawing.TextVerticalAdjust", "CENTER")


class _ParagraphAdjust:
    LEFT = uno.Enum("com.sun.star.style.ParagraphAdjust", "LEFT")
    CENTER = uno.Enum("com.sun.star.style.ParagraphAdjust", "CENTER")
    RIGHT = uno.Enum("com.sun.star.style.ParagraphAdjust", "RIGHT")


FillStyle = _FillStyle
LineStyle = _LineStyle
TextVerticalAdjust = _TextVerticalAdjust
ParagraphAdjust = _ParagraphAdjust


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "presentation"
ASSET_DIR = OUT / "generated_assets"
PPTX = OUT / "CDOT_Predictive_UPF_Steering_Technical_Review.pptx"
PDF = OUT / "CDOT_Predictive_UPF_Steering_Technical_Review.pdf"

W, H = 33867, 19050  # 13.333 x 7.5 inches, in 1/100 mm

NAVY = 0x10222E
NAVY_2 = 0x193644
TEAL = 0x007C99
TEAL_2 = 0x2F91A6
CYAN = 0x77C6D4
GREEN = 0x16805F
AMBER = 0xC77B0B
RED = 0xCD443B
PURPLE = 0x6750A4
INK = 0x172731
SLATE = 0x526875
MUTED = 0x718592
PALE = 0xF3F7F8
PALE_2 = 0xEAF1F3
PALE_TEAL = 0xE1F1F4
PALE_GREEN = 0xE5F2ED
PALE_AMBER = 0xFAF0DE
PALE_RED = 0xFAE8E6
WHITE = 0xFFFFFF
LINE = 0xCFDCE1
GRID = 0xDCE6E9

FONT = "Liberation Sans"
MONO = "Liberation Mono"


def mm(value: float) -> int:
    return int(round(value * 100))


def prop(name: str, value) -> PropertyValue:
    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item


def connect():
    local_ctx = uno.getComponentContext()
    resolver = local_ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local_ctx
    )
    for _ in range(50):
        try:
            return resolver.resolve(
                "uno:pipe,name=cdot_slide_builder;urp;StarOffice.ComponentContext"
            )
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("LibreOffice UNO listener did not become ready")


class Deck:
    def __init__(self, ctx):
        self.ctx = ctx
        self.smgr = ctx.ServiceManager
        desktop = self.smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        self.doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
        self.pages = self.doc.getDrawPages()
        self.provider = self.smgr.createInstanceWithContext(
            "com.sun.star.graphic.GraphicProvider", ctx
        )
        self.slide_no = 0

    def new_slide(self, title: str, section: str, *, dark: bool = False, subtitle: str | None = None):
        if self.slide_no == 0:
            page = self.pages.getByIndex(0)
        else:
            page = self.pages.insertNewByIndex(self.pages.getCount())
        page.Width = W
        page.Height = H
        self.slide_no += 1
        self.rect(page, 0, 0, W, H, NAVY if dark else PALE, line=None)
        if self.slide_no > 1:
            self.text(page, mm(9), mm(4.4), mm(86), mm(4), section.upper(), 8.0,
                      TEAL if not dark else CYAN, bold=True, font=MONO, spacing=1.8)
            self.text(page, mm(9), mm(10.5), mm(294), mm(16), title, 21.5,
                      WHITE if dark else INK, bold=True)
            if subtitle:
                self.text(page, mm(9), mm(28.2), mm(292), mm(7), subtitle, 9.6,
                          0xBED0D8 if dark else SLATE)
            self.synthetic_pill(page, dark=dark)
        return page

    def synthetic_pill(self, page, *, dark=False):
        fill = 0x213E4B if dark else WHITE
        self.rect(page, mm(300), mm(5), mm(26), mm(5.5), fill,
                  line=0x54717D if dark else LINE, radius=1.4)
        self.text(page, mm(300.5), mm(6.1), mm(25), mm(3), "SYNTHETIC DATA", 7.1,
                  0xF4C36B if dark else AMBER, bold=True, align=ParagraphAdjust.CENTER, font=MONO)

    def footer(self, page, source: str, *, dark=False):
        color = 0x9FB4BE if dark else MUTED
        self.line(page, mm(9), mm(183.8), mm(317), mm(183.8), 0x39515C if dark else LINE, 0.3)
        self.text(page, mm(9), mm(185), mm(288), mm(3.5), source, 6.8, color, font=MONO)
        self.text(page, mm(307), mm(185), mm(19), mm(3.5), f"{self.slide_no:02d}", 7.5,
                  color, bold=True, align=ParagraphAdjust.RIGHT, font=MONO)

    def rect(self, page, x, y, w, h, fill, *, line=LINE, radius=0.0, transparency=0):
        shape = self.doc.createInstance("com.sun.star.drawing.RectangleShape")
        shape.Position = Point(int(x), int(y))
        shape.Size = Size(int(w), int(h))
        shape.FillStyle = FillStyle.SOLID
        shape.FillColor = int(fill)
        shape.FillTransparence = int(transparency)
        shape.LineStyle = LineStyle.NONE if line is None else LineStyle.SOLID
        if line is not None:
            shape.LineColor = int(line)
            shape.LineWidth = 18
        if radius:
            try:
                shape.CornerRadius = mm(radius)
            except Exception:
                pass
        page.add(shape)
        return shape

    def line(self, page, x1, y1, x2, y2, color=LINE, width=0.6, dash=False):
        shape = self.doc.createInstance("com.sun.star.drawing.LineShape")
        shape.Position = Point(int(x1), int(y1))
        shape.Size = Size(int(x2 - x1), int(y2 - y1))
        shape.LineColor = int(color)
        shape.LineWidth = max(1, mm(width) // 10)
        shape.LineStyle = LineStyle.DASH if dash else LineStyle.SOLID
        page.add(shape)
        return shape

    def text(self, page, x, y, w, h, value, size, color=INK, *, bold=False,
             align=ParagraphAdjust.LEFT, valign=TextVerticalAdjust.TOP,
             font=FONT, spacing=0.0, margin=0.0, rotation=0):
        shape = self.doc.createInstance("com.sun.star.drawing.TextShape")
        shape.Position = Point(int(x), int(y))
        shape.Size = Size(int(w), int(h))
        shape.FillStyle = FillStyle.NONE
        shape.LineStyle = LineStyle.NONE
        shape.TextVerticalAdjust = valign
        shape.TextLeftDistance = mm(margin)
        shape.TextRightDistance = mm(margin)
        shape.TextUpperDistance = mm(margin)
        shape.TextLowerDistance = mm(margin)
        page.add(shape)
        text_object = shape.getText()
        text_object.setString(str(value))
        cursor = text_object.createTextCursor()
        cursor.gotoStart(False)
        cursor.gotoEnd(True)
        cursor.CharFontName = font
        cursor.CharHeight = float(size)
        cursor.CharColor = int(color)
        cursor.CharWeight = 150.0 if bold else 100.0
        cursor.ParaAdjust = align
        try:
            cursor.CharKerning = int(spacing * 100)
        except Exception:
            pass
        if rotation:
            shape.RotateAngle = int(rotation * 100)
        return shape

    def image(self, page, path: Path, x, y, w, h, *, border=LINE):
        shape = self.doc.createInstance("com.sun.star.drawing.GraphicObjectShape")
        shape.Position = Point(int(x), int(y))
        shape.Size = Size(int(w), int(h))
        graphic = self.provider.queryGraphic((prop("URL", uno.systemPathToFileUrl(str(path))),))
        shape.Graphic = graphic
        shape.LineStyle = LineStyle.NONE
        page.add(shape)
        if border is not None:
            outline = self.rect(page, x, y, w, h, 0xFFFFFF, line=border, transparency=100)
            return shape, outline
        return shape

    def circle(self, page, x, y, d, fill, *, line=None):
        shape = self.doc.createInstance("com.sun.star.drawing.EllipseShape")
        shape.Position = Point(int(x), int(y))
        shape.Size = Size(int(d), int(d))
        shape.FillStyle = FillStyle.SOLID
        shape.FillColor = int(fill)
        shape.LineStyle = LineStyle.NONE if line is None else LineStyle.SOLID
        if line is not None:
            shape.LineColor = int(line)
        page.add(shape)
        return shape

    def card(self, page, x, y, w, h, *, fill=WHITE, line=LINE, accent=None, radius=2.2):
        shape = self.rect(page, x, y, w, h, fill, line=line, radius=radius)
        if accent is not None:
            self.rect(page, x, y, mm(1.8), h, accent, line=None)
        return shape

    def metric(self, page, x, y, w, label, value, detail, *, tone=TEAL, dark=False):
        fill = 0x1B3541 if dark else WHITE
        line = 0x3B5966 if dark else LINE
        self.card(page, x, y, w, mm(28), fill=fill, line=line, accent=tone)
        self.text(page, x + mm(5), y + mm(4), w - mm(9), mm(4), label.upper(), 7.2,
                  0x9EB3BD if dark else MUTED, bold=True, font=MONO)
        self.text(page, x + mm(5), y + mm(9), w - mm(9), mm(9), value, 24,
                  WHITE if dark else tone, bold=True)
        self.text(page, x + mm(5), y + mm(20), w - mm(9), mm(5), detail, 8.2,
                  0xC1D0D6 if dark else SLATE)

    def bullet_list(self, page, x, y, w, items, *, font_size=11, color=INK,
                    bullet_color=TEAL, gap=8.2, level2=False):
        current = y
        for item in items:
            if isinstance(item, tuple):
                head, body = item
            else:
                head, body = "", item
            self.circle(page, x, current + mm(1.8), mm(2.2), bullet_color)
            if head:
                self.text(page, x + mm(5), current, w - mm(5), mm(5), head, font_size,
                          color, bold=True)
                self.text(page, x + mm(5), current + mm(5), w - mm(5), mm(gap - 3), body,
                          font_size - 1.2, SLATE if color == INK else color)
            else:
                self.text(page, x + mm(5), current - mm(0.5), w - mm(5), mm(gap), body,
                          font_size, color)
            current += mm(gap)

    def arrow(self, page, x1, y, x2, *, color=TEAL, label=None):
        self.line(page, x1, y, x2 - mm(2.4), y, color, 1.3)
        self.text(page, x2 - mm(4), y - mm(3), mm(4), mm(6), "›", 18, color, bold=True,
                  align=ParagraphAdjust.CENTER, valign=TextVerticalAdjust.CENTER)
        if label:
            self.text(page, (x1 + x2) // 2 - mm(16), y - mm(6), mm(32), mm(4), label, 7.2,
                      MUTED, align=ParagraphAdjust.CENTER, font=MONO)

    def table(self, page, x, y, widths, rows, *, header=True, row_h=7.0, font_size=8.2,
              header_fill=NAVY_2, header_color=WHITE, fills=None, alignments=None):
        total = sum(widths)
        current_y = y
        for r_index, row in enumerate(rows):
            fill = header_fill if header and r_index == 0 else (
                fills[r_index] if fills and r_index < len(fills) else (WHITE if r_index % 2 else 0xF8FAFB)
            )
            self.rect(page, x, current_y, total, mm(row_h), fill, line=LINE)
            current_x = x
            for c_index, (cell, width) in enumerate(zip(row, widths)):
                color = header_color if header and r_index == 0 else INK
                bold = header and r_index == 0
                align = alignments[c_index] if alignments else ParagraphAdjust.LEFT
                self.text(page, current_x + mm(1.7), current_y + mm(1.3), width - mm(3.4), mm(row_h - 2.0),
                          cell, font_size if r_index else font_size - 0.2, color, bold=bold,
                          align=align, valign=TextVerticalAdjust.CENTER)
                current_x += width
            current_y += mm(row_h)
        return current_y

    def bar(self, page, x, y, w, h, value, max_value, *, color=TEAL, bg=PALE_2, label=None):
        self.rect(page, x, y, w, h, bg, line=None, radius=1.2)
        width = 0 if max_value == 0 else max(0, min(w, w * value / max_value))
        self.rect(page, x, y, width, h, color, line=None, radius=1.2)
        if label:
            self.text(page, x + w + mm(2), y - mm(0.8), mm(25), h + mm(2), label, 8.5, INK,
                      bold=True, valign=TextVerticalAdjust.CENTER)

    def save(self):
        OUT.mkdir(parents=True, exist_ok=True)
        self.doc.storeAsURL(
            uno.systemPathToFileUrl(str(PPTX)),
            (prop("FilterName", "Impress MS PowerPoint 2007 XML"), prop("Overwrite", True)),
        )
        self.doc.storeToURL(
            uno.systemPathToFileUrl(str(PDF)),
            (prop("FilterName", "impress_pdf_Export"), prop("Overwrite", True)),
        )
        self.doc.close(True)


def crop_image(source: Path, name: str, aspect: float, *, focus=(0.5, 0.5)) -> Path:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    destination = ASSET_DIR / name
    image = Image.open(source).convert("RGB")
    src_aspect = image.width / image.height
    if src_aspect > aspect:
        new_w = int(image.height * aspect)
        left = int((image.width - new_w) * focus[0])
        image = image.crop((left, 0, left + new_w, image.height))
    else:
        new_h = int(image.width / aspect)
        top = int((image.height - new_h) * focus[1])
        image = image.crop((0, top, image.width, top + new_h))
    image = ImageOps.expand(image, border=2, fill="#cfdae0")
    image.save(destination, quality=94)
    return destination


def section_marker(deck: Deck, page, number, title, detail, x, y, *, color=TEAL):
    deck.circle(page, x, y, mm(9), color)
    deck.text(page, x, y + mm(1.7), mm(9), mm(5), f"{number:02d}", 9, WHITE, bold=True,
              align=ParagraphAdjust.CENTER, valign=TextVerticalAdjust.CENTER, font=MONO)
    deck.text(page, x + mm(12), y - mm(0.5), mm(70), mm(5), title, 11.5, INK, bold=True)
    deck.text(page, x + mm(12), y + mm(5), mm(75), mm(9), detail, 8.7, SLATE)


def build(deck: Deck):
    # 1 — Cover
    page = deck.new_slide("", "", dark=True)
    deck.text(page, mm(10), mm(9), mm(74), mm(7), "C-DOT · PREDICTIVE USER PLANE", 9,
              CYAN, bold=True, font=MONO, spacing=1.4)
    deck.text(page, mm(10), mm(39), mm(244), mm(32),
              "Predictive UPF Steering\nSimulation, Forecasting, Optimization & Demo",
              29, WHITE, bold=True)
    deck.text(page, mm(10), mm(78), mm(230), mm(12),
              "Implementation review and evidence-backed technical presentation",
              14.5, 0xC4D3D9)
    deck.text(page, mm(10), mm(95), mm(140), mm(8),
              "Prepared for academic review and the C-DOT team · 09 August 2026",
              9.5, 0x9FB4BE)
    # Cover flow
    x_positions = [12, 75, 138, 201, 264]
    labels = [
        ("01", "SIMULATE", "30 s causal cohorts"),
        ("02", "FORECAST", "10–80 min demand"),
        ("03", "OPTIMIZE", "2 h cohort MPC"),
        ("04", "STEER", "new sessions only"),
        ("05", "DEMONSTRATE", "live operator view"),
    ]
    for idx, ((num, title, detail), x) in enumerate(zip(labels, x_positions)):
        deck.card(page, mm(x), mm(125), mm(52), mm(27), fill=0x193541, line=0x385662,
                  accent=[CYAN, TEAL_2, PURPLE, GREEN, AMBER][idx])
        deck.text(page, mm(x + 5), mm(130), mm(42), mm(4), num, 7.5, 0x9CB3BD,
                  font=MONO, bold=True)
        deck.text(page, mm(x + 5), mm(136), mm(42), mm(5), title, 11.2, WHITE, bold=True)
        deck.text(page, mm(x + 5), mm(143), mm(42), mm(5), detail, 8.2, 0xB8CBD2)
        if idx < 4:
            deck.arrow(page, mm(x + 52), mm(138.5), mm(x_positions[idx + 1]), color=0x4D6A75)
    deck.text(page, mm(10), mm(170), mm(250), mm(6),
              "All performance evidence in this deck is synthetic and artifact-backed.",
              10.5, 0xF0C36D, bold=True)
    deck.footer(page, "Repository review · implementation + docs + frozen experiment artifacts", dark=True)

    # 2 — Executive summary
    page = deck.new_slide("The system works end to end—and the evidence boundary is explicit", "Executive summary",
                          subtitle="A complete synthetic loop is implemented; the controller is a working demo candidate, not a production release.")
    deck.metric(page, mm(9), mm(39), mm(73), "Training corpus", "1.55M", "group × 10-minute observations", tone=TEAL)
    deck.metric(page, mm(88), mm(39), mm(73), "Forecast WAPE", "7.63%", "macro held-out, 10–80 min", tone=PURPLE)
    deck.metric(page, mm(167), mm(39), mm(73), "Mean-pair UL gain", "10.52%", "30 matched static/MPC pairs", tone=GREEN)
    deck.metric(page, mm(246), mm(39), mm(80), "Severity-weighted gain", "2.84%", "tail risk still material", tone=AMBER)
    deck.card(page, mm(9), mm(76), mm(202), mm(90), fill=WHITE, accent=TEAL)
    deck.text(page, mm(16), mm(82), mm(185), mm(7), "What is implemented", 15, INK, bold=True)
    deck.bullet_list(page, mm(16), mm(94), mm(185), [
        ("Deterministic synthetic data factory", "30-second cohort simulation, 8 zones, 12 services, 24 UPFs, seeded surges and faults."),
        ("Trained probabilistic forecast bundle", "2,304 direct calendar-ridge models with p50/p90/p95 conformal envelopes."),
        ("Causal cohort-state MPC", "Twelve 10-minute windows, persistent-session state, static anchoring, same-state certificate and fallback."),
        ("Working dashboard demo", "FastAPI + React, REST/WebSocket snapshots, class telemetry, per-UPF routing proof and frozen evidence."),
    ], font_size=10.7, gap=17.4)
    deck.card(page, mm(219), mm(76), mm(107), mm(90), fill=PALE_AMBER, line=0xE8C994, accent=AMBER)
    deck.text(page, mm(226), mm(82), mm(91), mm(7), "Decision boundary", 15, INK, bold=True)
    deck.text(page, mm(226), mm(96), mm(90), mm(19),
              "The accepted claim is reduced modeled overload exposure for future sessions—not guaranteed overload prevention.",
              12.2, INK, bold=True)
    deck.bullet_list(page, mm(226), mm(121), mm(90), [
        "Worst paired scenario: −23.50%",
        "No established-session migration",
        "No live SMF/EMS actuation",
        "C-DOT telemetry and capacity calibration remain external",
    ], font_size=9.5, gap=9.5, bullet_color=AMBER)
    deck.footer(page, "docs/extreme-forecaster-v1-results.md · docs/cohort-mpc-full-campaign-results.md · README.md")

    # 3 — Review method
    page = deck.new_slide("Review basis: implementation, documentation, artifacts and executable checks", "Evidence standard")
    section_marker(deck, page, 1, "Implementation", "Simulator, forecasting, optimization, steering, API and React UI traced to source.", mm(14), mm(43), color=TEAL)
    section_marker(deck, page, 2, "Design records", "Architecture decisions, runbooks, traffic specification and C-DOT gap analysis reconciled.", mm(117), mm(43), color=PURPLE)
    section_marker(deck, page, 3, "Frozen results", "Forecast bundle, optimizer tuning, oracle bound and 30-pair campaign evidence reviewed.", mm(220), mm(43), color=GREEN)
    deck.card(page, mm(14), mm(83), mm(96), mm(70), fill=WHITE, accent=TEAL)
    deck.text(page, mm(21), mm(90), mm(80), mm(8), "90 / 90", 27, TEAL, bold=True)
    deck.text(page, mm(21), mm(101), mm(80), mm(6), "backend tests passed", 11, INK, bold=True)
    deck.bullet_list(page, mm(21), mm(115), mm(80), [
        "contracts and telemetry reconstruction",
        "deterministic simulator and Parquet",
        "forecast leakage and bundle integrity",
        "HiGHS, cohort MPC and oracle bounds",
        "API, story, rewind and campaign evidence",
    ], font_size=8.8, gap=7.1)
    deck.card(page, mm(118), mm(83), mm(96), mm(70), fill=WHITE, accent=GREEN)
    deck.text(page, mm(125), mm(90), mm(80), mm(8), "6 / 6", 27, GREEN, bold=True)
    deck.text(page, mm(125), mm(101), mm(80), mm(6), "demo preflight gates passed", 11, INK, bold=True)
    deck.bullet_list(page, mm(125), mm(115), mm(80), [
        "traffic registry",
        "3-UPF / 6-class scenario",
        "forecast bundle checksum",
        "FastAPI OpenAPI: 20 paths",
        "production-built operator console",
        "frontend bundle checksum",
    ], font_size=8.8, gap=6.6, bullet_color=GREEN)
    deck.card(page, mm(222), mm(83), mm(104), mm(70), fill=PALE_RED, line=0xECC5C1, accent=RED)
    deck.text(page, mm(229), mm(90), mm(90), mm(7), "Evidence rule", 14, RED, bold=True)
    deck.text(page, mm(229), mm(103), mm(88), mm(17),
              "Synthetic values, workstation benchmarks and external integration assumptions are never mixed into a production claim.",
              10.8, INK, bold=True)
    deck.text(page, mm(229), mm(128), mm(88), mm(15),
              "The worktree is currently dirty; frozen artifacts preserve hashes and exact code identities where documented.",
              9.2, SLATE)
    deck.footer(page, "env/bin/python -m unittest discover -s tests -v · env/bin/python scripts/preflight.py")

    # 4 — System flow
    page = deck.new_slide("The implementation is an artifact-backed, causal closed loop", "Architecture")
    boxes = [
        ("SCENARIO", "Seeded manifest\n+ topology + events", TEAL),
        ("SIMULATE", "30 s cohorts\n+ traffic accounting", TEAL_2),
        ("AGGREGATE", "20 ticks →\n10 min observations", PURPLE),
        ("FORECAST", "p50 / p90 / p95\n1–12 windows", PURPLE),
        ("OPTIMIZE", "Cohort-state MPC\n+ static certificate", GREEN),
        ("ACTUATE", "Weighted rendezvous\nnew sessions only", AMBER),
    ]
    start_x = 9
    for i, (head, body, color) in enumerate(boxes):
        x = start_x + i * 52.8
        deck.card(page, mm(x), mm(55), mm(46), mm(34), fill=WHITE, accent=color)
        deck.text(page, mm(x + 5), mm(61), mm(36), mm(5), head, 9.5, color, bold=True, font=MONO)
        deck.text(page, mm(x + 5), mm(69), mm(36), mm(14), body, 10.2, INK, bold=True)
        if i < len(boxes) - 1:
            deck.arrow(page, mm(x + 46), mm(72), mm(x + 52.8), color=SLATE)
    deck.line(page, mm(32), mm(105), mm(302), mm(105), TEAL, 1.2)
    deck.text(page, mm(10), mm(101), mm(22), mm(8), "OFFLINE", 8, TEAL, bold=True, font=MONO)
    deck.text(page, mm(307), mm(101), mm(19), mm(8), "LIVE", 8, TEAL, bold=True, font=MONO,
              align=ParagraphAdjust.RIGHT)
    deck.card(page, mm(9), mm(117), mm(92), mm(49), fill=WHITE, accent=TEAL)
    deck.text(page, mm(16), mm(123), mm(77), mm(5), "Artifact factory", 12.5, INK, bold=True)
    deck.text(page, mm(16), mm(132), mm(77), mm(25),
              "PBS or workstation shards publish immutable Parquet, JSONL, selection audits and metadata hashes. The cluster is not required during a demo.",
              9.3, SLATE)
    deck.card(page, mm(110), mm(117), mm(99), mm(49), fill=WHITE, accent=PURPLE)
    deck.text(page, mm(117), mm(123), mm(83), mm(5), "Control decision", 12.5, INK, bold=True)
    deck.text(page, mm(117), mm(132), mm(83), mm(25),
              "At a decision boundary, only closed history and declared known-at events are visible. The first MPC action is compared with static from the identical cohort state.",
              9.3, SLATE)
    deck.card(page, mm(218), mm(117), mm(108), mm(49), fill=WHITE, accent=GREEN)
    deck.text(page, mm(225), mm(123), mm(92), mm(5), "Presentation runtime", 12.5, INK, bold=True)
    deck.text(page, mm(225), mm(132), mm(92), mm(25),
              "One FastAPI process advances the simulator, emits ordered WebSocket deltas, serves the React console and retains an append-only audit trail.",
              9.3, SLATE)
    deck.footer(page, "docs/system-architecture-decisions.md · docs/end-to-end-runbook.md · demo_api/runtime.py")

    # 5 — Group model
    page = deck.new_slide("Traffic is modeled by controllable groups—not by anonymous aggregate load", "Stage 1 · Simulation data")
    deck.card(page, mm(9), mm(39), mm(131), mm(118), fill=WHITE, accent=TEAL)
    deck.text(page, mm(16), mm(45), mm(116), mm(7), "Population and dimensions", 15, INK, bold=True)
    deck.metric(page, mm(16), mm(57), mm(52), "Zones", "8", "urban · industrial · airport · stadium · rural", tone=TEAL)
    deck.metric(page, mm(75), mm(57), mm(57), "Services", "12", "consumer, enterprise, critical and IoT", tone=PURPLE)
    deck.metric(page, mm(16), mm(92), mm(52), "Groups", "96", "8 zones × 12 service profiles", tone=GREEN)
    deck.metric(page, mm(75), mm(92), mm(57), "Eligible UPFs", "6", "2 edge + 2 regional + 2 central", tone=AMBER)
    deck.text(page, mm(16), mm(129), mm(116), mm(6), "Forecast / QoS key", 9, MUTED, bold=True, font=MONO)
    deck.text(page, mm(16), mm(136), mm(116), mm(8), "(zone, DNN, S-NSSAI, 5QI)", 13.2, INK, bold=True, font=MONO)
    deck.text(page, mm(16), mm(148), mm(116), mm(7),
              "5QI is descriptive in v1; it is not assumed to be an independent SMF selection key.", 8.8, SLATE)
    deck.card(page, mm(149), mm(39), mm(177), mm(118), fill=WHITE, accent=PURPLE)
    deck.text(page, mm(156), mm(45), mm(162), mm(7), "Zone and service diversity", 15, INK, bold=True)
    zones = ["north-urban", "south-urban", "east-urban", "west-urban", "industrial", "airport", "stadium", "rural"]
    for i, zone in enumerate(zones):
        col = i % 2
        row = i // 2
        x = 156 + col * 77
        y = 59 + row * 13
        deck.card(page, mm(x), mm(y), mm(70), mm(10), fill=PALE_2, line=None)
        deck.circle(page, mm(x + 4), mm(y + 3), mm(3.2), TEAL if row < 2 else PURPLE)
        deck.text(page, mm(x + 10), mm(y + 2), mm(54), mm(5), zone, 9.1, INK, bold=True)
    deck.text(page, mm(156), mm(115), mm(154), mm(5), "Why grouping matters", 10.5, TEAL, bold=True)
    deck.bullet_list(page, mm(156), mm(125), mm(154), [
        "Forecasts remain local to an operationally meaningful class.",
        "UPF eligibility and path latency are enforced per group.",
        "Routing weights can shift one class without assuming every session can reach every UPF.",
    ], font_size=9.3, gap=9.2)
    deck.footer(page, "configs/extreme_training_profile.json · experiments/build_extreme_history_manifest.py · docs/extreme-data-spec-and-cdot-gap-analysis.md")

    # 6 — Probabilistic model
    page = deck.new_slide("The executable stochastic model is simple, reproducible and deliberately bounded", "Stage 1 · Probabilistic model")
    deck.card(page, mm(9), mm(40), mm(197), mm(55), fill=NAVY_2, line=None, accent=TEAL)
    deck.text(page, mm(18), mm(48), mm(178), mm(9), "N(g,t)  ~  Poisson( λg × Fg,t )", 25, WHITE, bold=True, font=MONO,
              align=ParagraphAdjust.CENTER)
    deck.text(page, mm(18), mm(66), mm(178), mm(7),
              "Fg,t = daily profile × weekend factor × U(0.86, 1.16) weekly noise × active surge",
              10.8, 0xC5D7DD, align=ParagraphAdjust.CENTER, font=MONO)
    deck.text(page, mm(18), mm(81), mm(178), mm(5),
              "Factors update hourly; arrivals are drawn every 30 seconds.", 9.2, CYAN,
              align=ParagraphAdjust.CENTER, bold=True)
    deck.card(page, mm(214), mm(40), mm(112), mm(55), fill=WHITE, accent=GREEN)
    deck.text(page, mm(221), mm(47), mm(98), mm(6), "Determinism by construction", 13, INK, bold=True)
    deck.bullet_list(page, mm(221), mm(60), mm(97), [
        "manifest seed fixes events",
        "SHA-256-derived random stream per group",
        "independent arrival and lifetime streams",
        "same seed + policy ⇒ same logical run",
    ], font_size=9.2, gap=7.4, bullet_color=GREEN)
    columns = [
        ("SESSION LIFETIME", "Discrete uniform integer draw within each class range."),
        ("SESSION RATE", "Fixed UL/DL Mbps per active session for a class."),
        ("STATE", "Placed sessions become aggregate cohorts with explicit departure steps."),
        ("LOAD", "Offered demand is the sum of fixed rates for all active sessions."),
    ]
    for i, (head, body) in enumerate(columns):
        x = 9 + i * 79.3
        deck.card(page, mm(x), mm(104), mm(72), mm(45), fill=WHITE, accent=[TEAL, PURPLE, GREEN, AMBER][i])
        deck.text(page, mm(x + 6), mm(111), mm(59), mm(5), head, 8.2, [TEAL, PURPLE, GREEN, AMBER][i], bold=True, font=MONO)
        deck.text(page, mm(x + 6), mm(122), mm(59), mm(20), body, 9.6, INK, bold=True)
    deck.card(page, mm(9), mm(156), mm(317), mm(16), fill=PALE_AMBER, line=0xE7CA99, accent=AMBER)
    deck.text(page, mm(16), mm(160), mm(303), mm(8),
              "Not implemented in the extreme generator: AR(1), Markov bursts, heavy-tailed rate/lifetime, persistent UE identity, mobility, packet-level traffic or telemetry faults.",
              9.2, INK, bold=True)
    deck.footer(page, "simulator/macro/engine.py::_poisson · configs/extreme_training_profile.json · docs/traffic-model-spec.md")

    # 7 — Service catalog
    page = deck.new_slide("Twelve service classes create distinct temporal and directional demand", "Stage 1 · Traffic catalog",
                          subtitle="Values are per active session; lifetime ranges are from the executable extreme profile.")
    rows = [["SERVICE / DNN", "5QI", "ARRIVALS / 30 s / ZONE", "LIFETIME", "UL / DL Mbps", "CALENDAR"]]
    service_rows = [
        ["Consumer video", "9", "201.6–403.2", "1–12 h", "0.15 / 4.00", "Evening"],
        ["Social / live", "8", "112.0–224.0", "10 m–2 h", "2.00 / 1.00", "Evening"],
        ["Gaming", "3", "134.4–268.8", "40 m–6 h", "0.20 / 0.40", "Late"],
        ["IMS voice", "1", "89.6–179.2", "10 m–4 h", "0.08 / 0.08", "Commute"],
        ["Enterprise", "7", "112.0–224.0", "30 m–8 h", "1.00 / 2.00", "Business"],
        ["Video conference", "2", "89.6–179.2", "30 m–6 h", "1.50 / 1.50", "Business"],
        ["Industrial URLLC", "82", "67.2–134.4", "4–24 h", "0.10 / 0.08", "Industrial"],
        ["Massive IoT", "9", "224.0–448.0", "6–48 h", "0.020 / 0.004", "Flat"],
        ["Connected vehicle", "84", "112.0–224.0", "5 m–1 h", "0.50 / 1.00", "Commute"],
        ["Edge AI", "7", "56.0–112.0", "10 m–2 h", "3.00 / 6.00", "Business"],
        ["Cloud backup", "9", "22.4–44.8", "1–12 h", "8.00 / 2.00", "Overnight"],
        ["Public safety", "65", "33.6–67.2", "20 m–3 h20", "2.00 / 2.00", "Flat"],
    ]
    rows.extend(service_rows)
    deck.table(page, mm(9), mm(39), [mm(78), mm(20), mm(54), mm(42), mm(50), mm(50)], rows,
               row_h=9.6, font_size=8.5,
               alignments=[ParagraphAdjust.LEFT, ParagraphAdjust.CENTER, ParagraphAdjust.RIGHT,
                           ParagraphAdjust.CENTER, ParagraphAdjust.CENTER, ParagraphAdjust.LEFT])
    deck.card(page, mm(9), mm(166), mm(317), mm(9), fill=PALE_TEAL, line=None, accent=TEAL)
    deck.text(page, mm(16), mm(168), mm(303), mm(5),
              "The same arrival count drives sessions, UL and DL via fixed class rates; identical target WAPE does not imply three independent traffic processes.",
              8.4, INK, bold=True)
    deck.footer(page, "configs/extreme_training_profile.json · docs/extreme-data-spec-and-cdot-gap-analysis.md §4")

    # 8 — Scenario catalog
    page = deck.new_slide("Scenario generation covers normal regimes, random surges and network disturbances", "Stage 1 · Scenario catalogue")
    events = [
        ("DAILY / WEEKLY", "258,048 factors", "service curve × weekend × group/week noise", "demand seasonality", TEAL),
        ("FLASH CROWDS", "192 episodes", "12/week · random group · 2–10 h · ×2.5–8.0", "localized step surges", PURPLE),
        ("BROWNOUTS", "128 episodes", "8/week · 1–8 h · UL ×0.18–0.70", "capacity, queue, drops", AMBER),
        ("NEAR OUTAGES", "48 episodes", "3/week · 1–4 h · degraded + 1% capacity", "severe residual stress", RED),
        ("LATENCY", "80 episodes", "5/week · 1–6 h · +25–140 ms", "path-cost disturbance", GREEN),
    ]
    for i, (head, count, sampling, effect, color) in enumerate(events):
        y = 40 + i * 25.4
        deck.card(page, mm(9), mm(y), mm(317), mm(20), fill=WHITE, accent=color)
        deck.text(page, mm(17), mm(y + 4), mm(50), mm(5), head, 8.7, color, bold=True, font=MONO)
        deck.text(page, mm(71), mm(y + 4), mm(45), mm(5), count, 11, INK, bold=True)
        deck.text(page, mm(121), mm(y + 4), mm(125), mm(5), sampling, 9.4, INK, bold=True)
        deck.text(page, mm(252), mm(y + 4), mm(65), mm(5), effect, 8.9, SLATE)
    deck.card(page, mm(9), mm(169), mm(317), mm(7.5), fill=PALE_AMBER, line=None, accent=AMBER)
    deck.text(page, mm(16), mm(170.4), mm(303), mm(4),
              "Surge IDs are not stored in run.parquet; their multipliers are folded into hourly arrival-factor events. Fault effects appear as resulting UPF state.",
              7.8, INK, bold=True)
    deck.footer(page, "experiments/build_extreme_history_manifest.py::_surges/_fault_events · docs/extreme-data-spec-and-cdot-gap-analysis.md §6")

    # 9 — Topology
    page = deck.new_slide("Eligibility and directional safety envelopes constrain every placement", "Stage 1 · UPF topology")
    # logical topology
    deck.card(page, mm(9), mm(39), mm(150), mm(126), fill=WHITE, accent=TEAL)
    deck.text(page, mm(16), mm(46), mm(136), mm(6), "24-UPF logical hierarchy", 14, INK, bold=True)
    deck.text(page, mm(16), mm(55), mm(136), mm(6), "One traffic group sees exactly six eligible destinations", 9.2, SLATE)
    tiers = [
        ("ZONE", ["north", "south", "east", "west", "industrial", "airport", "stadium", "rural"], TEAL),
        ("EDGE", ["2 per zone"], TEAL_2),
        ("REGIONAL", ["4 total"], PURPLE),
        ("CENTRAL", ["4 total"], GREEN),
    ]
    y_positions = [70, 93, 116, 139]
    for (label, values, color), y in zip(tiers, y_positions):
        deck.text(page, mm(18), mm(y + 2), mm(26), mm(4), label, 7.5, color, bold=True, font=MONO)
        if label == "ZONE":
            for j, value in enumerate(values):
                x = 47 + (j % 4) * 25
                yy = y + (j // 4) * 9
                deck.card(page, mm(x), mm(yy), mm(21), mm(7), fill=PALE_2, line=None)
                deck.text(page, mm(x + 1), mm(yy + 1.2), mm(19), mm(3.5), value, 6.8, INK,
                          align=ParagraphAdjust.CENTER)
        else:
            deck.card(page, mm(47), mm(y), mm(82), mm(11), fill=PALE_2, line=None, accent=color)
            deck.text(page, mm(54), mm(y + 2.8), mm(68), mm(4), values[0], 9.8, INK, bold=True)
            deck.text(page, mm(132), mm(y + 1.7), mm(16), mm(6), "→", 18, color, bold=True,
                      align=ParagraphAdjust.CENTER)
    deck.card(page, mm(168), mm(39), mm(158), mm(126), fill=WHITE, accent=PURPLE)
    deck.text(page, mm(175), mm(46), mm(144), mm(6), "Per-instance capacity envelope", 14, INK, bold=True)
    cap_rows = [
        ["TIER", "COUNT", "UL", "DL", "SESSION CAP", "SAFE"],
        ["Edge A", "8", "480 G", "1.44 T", "1.44 M", "78% / 82%"],
        ["Edge B", "8", "560 G", "1.28 T", "1.44 M", "78% / 82%"],
        ["Regional", "4", "1.12 T", "2.40 T", "3.52 M", "80% / 85%"],
        ["Central", "4", "1.92 T", "3.84 T", "5.12 M", "75% / 85%"],
        ["TOTAL", "24", "20.48 T", "46.72 T", "57.60 M", "mixed"],
    ]
    deck.table(page, mm(175), mm(59), [mm(35), mm(18), mm(25), mm(26), mm(31), mm(33)], cap_rows,
               row_h=11.0, font_size=7.6,
               alignments=[ParagraphAdjust.LEFT, ParagraphAdjust.CENTER, ParagraphAdjust.RIGHT,
                           ParagraphAdjust.RIGHT, ParagraphAdjust.RIGHT, ParagraphAdjust.CENTER])
    deck.text(page, mm(175), mm(131), mm(142), mm(25),
              "Admission filters: health ∈ {healthy, degraded} · group eligible · locality path exists · optional max latency · session capacity available.",
              9.4, INK, bold=True)
    deck.footer(page, "experiments/build_extreme_history_manifest.py::_upfs/_eligible_upfs · simulator/macro/config.py")

    # 10 — Simulator loop
    page = deck.new_slide("Each 30-second tick preserves causal order and all traffic accounting states", "Stage 1 · Simulator mechanics")
    steps = [
        ("1", "CLOSE", "At a 10-minute boundary, close the prior 20-tick history bucket."),
        ("2", "EVENTS", "Apply arrival, capacity, health and latency events for this tick."),
        ("3", "POLICY", "Replan if due using current state and only causal history."),
        ("4", "ARRIVALS", "Draw exact Poisson arrivals per group from independent streams."),
        ("5", "SELECT", "Filter eligible/healthy UPFs and run weighted rendezvous per session."),
        ("6", "ADMIT", "Check session capacity; draw uniform lifetime and create a cohort."),
        ("7", "SERVE", "Apply capacity, queues and drops separately for UL and DL."),
        ("8", "EMIT", "Write step telemetry, audit samples and boundary group/UPF state."),
    ]
    for i, (num, head, body) in enumerate(steps):
        col = i % 2
        row = i // 2
        x = 9 + col * 160.5
        y = 41 + row * 31
        deck.card(page, mm(x), mm(y), mm(151), mm(25), fill=WHITE, accent=TEAL if col == 0 else PURPLE)
        deck.circle(page, mm(x + 6), mm(y + 6), mm(12), TEAL if col == 0 else PURPLE)
        deck.text(page, mm(x + 6), mm(y + 9), mm(12), mm(5), num, 10, WHITE, bold=True,
                  align=ParagraphAdjust.CENTER, font=MONO)
        deck.text(page, mm(x + 23), mm(y + 5), mm(35), mm(5), head, 8.4,
                  TEAL if col == 0 else PURPLE, bold=True, font=MONO)
        deck.text(page, mm(x + 58), mm(y + 4), mm(85), mm(15), body, 8.7, INK)
    deck.card(page, mm(9), mm(168), mm(317), mm(8), fill=PALE_TEAL, line=None, accent=TEAL)
    deck.text(page, mm(16), mm(169.5), mm(303), mm(4),
              "Traffic is recorded as offered → carried / queued / dropped, while admission failures and unplaced rejections remain separate.",
              8.2, INK, bold=True)
    deck.footer(page, "simulator/macro/engine.py::Simulator.advance · simulator/macro/model.py · steering/hashing.py")

    # 11 — Data scale and artifacts
    page = deck.new_slide("The 16-week extreme campaign is large enough to stress the full offline path", "Stage 1 · Dataset and artifacts")
    deck.metric(page, mm(9), mm(39), mm(74), "Duration", "112 d", "322,560 × 30-second ticks", tone=TEAL)
    deck.metric(page, mm(89), mm(39), mm(74), "Training rows", "1.548 M", "96 groups × 16,128 buckets", tone=PURPLE)
    deck.metric(page, mm(169), mm(39), mm(74), "Projected sessions", "4.39 B", "routed arrivals; audit sampled 1:5,000", tone=GREEN)
    deck.metric(page, mm(249), mm(39), mm(77), "Artifacts", "≈9.6 GiB", "one 112-day extreme shard", tone=AMBER)
    artifact_rows = [
        ["ARTIFACT", "GRANULARITY", "WHAT IT PROVES"],
        ["run.parquet", "1 row / 30 s", "group arrivals + nested per-UPF UL/DL/session state"],
        ["group_upf_buckets[]", "10 min boundary", "joint class/UPF active, admitted and rejected state"],
        ["selection-audits.parquet", "1 / 5,000 sessions", "hashed key, eligible set, weights and selected UPF"],
        ["run.jsonl", "readable adapter", "metadata + steps + retained selection audits"],
        ["metadata.json", "1 / shard", "hashes, source identity, host/job and aggregate metrics"],
        ["forecast bundle", "1 immutable JSON", "2,304 models, metrics, calibration and checksum"],
    ]
    deck.table(page, mm(9), mm(77), [mm(73), mm(55), mm(189)], artifact_rows,
               row_h=12.0, font_size=8.6)
    deck.card(page, mm(9), mm(165), mm(317), mm(11), fill=PALE_AMBER, line=None, accent=AMBER)
    deck.text(page, mm(16), mm(167.5), mm(303), mm(6),
              "Measured one-day calibration: 5:06 wall time · 588 MiB peak RSS · 87.6 MiB output. A 112-day shard projects to ~9 h 32 m and ~64 GiB retained memory.",
              8.5, INK, bold=True)
    deck.footer(page, "docs/extreme-data-spec-and-cdot-gap-analysis.md §§3,8 · simulator/macro/engine.py::write_parquet")

    # 12 — Forecast pipeline
    page = deck.new_slide("Training converts canonical 30-second Parquet into leakage-safe direct forecasts", "Stage 2 · Forecaster training")
    pipeline = [
        ("20 TICKS", "sum group arrivals", TEAL),
        ("10 MIN", "mean UPF residual state", TEAL_2),
        ("OBSERVATION", "sessions + UL + DL", PURPLE),
        ("DIRECT MODELS", "3 targets × 8 horizons", PURPLE),
        ("CONFORMAL", "p50 + p90 / p95 widths", GREEN),
        ("BUNDLE", "checksum + metadata", AMBER),
    ]
    for i, (head, body, color) in enumerate(pipeline):
        x = 9 + i * 52.8
        deck.card(page, mm(x), mm(49), mm(46), mm(32), fill=WHITE, accent=color)
        deck.text(page, mm(x + 5), mm(55), mm(36), mm(5), head, 8.2, color, bold=True, font=MONO)
        deck.text(page, mm(x + 5), mm(65), mm(36), mm(10), body, 9.2, INK, bold=True)
        if i < len(pipeline) - 1:
            deck.arrow(page, mm(x + 46), mm(65), mm(x + 52.8), color=SLATE)
    deck.card(page, mm(9), mm(93), mm(151), mm(66), fill=WHITE, accent=PURPLE)
    deck.text(page, mm(16), mm(100), mm(136), mm(6), "Exact training row", 13.5, INK, bold=True)
    deck.bullet_list(page, mm(16), mm(112), mm(136), [
        "new_session_count = Σ arrivals in the 20 ticks",
        "new UL/DL = count × fixed class Mbps/session",
        "residual sessions and offered Mbps = duration mean across the bucket",
        "one DemandObservation emitted per controllable group",
    ], font_size=9.4, gap=9.0)
    deck.card(page, mm(169), mm(93), mm(157), mm(66), fill=WHITE, accent=AMBER)
    deck.text(page, mm(176), mm(100), mm(142), mm(6), "What the v1 trainer does not consume", 13.5, INK, bold=True)
    deck.bullet_list(page, mm(176), mm(112), mm(142), [
        "queue, drop or rejection fields as targets",
        "selection audits or explicit event IDs",
        "packet counters, CPU, memory or resets/gaps",
        "per-group measured carried throughput",
    ], font_size=9.4, gap=9.0, bullet_color=AMBER)
    deck.footer(page, "experiments/train_forecaster.py::_bucket_sequence · forecasting/bundle.py")

    # 13 — Forecast model and optimization
    page = deck.new_slide("The chosen forecaster favors transparency, speed and numerical stability", "Stage 2 · Model and training optimizations")
    deck.card(page, mm(9), mm(39), mm(170), mm(130), fill=WHITE, accent=PURPLE)
    deck.text(page, mm(16), mm(46), mm(156), mm(6), "Calendar-ridge direct multi-horizon model", 15, INK, bold=True)
    feature_rows = [
        ["FEATURE", "ROLE"],
        ["last value", "autoregressive level"],
        ["rolling mean (6)", "one-hour causal baseline"],
        ["recent trend", "local direction"],
        ["daily seasonal lag", "same-time prior-day structure"],
        ["sin/cos time of day", "continuous daily phase"],
        ["sin/cos day of week", "weekly calendar phase"],
        ["intercept", "group/horizon baseline"],
    ]
    deck.table(page, mm(16), mm(59), [mm(66), mm(89)], feature_rows, row_h=10.4, font_size=8.5)
    deck.text(page, mm(16), mm(149), mm(155), mm(12),
              "96 groups × 3 targets × 8 horizons = 2,304 nine-feature ridge systems",
              10.1, PURPLE, bold=True)
    deck.card(page, mm(188), mm(39), mm(138), mm(130), fill=WHITE, accent=GREEN)
    deck.text(page, mm(195), mm(46), mm(124), mm(6), "Engineering optimizations", 15, INK, bold=True)
    deck.bullet_list(page, mm(195), mm(59), mm(124), [
        ("Vectorized row construction", "NumPy builds each sequence in O(n); the prior scalar path rebuilt full histories."),
        ("RMS feature scaling", "stabilizes national-scale normal equations while preserving the bundle coefficient contract."),
        ("Ordered split", "70% train / 15% calibration / 15% test; features never cross the forecast origin."),
        ("Median bias correction", "calibration residual median shifts the non-negative p50 point forecast."),
        ("Split conformal + ACI", "residual widths provide p90/p95; adaptive alpha reacts to realized coverage."),
    ], font_size=8.9, gap=17.0, bullet_color=GREEN)
    deck.card(page, mm(188), mm(145), mm(138), mm(17), fill=PALE_GREEN, line=None, accent=GREEN)
    deck.text(page, mm(195), mm(149), mm(124), mm(8),
              "3:03 total · ~13 s fitting · ~1.8 GiB peak RSS · GPU unnecessary for v1",
              9.2, INK, bold=True)
    deck.footer(page, "forecasting/bundle.py::_sequence_training_rows/_fit_direct_model · logs/extreme-forecaster-training-optimized.log")

    # 14 — Forecast results
    page = deck.new_slide("Held-out performance is strong at operational horizons and degrades predictably with lead time", "Stage 2 · Forecast results")
    horizons = [10, 20, 30, 40, 50, 60, 70, 80]
    wape = [4.48, 6.09, 7.35, 7.95, 7.42, 7.63, 9.34, 10.73]
    p90 = [94.84, 94.96, 94.92, 94.33, 93.88, 93.10, 93.67, 93.97]
    deck.card(page, mm(9), mm(39), mm(205), mm(116), fill=WHITE, accent=PURPLE)
    deck.text(page, mm(16), mm(46), mm(190), mm(6), "Macro WAPE by horizon", 14, INK, bold=True)
    chart_x, chart_y, chart_w, chart_h = mm(25), mm(65), mm(172), mm(65)
    for pct in [0, 3, 6, 9, 12]:
        yy = chart_y + chart_h - chart_h * pct / 12
        deck.line(page, chart_x, yy, chart_x + chart_w, yy, GRID, 0.25)
        deck.text(page, mm(13), yy - mm(2), mm(10), mm(4), f"{pct}%", 7, MUTED,
                  align=ParagraphAdjust.RIGHT, font=MONO)
    points = []
    for i, (horizon, value) in enumerate(zip(horizons, wape)):
        x = chart_x + chart_w * i / (len(horizons) - 1)
        y = chart_y + chart_h - chart_h * value / 12
        points.append((x, y))
        if i:
            deck.line(page, points[i - 1][0], points[i - 1][1], x, y, PURPLE, 1.2)
        deck.circle(page, x - mm(2), y - mm(2), mm(4), PURPLE)
        deck.text(page, x - mm(8), chart_y + chart_h + mm(4), mm(16), mm(4), f"{horizon}m", 7.5,
                  MUTED, align=ParagraphAdjust.CENTER, font=MONO)
        deck.text(page, x - mm(8), y - mm(7), mm(16), mm(4), f"{value:.2f}", 7.1, PURPLE,
                  bold=True, align=ParagraphAdjust.CENTER, font=MONO)
    deck.text(page, mm(16), mm(142), mm(190), mm(5),
              "p90 upper-bound coverage stays between 93.10% and 94.96% across all eight horizons.", 8.8, SLATE)
    deck.card(page, mm(223), mm(39), mm(103), mm(116), fill=WHITE, accent=TEAL)
    deck.text(page, mm(230), mm(46), mm(89), mm(6), "Fair baseline comparison", 14, INK, bold=True)
    methods = [("Calendar ridge", 7.63, PURPLE), ("Daily seasonal", 13.71, TEAL_2), ("MA(6)", 14.30, AMBER)]
    for i, (label, value, color) in enumerate(methods):
        y = 66 + i * 25
        deck.text(page, mm(230), mm(y), mm(82), mm(5), label, 9.5, INK, bold=True)
        deck.bar(page, mm(230), mm(y + 7), mm(63), mm(5), value, 15, color=color, label=f"{value:.2f}%")
    deck.text(page, mm(230), mm(139), mm(88), mm(12),
              "44.36% lower WAPE than daily seasonal naive.", 10, GREEN, bold=True)
    deck.footer(page, "docs/extreme-forecaster-v1-results.md §§3–4 · output/models/extreme-forecaster-v1-baseline-evaluation.json")

    # 15 — Regime limits
    page = deck.new_slide("Surges—not brownouts—are the dominant forecast weakness", "Stage 2 · Regime analysis")
    regime_rows = [
        ["REGIME", "WAPE", "P90 COVERAGE", "INTERPRETATION"],
        ["Normal", "6.51%", "94.19%", "well calibrated"],
        ["Surge", "11.08%", "30.73%", "unannounced step-change is not predictable beforehand"],
        ["Brownout", "5.09%", "87.88%", "network state changes; offered demand does not"],
        ["Near outage", "5.29%", "73.96%", "demand accuracy stays normal; coverage weakens"],
        ["Latency incident", "5.04%", "92.36%", "path state changes, not demand"],
    ]
    fills = [NAVY_2, WHITE, PALE_RED, WHITE, WHITE, WHITE]
    deck.table(page, mm(9), mm(42), [mm(60), mm(38), mm(48), mm(171)], regime_rows,
               row_h=15.2, font_size=9.2, fills=fills,
               alignments=[ParagraphAdjust.LEFT, ParagraphAdjust.RIGHT, ParagraphAdjust.RIGHT, ParagraphAdjust.LEFT])
    deck.card(page, mm(9), mm(141), mm(152), mm(28), fill=PALE_AMBER, line=0xE8CA99, accent=AMBER)
    deck.text(page, mm(16), mm(147), mm(137), mm(5), "Operational consequence", 10, AMBER, bold=True, font=MONO)
    deck.text(page, mm(16), mm(155), mm(137), mm(10),
              "The controller needs robust fallback and static-relative certification; forecast accuracy alone cannot guarantee benefit.",
              9.4, INK, bold=True)
    deck.card(page, mm(169), mm(141), mm(157), mm(28), fill=PALE_TEAL, line=0xBADCE3, accent=TEAL)
    deck.text(page, mm(176), mm(147), mm(142), mm(5), "Evaluation caveat", 10, TEAL, bold=True, font=MONO)
    deck.text(page, mm(176), mm(155), mm(142), mm(10),
              "The v1 70/15/15 split differs slightly from the manifest’s explicit 11/2/3-week boundary; event-stratified release reporting remains incomplete.",
              9.0, INK, bold=True)
    deck.footer(page, "docs/extreme-optimizer-tuning-results.md §Forecast performance by regime · docs/extreme-forecaster-v1-results.md")

    # 16 — Optimizer evolution
    page = deck.new_slide("The optimizer evolved only after the evidence disproved the first design", "Stage 3 · Controller evolution")
    stages = [
        ("ONE-WINDOW LP", "p95 forecast + residual load", "Validation failed", RED),
        ("MECHANISM TUNING", "hints · anomaly · lifetime · caps", "Still worse than static", RED),
        ("ORACLE BOUND", "full-day cohort relaxation", "100% modeled UL reduction possible", PURPLE),
        ("COHORT MPC", "12-window state transition", "10.52% mean-pair gain", GREEN),
    ]
    for i, (head, body, outcome, color) in enumerate(stages):
        x = 9 + i * 79.3
        deck.card(page, mm(x), mm(51), mm(70), mm(54), fill=WHITE, accent=color)
        deck.text(page, mm(x + 6), mm(58), mm(58), mm(5), f"0{i + 1} · {head}", 8.2, color, bold=True, font=MONO)
        deck.text(page, mm(x + 6), mm(70), mm(58), mm(12), body, 11, INK, bold=True)
        deck.text(page, mm(x + 6), mm(90), mm(58), mm(8), outcome, 9.2, color, bold=True)
        if i < len(stages) - 1:
            deck.arrow(page, mm(x + 70), mm(77), mm(x + 79.3), color=SLATE)
    deck.card(page, mm(9), mm(119), mm(317), mm(46), fill=NAVY_2, line=None, accent=PURPLE)
    deck.text(page, mm(18), mm(126), mm(299), mm(6), "Core lesson", 9.3, CYAN, bold=True, font=MONO)
    deck.text(page, mm(18), mm(138), mm(299), mm(13),
              "New-session decisions persist for hours. A controller that optimizes only the next bucket can create residual concentrations that it cannot later undo.",
              16.5, WHITE, bold=True, align=ParagraphAdjust.CENTER)
    deck.text(page, mm(18), mm(155), mm(299), mm(5),
              "The successful redesign carried cohort survival through a two-hour horizon and compared every candidate with static from the same state.",
              9.5, 0xC1D2D9, align=ParagraphAdjust.CENTER)
    deck.footer(page, "docs/extreme-optimizer-tuning-results.md · docs/extreme-oracle-bound-results.md · docs/cohort-mpc-full-campaign-results.md")

    # 17 — Why LP failed
    page = deck.new_slide("Why the one-window HiGHS controller lost to a strong static baseline", "Stage 3 · Failure analysis")
    deck.card(page, mm(9), mm(39), mm(151), mm(129), fill=WHITE, accent=RED)
    deck.text(page, mm(16), mm(46), mm(136), mm(6), "Mechanism of failure", 15, INK, bold=True)
    deck.bullet_list(page, mm(16), mm(60), mm(136), [
        ("Myopic horizon", "Minimizing next-window peak utilization ignores the future occupancy of long-lived cohorts."),
        ("Persistent placement", "Only future sessions are controllable; already placed sessions cannot be rebalanced."),
        ("Correlated concentration", "A per-group weight cap does not prevent many groups from choosing the same failure domain."),
        ("Forecast step-changes", "Surge p90 coverage collapses before an unannounced jump becomes observable."),
        ("Static is robust", "Capacity-weighted rendezvous spreads each group broadly and continuously."),
    ], font_size=9.4, gap=20.0, bullet_color=RED)
    deck.card(page, mm(169), mm(39), mm(157), mm(129), fill=WHITE, accent=AMBER)
    deck.text(page, mm(176), mm(46), mm(142), mm(6), "Validation outcome versus static", 15, INK, bold=True)
    tuning = [
        ("Responsive gate p95", -7.11),
        ("Static anchor · 10%", -9.12),
        ("Default p95", -38.98),
        ("Load-only aggressive", -76.53),
        ("Load-first · +35% safety", -93.81),
        ("Load-first · +15% safety", -168.04),
    ]
    max_abs = 170
    for i, (label, value) in enumerate(tuning):
        y = 63 + i * 14
        deck.text(page, mm(176), mm(y), mm(74), mm(5), label, 8.2, INK, bold=True)
        deck.rect(page, mm(252), mm(y + 1), mm(58), mm(5), PALE_2, line=None, radius=1)
        width = mm(58) * abs(value) / max_abs
        deck.rect(page, mm(252), mm(y + 1), width, mm(5), RED, line=None, radius=1)
        deck.text(page, mm(312), mm(y), mm(9), mm(5), f"{value:.1f}%", 7.6, RED, bold=True,
                  align=ParagraphAdjust.RIGHT, font=MONO)
    deck.text(page, mm(176), mm(151), mm(142), mm(10),
              "No profile passed guardrails; reserved fresh test seeds were preserved rather than used for selection.",
              9.3, RED, bold=True)
    deck.footer(page, "docs/static-controller-deep-research.md · docs/extreme-optimizer-tuning-results.md")

    # 18 — MPC details
    page = deck.new_slide("Cohort-state MPC plans the consequences of today’s weights across twelve windows", "Stage 3 · Cohort MPC")
    deck.card(page, mm(9), mm(39), mm(205), mm(128), fill=WHITE, accent=GREEN)
    deck.text(page, mm(16), mm(46), mm(190), mm(6), "State transition model", 15, INK, bold=True)
    deck.text(page, mm(16), mm(59), mm(190), mm(13),
              "Projected load(u,t) = anchored cohorts(u,t) + Σg,τ≤t  x(g,τ,u) · demand(g,τ) · survival(g,t−τ)",
              12.1, NAVY_2, bold=True, font=MONO, align=ParagraphAdjust.CENTER)
    mpc_items = [
        ("HORIZON", "12 × 10 min = 2 hours"),
        ("DEMAND", "causal MA(6) p95; scheduled multipliers only after known_at_step"),
        ("STATE", "exact active cohorts by group, UPF, remaining lifetime and directional rate"),
        ("CAPACITY PATH", "current state + declared future health/capacity events"),
        ("DECISION", "continuous group→UPF weights for each horizon window"),
        ("ACTION", "only the first window is published; replan every 10 minutes"),
    ]
    for i, (head, body) in enumerate(mpc_items):
        col = i % 2
        row = i // 2
        x = 16 + col * 94
        y = 83 + row * 23
        deck.text(page, mm(x), mm(y), mm(27), mm(4), head, 7.4, GREEN, bold=True, font=MONO)
        deck.text(page, mm(x + 29), mm(y - 1), mm(60), mm(11), body, 8.7, INK, bold=True)
    deck.card(page, mm(223), mm(39), mm(103), mm(128), fill=NAVY_2, line=None, accent=PURPLE)
    deck.text(page, mm(230), mm(47), mm(89), mm(6), "Frozen demo profile", 14, WHITE, bold=True)
    profile = [
        ("max UPF weight", "0.75"),
        ("static blend", "50%"),
        ("solver timeout", "2.0 s"),
        ("overload cost", "1×"),
        ("physical drop cost", "10×"),
        ("terminal exposure", "1×"),
        ("unplanned capacity", "fallback static"),
    ]
    for i, (label, value) in enumerate(profile):
        y = 64 + i * 12.5
        deck.text(page, mm(230), mm(y), mm(53), mm(4), label.upper(), 7.1, 0x9FB5BF, bold=True, font=MONO)
        deck.text(page, mm(284), mm(y - 1), mm(35), mm(5), value, 10, WHITE, bold=True,
                  align=ParagraphAdjust.RIGHT)
        deck.line(page, mm(230), mm(y + 7), mm(319), mm(y + 7), 0x395562, 0.25)
    deck.footer(page, "optimization/cohort_mpc.py · simulator/macro/controllers.py::CohortMPCController · configs/cohort_mpc_pilot_10pct_v2.json")

    # 19 — Certificate
    page = deck.new_slide("Every MPC action is guarded by a same-state static certificate", "Stage 3 · Safety and fallback")
    x_positions = [9, 78, 147, 216, 285]
    flow = [
        ("FORECAST", "12-window demand", PURPLE),
        ("SOLVE", "HiGHS LP", GREEN),
        ("REPLAY", "static from same state", TEAL),
        ("CERTIFY", "guardrails + gain", AMBER),
        ("PUBLISH", "or exact static", RED),
    ]
    for i, ((head, body, color), x) in enumerate(zip(flow, x_positions)):
        deck.circle(page, mm(x), mm(51), mm(18), color)
        deck.text(page, mm(x), mm(56), mm(18), mm(5), f"{i + 1}", 10, WHITE, bold=True,
                  align=ParagraphAdjust.CENTER, font=MONO)
        deck.text(page, mm(x - 4), mm(73), mm(26), mm(5), head, 8, color, bold=True,
                  align=ParagraphAdjust.CENTER, font=MONO)
        deck.text(page, mm(x - 10), mm(81), mm(38), mm(8), body, 8.4, INK, bold=True,
                  align=ParagraphAdjust.CENTER)
        if i < len(flow) - 1:
            deck.arrow(page, mm(x + 18), mm(60), mm(x_positions[i + 1]), color=SLATE)
    deck.card(page, mm(9), mm(103), mm(152), mm(62), fill=WHITE, accent=GREEN)
    deck.text(page, mm(16), mm(110), mm(137), mm(6), "Certificate requires", 14, INK, bold=True)
    deck.bullet_list(page, mm(16), mm(123), mm(137), [
        "UL overload area no worse than static",
        "DL overload area no worse than static",
        "session overload no worse than static",
        "UL and DL physical drops no worse than static",
        "minimum score and modeled UL improvement",
    ], font_size=9, gap=7.6, bullet_color=GREEN)
    deck.card(page, mm(170), mm(103), mm(156), mm(62), fill=WHITE, accent=RED)
    deck.text(page, mm(177), mm(110), mm(141), mm(6), "Fallback triggers", 14, INK, bold=True)
    deck.bullet_list(page, mm(177), mm(123), mm(141), [
        "insufficient history or forecast error",
        "solver infeasible / error / timeout",
        "certificate rejects the candidate",
        "observed unplanned capacity state",
        "no healthy eligible route exists",
    ], font_size=9, gap=7.6, bullet_color=RED)
    deck.footer(page, "optimization/cohort_mpc.py::_certificate · simulator/macro/controllers.py::_static_fallback · steering/policy.py")

    # 20 — Campaign results
    page = deck.new_slide("The frozen 30-pair campaign passes the working-demo gate", "Stage 3 · Evaluation results")
    deck.metric(page, mm(9), mm(39), mm(75), "Mean-pair UL", "+10.52%", "bootstrap 95% CI: 4.81–16.93%", tone=GREEN)
    deck.metric(page, mm(91), mm(39), mm(75), "Severity-weighted UL", "+2.84%", "dominated by highest-overload days", tone=AMBER)
    deck.metric(page, mm(173), mm(39), mm(75), "UL dropped bytes", "+12.42%", "aggregate reduction", tone=TEAL)
    deck.metric(page, mm(255), mm(39), mm(71), "DL dropped bytes", "+9.34%", "aggregate reduction", tone=PURPLE)
    deck.card(page, mm(9), mm(80), mm(194), mm(80), fill=WHITE, accent=GREEN)
    deck.text(page, mm(16), mm(87), mm(180), mm(6), "Exactly paired experiment", 14, INK, bold=True)
    deck.bullet_list(page, mm(16), mm(100), mm(180), [
        ("30 fresh seeds", "34001–34030; one simulated day per pair."),
        ("Common random numbers", "same scenario, arrivals, lifetimes, event schedule and rendezvous namespace."),
        ("Four stress families", "8 surge · 8 scheduled fault · 7 unannounced outage · 7 mixed."),
        ("Aggregate guardrails", "DL overload, both directional drops and establishment failures all pass."),
    ], font_size=9.4, gap=15.0, bullet_color=GREEN)
    deck.card(page, mm(212), mm(80), mm(114), mm(80), fill=PALE_RED, line=0xECC3BF, accent=RED)
    deck.text(page, mm(219), mm(87), mm(100), mm(6), "Release decision", 14, RED, bold=True)
    deck.text(page, mm(219), mm(102), mm(100), mm(18),
              "Working demo candidate—not production-ready.", 15.5, INK, bold=True)
    deck.metric(page, mm(219), mm(126), mm(100), "Worst matched pair", "−23.50%", "scheduled-fault tail regression", tone=RED)
    deck.footer(page, "docs/cohort-mpc-full-campaign-results.md · demo_api/data/cohort_mpc_full_campaign_evidence_v1.json")

    # 21 — Scenario breakdown
    page = deck.new_slide("Average gains are broad, but robustness is weakest under surprise and fault tails", "Stage 3 · Scenario breakdown")
    scenarios = [
        ("Demand surge", 8, 10.42, 2.57, GREEN),
        ("Scheduled fault", 8, 19.01, -23.50, AMBER),
        ("Unannounced outage", 7, 0.71, -9.84, RED),
        ("Mixed stress", 7, 1.92, -8.28, PURPLE),
    ]
    deck.text(page, mm(14), mm(44), mm(119), mm(5), "AGGREGATE UL OVERLOAD-AREA REDUCTION", 8, MUTED, bold=True, font=MONO)
    for i, (label, pairs, agg, worst, color) in enumerate(scenarios):
        y = 58 + i * 25
        deck.text(page, mm(14), mm(y), mm(64), mm(5), label, 10.5, INK, bold=True)
        deck.text(page, mm(80), mm(y), mm(16), mm(5), f"n={pairs}", 7.8, MUTED, font=MONO)
        deck.bar(page, mm(102), mm(y + 1), mm(91), mm(7), agg, 20, color=color, label=f"+{agg:.2f}%")
    deck.card(page, mm(214), mm(42), mm(112), mm(112), fill=WHITE, accent=RED)
    deck.text(page, mm(221), mm(49), mm(98), mm(6), "Worst pair by scenario", 14, INK, bold=True)
    for i, (label, pairs, agg, worst, color) in enumerate(scenarios):
        y = 68 + i * 18
        deck.text(page, mm(221), mm(y), mm(60), mm(5), label, 8.9, INK, bold=True)
        tone = GREEN if worst >= 0 else RED
        deck.text(page, mm(285), mm(y - 1), mm(33), mm(6), f"{worst:+.2f}%", 11, tone, bold=True,
                  align=ParagraphAdjust.RIGHT, font=MONO)
        deck.line(page, mm(221), mm(y + 8), mm(318), mm(y + 8), LINE, 0.25)
    deck.card(page, mm(9), mm(161), mm(317), mm(13), fill=PALE_AMBER, line=None, accent=AMBER)
    deck.text(page, mm(16), mm(164), mm(303), mm(7),
              "Next release gate: positive severity-weighted confidence bounds on untouched seeds, plus materially reduced unannounced-outage and scheduled-fault tails.",
              9.2, INK, bold=True)
    deck.footer(page, "demo_api/data/cohort_mpc_full_campaign_evidence_v1.json · docs/cohort-mpc-full-campaign-results.md")

    # 22 — Dashboard architecture
    page = deck.new_slide("The dashboard is a real causal runtime—not a prerecorded animation", "Stage 4 · Working demo")
    layers = [
        ("REACT OPERATOR CONSOLE", "Live Dashboard · Evidence · Technical Detail · Expert mode", TEAL),
        ("REST + WEBSOCKET", "commands and snapshots · ordered versioned deltas · reconnect", PURPLE),
        ("FASTAPI ORCHESTRATOR", "run lifecycle · auth roles · audit · story checkpoints · analytics", GREEN),
        ("CAUSAL SIMULATION LOOP", "30 s tick · 10 min control · forecast · MPC · actuator", AMBER),
        ("FROZEN ARTIFACTS", "scenario · forecast bundle · MPC profile · 30-pair evidence", RED),
    ]
    for i, (head, body, color) in enumerate(layers):
        y = 41 + i * 25
        inset = i * 8
        deck.card(page, mm(9 + inset), mm(y), mm(317 - 2 * inset), mm(19), fill=WHITE, accent=color)
        deck.text(page, mm(17 + inset), mm(y + 4), mm(71), mm(5), head, 8.4, color, bold=True, font=MONO)
        deck.text(page, mm(92 + inset), mm(y + 3), mm(217 - 2 * inset), mm(7), body, 10, INK, bold=True,
                  align=ParagraphAdjust.CENTER)
    deck.card(page, mm(9), mm(169), mm(317), mm(7), fill=PALE_TEAL, line=None, accent=TEAL)
    deck.text(page, mm(16), mm(170.3), mm(303), mm(4),
              "The server chooses the policy before realizing the tick; event injection never rewrites prior telemetry. Rewind restores complete runtime state.",
              7.9, INK, bold=True)
    deck.footer(page, "demo_api/main.py · demo_api/runtime.py · frontend/src/App.tsx · docs/system-architecture-decisions.md ADR-021–023")

    # 23 — Dashboard data/features
    page = deck.new_slide("The operator sees both the decision and the evidence behind it", "Stage 4 · Dashboard data and features")
    feature_cards = [
        ("LIVE DASHBOARD", TEAL, ["per-UPF capacity, load, headroom and health", "new-session route widths and weight deltas", "forecast → certificate → apply/hold trace", "completed surge debrief and iteration ledger"]),
        ("EVIDENCE", GREEN, ["30 matched-pair frozen campaign", "mean-pair CI and severity-weighted total", "scenario-level aggregate and worst pair", "artifact identity and production boundary"]),
        ("TECHNICAL DETAIL", PURPLE, ["class arrivals, admissions and rejections", "p50/p90/actual forecast table", "static vs MPC certificate metrics", "raw trace, deployment boundary and expert controls"]),
    ]
    for i, (head, color, items) in enumerate(feature_cards):
        x = 9 + i * 106
        deck.card(page, mm(x), mm(43), mm(98), mm(104), fill=WHITE, accent=color)
        deck.text(page, mm(x + 7), mm(50), mm(84), mm(6), head, 9, color, bold=True, font=MONO)
        deck.bullet_list(page, mm(x + 7), mm(65), mm(84), items, font_size=9.2, gap=17.0, bullet_color=color)
    deck.card(page, mm(9), mm(155), mm(317), mm(18), fill=NAVY_2, line=None, accent=AMBER)
    deck.text(page, mm(16), mm(160), mm(92), mm(5), "DISPLAYED DATA SOURCES", 8.1, CYAN, bold=True, font=MONO)
    deck.text(page, mm(109), mm(159), mm(209), mm(8),
              "simulator StepResult · canonical group/UPF buckets · Forecast · Policy · certificate · frozen campaign JSON",
              9.4, WHITE, bold=True)
    deck.footer(page, "frontend/src/views.tsx · frontend/src/types.ts · demo_api/runtime.py::snapshot/comparison")

    # 24 — Dashboard screenshots
    page = deck.new_slide("The actual console keeps routing, evidence and caveats visible", "Stage 4 · Dashboard experience")
    shots = ROOT / "frontend/tests/demo.spec.ts-snapshots"
    prediction = crop_image(shots / "prediction-checkpoint-linux.png", "prediction-wide.jpg", 1.73, focus=(0.47, 0.45))
    evidence = crop_image(shots / "evidence-ending-linux.png", "evidence-wide.jpg", 1.73, focus=(0.5, 0.35))
    deck.image(page, prediction, mm(9), mm(42), mm(154), mm(89))
    deck.image(page, evidence, mm(172), mm(42), mm(154), mm(89))
    deck.text(page, mm(9), mm(135), mm(154), mm(5), "Predict + compare checkpoint", 10.5, TEAL, bold=True)
    deck.text(page, mm(9), mm(143), mm(154), mm(15),
              "Route widths encode future-session allocation; the side rail exposes the same-state static comparison and the no-migration caveat.",
              8.8, SLATE)
    deck.text(page, mm(172), mm(135), mm(154), mm(5), "Frozen campaign evidence", 10.5, GREEN, bold=True)
    deck.text(page, mm(172), mm(143), mm(154), mm(15),
              "The evidence view leads with the 10.52% average but places severity weighting, worst pair and release status beside it.",
              8.8, SLATE)
    deck.footer(page, "frontend/tests/demo.spec.ts-snapshots/prediction-checkpoint-linux.png · evidence-ending-linux.png")

    # 25 — Diversion code
    page = deck.new_slide("Traffic diversion is deterministic weighted placement for newly arriving sessions", "Stage 4 · How diversion works")
    deck.card(page, mm(9), mm(39), mm(198), mm(130), fill=NAVY_2, line=None, accent=TEAL)
    deck.text(page, mm(17), mm(46), mm(182), mm(5), "SIMULATOR PLACEMENT PATH", 8.4, CYAN, bold=True, font=MONO)
    code = (
        "requested = policy_weights[group_id]\n"
        "allowed = {u:w for u,w in requested.items()\n"
        "           if u in eligible_upfs\n"
        "           and state[u].health in {healthy,degraded}}\n"
        "weights = normalize(allowed)\n\n"
        "selected = rendezvous_select(\n"
        "    session_key, stable_namespace, weights)\n\n"
        "if active[selected] >= session_capacity:\n"
        "    reject()\n"
        "else:\n"
        "    lifetime = randint(min_steps, max_steps)\n"
        "    create_anchored_cohort(selected, lifetime)"
    )
    deck.text(page, mm(17), mm(58), mm(182), mm(96), code, 10.2, 0xE8F3F6, font=MONO)
    deck.text(page, mm(17), mm(157), mm(182), mm(6),
              "The policy ID does not re-salt the hash namespace; paired runs share the selection randomization.",
              8.2, 0xAFC3CB, bold=True)
    deck.card(page, mm(216), mm(39), mm(110), mm(130), fill=WHITE, accent=AMBER)
    deck.text(page, mm(223), mm(46), mm(96), mm(6), "What a weight change means", 14, INK, bold=True)
    deck.bullet_list(page, mm(223), mm(61), mm(96), [
        ("Before", "Each new session hashes across the current eligible weighted set."),
        ("Policy epoch", "MPC publishes new normalized weights after certification."),
        ("After", "Only later arrivals use the new weighted scores."),
        ("Persistence", "Admitted sessions stay on the selected UPF until their lifetime ends."),
        ("No migration", "Existing TEIDs/sessions are never moved by this code."),
    ], font_size=9.2, gap=19.2, bullet_color=AMBER)
    deck.footer(page, "simulator/macro/engine.py::advance · steering/hashing.py::rendezvous_select · docs/cdot-session-migration-decision.md")

    # 26 — guided demo
    page = deck.new_slide("A five-minute guided story makes the control loop explainable", "Stage 4 · Presenter walkthrough")
    story_rows = [
        ["TIME", "CHECKPOINT", "WHAT THE AUDIENCE SEES", "DEFENSIBLE CLAIM"],
        ["0:00", "Normal network", "three UPFs inside safe envelopes", "established sessions remain anchored"],
        ["0:35", "Problem appears", "stadium demand rises; known UPF-A UL reduction", "pressure is visible before the route changes"],
        ["1:20", "Predict + compare", "causal MA6 forecast + same-state certificate", "forecast does not steer traffic by itself"],
        ["2:35", "Divert new sessions", "new route widths and per-UPF weight deltas", "future arrivals shift; existing sessions do not"],
        ["3:40", "Result + evidence", "realized outcome, loss and frozen 30-pair evidence", "modeled exposure is reduced, not eliminated"],
    ]
    deck.table(page, mm(9), mm(43), [mm(28), mm(61), mm(124), mm(104)], story_rows,
               row_h=18.5, font_size=8.6)
    deck.card(page, mm(9), mm(161), mm(317), mm(13), fill=PALE_TEAL, line=None, accent=TEAL)
    deck.text(page, mm(16), mm(164), mm(303), mm(7),
              "Deterministic checkpoints are server-side state: browser reload, brief disconnect and rewind do not invent or recompute realized history.",
              8.9, INK, bold=True)
    deck.footer(page, "docs/presenter-guide.md · demo_api/story.py · demo_api/runtime.py::_guided_story/rewind")

    # 27 — Validation
    page = deck.new_slide("Verification spans contracts, causality, optimization and the presentation surface", "Quality assurance")
    qa = [
        ("CONTRACTS", "versioned schemas · round-trip fixtures · normalized policies", TEAL),
        ("CAUSALITY", "no future observations · known_at_step enforcement · append-only history", PURPLE),
        ("SIMULATOR", "deterministic streams · queues/drops · Parquet schema · audit sampling", GREEN),
        ("OPTIMIZER", "hand-solvable LPs · eligibility · diversification · same-state certificate", AMBER),
        ("EXPERIMENTS", "exact pairing · reserved seeds · immutable outputs · bootstrap interval", RED),
        ("DEMO", "auth roles · OpenAPI · WebSocket order · rewind exactness · visual snapshots", TEAL_2),
    ]
    for i, (head, body, color) in enumerate(qa):
        col = i % 2
        row = i // 2
        x = 9 + col * 160.5
        y = 42 + row * 38
        deck.card(page, mm(x), mm(y), mm(151), mm(31), fill=WHITE, accent=color)
        deck.text(page, mm(x + 7), mm(y + 6), mm(44), mm(5), head, 8.2, color, bold=True, font=MONO)
        deck.text(page, mm(x + 53), mm(y + 5), mm(90), mm(17), body, 9.2, INK, bold=True)
    deck.metric(page, mm(9), mm(164), mm(74), "Backend tests", "90", "all passed in 4.56 s", tone=GREEN)
    deck.metric(page, mm(89), mm(164), mm(74), "API paths", "20", "FastAPI preflight", tone=TEAL)
    deck.metric(page, mm(169), mm(164), mm(74), "Visual baselines", "13", "desktop, tablet and mobile", tone=PURPLE)
    deck.metric(page, mm(249), mm(164), mm(77), "Preflight", "PASS", "all data labeled synthetic", tone=AMBER)
    deck.footer(page, "tests/ · frontend/tests/demo.spec.ts · scripts/preflight.py · verified 09 Aug 2026")

    # 28 — integration boundary
    page = deck.new_slide("Simulation and production integration are intentionally separated", "C-DOT integration boundary")
    deck.card(page, mm(9), mm(40), mm(151), mm(124), fill=PALE_GREEN, line=0xB9DACA, accent=GREEN)
    deck.text(page, mm(16), mm(47), mm(136), mm(7), "Available now", 16, GREEN, bold=True)
    deck.bullet_list(page, mm(16), mm(63), mm(136), [
        "deterministic cohort simulation and event injection",
        "canonical Parquet, audit and hashed metadata",
        "trained forecast bundle with uncertainty bounds",
        "causal cohort MPC with static certificate",
        "simulated new-session actuation",
        "Prometheus-compatible synthetic metrics",
        "REST/WebSocket operator dashboard",
        "advisory and placeholder actuator interfaces",
    ], font_size=9.5, gap=11.0, bullet_color=GREEN)
    deck.card(page, mm(169), mm(40), mm(157), mm(124), fill=PALE_AMBER, line=0xE5C78D, accent=AMBER)
    deck.text(page, mm(176), mm(47), mm(142), mm(7), "Requires C-DOT / testbed", 16, AMBER, bold=True)
    deck.bullet_list(page, mm(176), mm(63), mm(142), [
        "live Prometheus metric names, labels and scrape semantics",
        "measured UPF UL/DL/session capacity envelopes",
        "topology, locality and eligibility truth",
        "authenticated SMF/EMS policy publication contract",
        "confirmation of established-session relocation support",
        "free5GC/PacketRusher integration and saturation tests",
        "production drift, missingness and counter-reset behavior",
        "untouched release campaign and tail-risk gate",
    ], font_size=9.5, gap=11.0, bullet_color=AMBER)
    deck.footer(page, "demo_api/interfaces.py · docs/cdot-session-migration-decision.md · docs/extreme-data-spec-and-cdot-gap-analysis.md §§11–13")

    # 29 — recommended next steps
    page = deck.new_slide("Recommended path from working demo to C-DOT-calibrated pilot", "Roadmap")
    roadmap = [
        ("01", "Observe", "Map N3/N6 counters, sessions, health and label cardinality into versioned telemetry contracts.", TEAL),
        ("02", "Calibrate", "Measure directional throughput/session envelopes and reproduce overload thresholds on target UPF builds.", PURPLE),
        ("03", "Replay", "Run the existing forecaster and MPC in advisory mode against recorded representative history.", GREEN),
        ("04", "Shadow", "Publish recommendations to an external store without actuation; compare static and MPC decisions online.", AMBER),
        ("05", "Pilot", "Enable bounded new-session steering with fail-closed rollback and explicit operational approval.", RED),
    ]
    for i, (num, head, body, color) in enumerate(roadmap):
        y = 40 + i * 26
        deck.circle(page, mm(12), mm(y + 2), mm(15), color)
        deck.text(page, mm(12), mm(y + 6), mm(15), mm(5), num, 8.5, WHITE, bold=True,
                  align=ParagraphAdjust.CENTER, font=MONO)
        deck.text(page, mm(34), mm(y + 2), mm(45), mm(6), head, 13, color, bold=True)
        deck.text(page, mm(83), mm(y + 1), mm(228), mm(12), body, 10.4, INK, bold=True)
        if i < len(roadmap) - 1:
            deck.line(page, mm(19.5), mm(y + 17), mm(19.5), mm(y + 28), LINE, 0.8)
    deck.card(page, mm(9), mm(171), mm(317), mm(6), fill=NAVY_2, line=None, accent=GREEN)
    deck.text(page, mm(16), mm(171.8), mm(303), mm(4),
              "Go/no-go principle: calibrate data and authority first; keep the current synthetic evidence intact as a reproducible engineering baseline.",
              7.6, WHITE, bold=True, align=ParagraphAdjust.CENTER)
    deck.footer(page, "docs/static-controller-deep-research.md §Recommended backlog · docs/end-to-end-runbook.md §Remaining implementation checklist")

    # 30 — close
    page = deck.new_slide("The engineering result is credible because the limitations are visible", "Conclusion", dark=True)
    deck.text(page, mm(10), mm(49), mm(207), mm(28),
              "Predict early.\nCertify against static.\nSteer only what is controllable.", 27, WHITE, bold=True)
    deck.text(page, mm(10), mm(90), mm(205), mm(20),
              "The repository demonstrates an end-to-end synthetic control loop with measurable average benefit, reproducible artifacts and an honest production boundary.",
              13.2, 0xC2D3DA)
    deck.card(page, mm(229), mm(44), mm(97), mm(92), fill=0x193541, line=0x395663, accent=GREEN)
    deck.text(page, mm(237), mm(53), mm(81), mm(5), "FINAL TAKEAWAYS", 8.5, CYAN, bold=True, font=MONO)
    deck.bullet_list(page, mm(237), mm(68), mm(80), [
        "7.63% forecast WAPE",
        "10.52% mean-pair UL improvement",
        "2.84% severity-weighted benefit",
        "all aggregate guardrails pass",
        "fault-tail risk remains",
        "new-session placement only",
    ], font_size=10, gap=10.2, color=WHITE, bullet_color=GREEN)
    deck.text(page, mm(10), mm(145), mm(207), mm(8), "Discussion", 11, CYAN, bold=True, font=MONO)
    deck.text(page, mm(10), mm(157), mm(207), mm(8),
              "What telemetry, capacity and actuation interfaces can C-DOT expose for a calibrated shadow pilot?",
              13.5, WHITE, bold=True)
    deck.footer(page, "C-DOT predictive UPF steering · technical review · 09 August 2026", dark=True)

    # 31 — Appendix metrics
    page = deck.new_slide("Appendix: metric definitions used in the experiments", "Appendix · Metrics")
    metric_rows = [
        ["METRIC", "DEFINITION", "WHY IT MATTERS"],
        ["WAPE", "Σ|actual−p50| / Σ|actual|", "scale-normalized forecast point error"],
        ["p90 coverage", "fraction where actual ≤ predicted upper p90", "uncertainty calibration for conservative planning"],
        ["UL overload area", "Σ max(0, load/safe_capacity−1) × seconds", "severity × duration above safe envelope"],
        ["Overload duration", "seconds with load above safe capacity", "duration only; ignores magnitude"],
        ["Dropped bytes", "overflow beyond physical capacity and bounded queue", "modeled service loss"],
        ["Establishment failures", "sessions rejected by capacity or no eligible route", "admission quality guardrail"],
        ["Mean-pair reduction", "mean of per-seed relative reductions", "equal weight to each paired scenario"],
        ["Severity-weighted reduction", "reduction in total area across all pairs", "weights heavy overload days more strongly"],
        ["Total variation", "½ Σ|new weight−old weight|", "policy churn / amount of routing change"],
    ]
    deck.table(page, mm(9), mm(42), [mm(62), mm(131), mm(124)], metric_rows,
               row_h=12.1, font_size=8.2)
    deck.card(page, mm(9), mm(169), mm(317), mm(7), fill=PALE_TEAL, line=None, accent=TEAL)
    deck.text(page, mm(16), mm(170.4), mm(303), mm(4),
              "The primary supplied-demo metric is directional UL overload area; DL is reported separately and cannot be hidden by a combined average.",
              7.9, INK, bold=True)
    deck.footer(page, "forecasting/metrics.py · simulator/macro/engine.py::SimulationResult.summary · experiments/evaluate_cohort_mpc_candidate.py")

    # 32 — Appendix source map
    page = deck.new_slide("Appendix: source map for implementation and evidence review", "Appendix · Traceability")
    source_rows = [
        ["STAGE", "PRIMARY IMPLEMENTATION", "PRIMARY EVIDENCE / DOCS"],
        ["Scenario generation", "experiments/build_extreme_history_manifest.py\nconfigs/extreme_training_profile.json", "docs/extreme-data-spec-and-cdot-gap-analysis.md\ndocs/traffic-model-spec.md"],
        ["Simulator", "simulator/macro/engine.py\nsimulator/macro/config.py · model.py", "docs/end-to-end-runbook.md\ndocs/system-architecture-decisions.md"],
        ["Forecaster", "experiments/train_forecaster.py\nforecasting/bundle.py · baselines.py · metrics.py", "docs/extreme-forecaster-v1-results.md"],
        ["One-window LP", "optimization/highs.py\nsimulator/macro/controllers.py", "docs/extreme-optimizer-tuning-results.md\ndocs/static-controller-deep-research.md"],
        ["Oracle + cohort MPC", "optimization/oracle_bounds.py\noptimization/cohort_mpc.py", "docs/extreme-oracle-bound-results.md\ndocs/cohort-mpc-full-campaign-results.md"],
        ["Steering", "steering/hashing.py · policy.py · gate.py", "docs/cdot-session-migration-decision.md"],
        ["Working demo", "demo_api/main.py · runtime.py · story.py\nfrontend/src/App.tsx · views.tsx", "docs/presenter-guide.md\nfrontend/tests/demo.spec.ts-snapshots/"],
    ]
    deck.table(page, mm(9), mm(42), [mm(59), mm(129), mm(129)], source_rows,
               row_h=15.0, font_size=7.8)
    deck.card(page, mm(9), mm(166), mm(317), mm(10), fill=NAVY_2, line=None, accent=TEAL)
    deck.text(page, mm(16), mm(168.5), mm(303), mm(5),
              "Frozen campaign evidence: demo_api/data/cohort_mpc_full_campaign_evidence_v1.json · source SHA-256 bd1d3727…b662c",
              8.2, WHITE, bold=True, font=MONO, align=ParagraphAdjust.CENTER)
    deck.footer(page, "Repository: /home/prarabdha/work/5g-simulation · deck generated from current workspace state")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ctx = connect()
    deck = Deck(ctx)
    build(deck)
    deck.save()
    print(PPTX)
    print(PDF)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
