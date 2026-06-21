"""Render a :class:`StoryAngle` to an editorial-light infographic.

The card is composed from *panels*: each panel is a function that draws into a
box ``(x, y, w)`` and returns the height it consumed, so the composer flows them
vertically and sizes the canvas to fit. A story is therefore "an ordered list of
panels + bound data" — exactly what the discovery engine emits — and adding or
reordering a module needs no coordinate surgery.

Every number drawn comes from the angle's audited :class:`Stat` corpus; nothing
is invented in the renderer.
"""

from __future__ import annotations

import base64
import re
import textwrap
from functools import lru_cache
from pathlib import Path

from padres_analytics.detect.angles import StoryAngle
from padres_analytics.render.cards import _html_to_png
from padres_analytics.render.tokens import (
    BIG_SHOULDERS_TTF,
    BROWN,
    BROWN_DIM,
    GOLD,
    HOT,
    INK,
    PAPER,
    SLATE,
    SPACE_GROTESK_TTF,
    TEXT_MUTED,
    XFRIARS_LOGO_PNG,
)


@lru_cache(maxsize=1)
def xfriars_logo_uri() -> str:
    """Return the real xFriars logo as a base64 data URI.

    Hard gate: every card must carry the real brand mark, never a text wordmark.
    Raises if the asset is missing so a logo-less card fails loudly instead of
    silently shipping off-brand.

    Raises:
        FileNotFoundError: if the logo asset is absent.
    """
    if not XFRIARS_LOGO_PNG.exists():
        raise FileNotFoundError(
            f"xFriars logo asset missing: {XFRIARS_LOGO_PNG} — cards must use the real logo"
        )
    return "data:image/png;base64," + base64.b64encode(XFRIARS_LOGO_PNG.read_bytes()).decode()


_W = 480
_ML, _MR = 26, 26
_CW = _W - _ML - _MR
_HAIR = "rgba(28,23,20,.12)"
_ZEBRA = "rgba(28,23,20,.035)"
_RED = HOT
_MUTED = TEXT_MUTED
_GAP = 18  # vertical gap between panels


class _Canvas:
    """SVG element accumulator with rounded-coordinate helpers."""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def text(
        self,
        x: float,
        y: float,
        txt: str,
        size: float,
        fill: str,
        *,
        w: int | None = None,
        anchor: str | None = None,
        ls: float | None = None,
        ff: str | None = None,
        italic: bool = False,
    ) -> None:
        a = f' text-anchor="{anchor}"' if anchor else ""
        wt = f' font-weight="{w}"' if w else ""
        sp = f' letter-spacing="{ls}"' if ls else ""
        fam = f' font-family="{ff}"' if ff else ""
        it = ' font-style="italic"' if italic else ""
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}"{a} font-size="{size}" '
            f'fill="{fill}"{wt}{sp}{fam}{it}>{_esc(txt)}</text>'
        )

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        stroke: str,
        w: float = 1,
        *,
        dash: str | None = None,
        op: float | None = None,
        cap: str | None = None,
    ) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{op}"' if op is not None else ""
        c = f' stroke-linecap="{cap}"' if cap else ""
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{w}"{d}{o}{c}/>'
        )

    def circle(
        self, cx: float, cy: float, r: float, fill: str, stroke: str | None = None, sw: float = 0
    ) -> None:
        s = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        self.parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}"{s}/>')

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        op: float | None = None,
        rx: float | None = None,
    ) -> None:
        o = f' opacity="{op}"' if op is not None else ""
        r = f' rx="{rx}"' if rx else ""
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}"{o}{r}/>'
        )

    def poly(self, pts: str, stroke: str, w: float = 1.6) -> None:
        self.parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{stroke}" stroke-width="{w}"/>'
        )


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _avg(v: float) -> str:
    """Format a rate stat like .314 (no leading zero)."""
    return f"{v:.3f}".lstrip("0")


def _tone_color(value: float, lo: float, hi: float) -> str:
    """Slate (cold) below ``lo``, gold (hot) above ``hi``, brown between."""
    if value <= lo:
        return SLATE
    if value >= hi:
        return GOLD
    return BROWN_DIM


def _section_header(c: _Canvas, x: float, y: float, title: str, right: str | None = None) -> float:
    """Draw a section header at baseline ``y``; return the y of the content start."""
    c.text(x, y, title, 10.5, INK, w=700, ls=1.2)
    if right:
        c.text(x + _CW, y, right, 9.5, BROWN_DIM, anchor="end", w=500)
    return y + 16


# --------------------------------------------------------------------------- #
# panels — each: (canvas, x, y_top, w, data) -> height consumed
# --------------------------------------------------------------------------- #
def _panel_dumbbell(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    rows = d["rows"][:10]
    c0 = _section_header(c, x, y + 12, "EVERY REGULAR IS OWED", "wOBA  vs  expected wOBA")
    lo, hi = 0.225, 0.360
    px0, px1 = x + 104, x + w - 28

    def dx(v: float) -> float:
        return px0 + (v - lo) / (hi - lo) * (px1 - px0)

    top, step = c0 + 6, 17.6
    bot = top + (len(rows) - 1) * step + 9
    for tick in (0.250, 0.300, 0.350):
        c.line(dx(tick), top, dx(tick), bot, _HAIR)
        c.text(dx(tick), top - 4, _avg(tick), 8.5, _MUTED, anchor="middle")
    for i, (name, woba, xwoba) in enumerate(rows):
        yy = top + i * step
        if i % 2 == 1:
            c.rect(x - 2, yy - step / 2, w + 4, step, _ZEBRA)
        xa, xe = dx(woba), dx(xwoba)
        c.text(x - 4, yy + 3.3, name, 11, INK, w=500)
        c.line(min(xa, xe), yy, max(xa, xe), yy, SLATE if xwoba > woba else GOLD, 2.4, op=0.45)
        c.circle(xe, yy, 4.2, PAPER, GOLD, 2)
        c.circle(xa, yy, 4.2, _RED if xwoba > woba else INK)
        gap = round((woba - xwoba) * 1000)
        gap_col = SLATE if gap < 0 else BROWN_DIM
        c.text(x + w, yy + 3.3, f"{gap:+d}", 10, gap_col, anchor="end", w=700)
    leg = bot + 15
    c.circle(x + 4, leg, 4, INK)
    c.text(x + 14, leg + 3.3, "actual wOBA", 9.5, BROWN)
    c.circle(x + 112, leg, 4, PAPER, GOLD, 2)
    c.text(x + 122, leg + 3.3, "expected (xwOBA)", 9.5, BROWN)
    c.text(x + w, leg + 3.3, "pts owed →", 9.5, _MUTED, anchor="end")
    return (leg + 6) - y


def _panel_gauge(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    woba, xwoba, pa, owed = d["woba"], d["xwoba"], d["pa"], d["owed"]
    c0 = _section_header(c, x, y + 12, "THE TEAM LUCK GAP")
    lo, hi = 0.250, max(0.330, xwoba + 0.012)
    gx0, gx1 = x, x + 188

    def gx(v: float) -> float:
        return gx0 + (v - lo) / (hi - lo) * (gx1 - gx0)

    by, bw = c0 + 18, 11
    c.text(gx(xwoba), by - 6, f"{_avg(xwoba)} expected", 9, BROWN, anchor="middle", w=700)
    c.rect(gx0, by, gx(xwoba) - gx0, bw, GOLD, op=0.28)
    c.rect(gx0, by, gx(woba) - gx0, bw, _RED)
    c.line(gx(xwoba), by - 3, gx(xwoba), by + bw + 3, GOLD, 2)
    c.text(gx(woba), by + bw + 13, f"{_avg(woba)} actual", 9, _RED, anchor="middle", w=700)
    big_y = by + 50
    c.text(
        x + 210,
        big_y,
        f"{owed:+d}",
        34,
        SLATE if owed > 0 else GOLD,
        w=900,
        ff="Big Shoulders Display",
        anchor="end",
    )
    c.text(x + 218, big_y - 13, "points of wOBA", 10, BROWN, w=600)
    mid = "owed by the bats," if owed > 0 else "the bats are giving back,"
    c.text(x + 218, big_y - 1, mid, 10, BROWN)
    c.text(x + 218, big_y + 11, f"over {pa:,} PA", 10, BROWN)
    return (big_y + 8) - y


def _panel_sparkline(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    vals = d["values"] or [0.0]
    span = d["span"]
    c0 = _section_header(c, x, y + 12, "A STREAKY STRETCH", "team AVG by game")
    sx0, sx1, top, bot = x, x + w, c0 + 6, c0 + 46

    def spx(i: int) -> float:
        return sx0 + (i / (len(vals) - 1) if len(vals) > 1 else 0) * (sx1 - sx0)

    def spy(v: float) -> float:
        return bot - (v - 0.0) / 0.400 * (bot - top)

    mean = sum(vals) / len(vals)
    c.line(sx0, spy(mean), sx1, spy(mean), _MUTED, 1, dash="2 3")
    c.poly(" ".join(f"{spx(i):.1f},{spy(v):.1f}" for i, v in enumerate(vals)), BROWN)
    imin, imax = vals.index(min(vals)), vals.index(max(vals))
    c.circle(spx(imin), spy(vals[imin]), 3.4, _RED)
    c.text(spx(imin), spy(vals[imin]) + 13, _avg(vals[imin]), 9, _RED, anchor="middle", w=700)
    c.circle(spx(imax), spy(vals[imax]), 3.4, GOLD)
    c.text(spx(imax), spy(vals[imax]) - 6, _avg(vals[imax]), 9, BROWN, anchor="middle", w=700)
    sub = "variance, not a trend"
    if span and span[0]:
        sub = f"{span[0]} to {span[1]} — variance, not a trend"
    c.text(x, bot + 16, sub, 9, _MUTED)
    return (bot + 20) - y


def _panel_contact(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    rows = d["rows"][:5]
    lg = d["league_ev"]
    c0 = _section_header(c, x, y + 12, "THE CONTACT IS REAL", "avg exit velocity (mph)")
    lo, hi = 86.0, 92.5
    cx0, cx1 = x + 92, x + w - 56

    def cxp(v: float) -> float:
        return cx0 + (max(lo, min(hi, v)) - lo) / (hi - lo) * (cx1 - cx0)

    r0, st = c0 + 8, 15
    lgx = cxp(lg)
    c.line(lgx, r0 - 12, lgx, r0 + (len(rows) - 1) * st + 7, SLATE, 1, dash="2 3")
    c.text(lgx, r0 - 16, f"lg avg {lg:.1f}", 8.5, SLATE, anchor="middle", w=600)
    for i, (nm, ev) in enumerate(rows):
        yy = r0 + i * st
        c.text(x, yy + 3, nm, 10, INK)
        c.line(cx0, yy, cxp(ev), yy, BROWN, 5, op=0.85, cap="round")
        c.text(cxp(ev) + 8, yy + 3, f"{ev:.1f}", 9.5, BROWN_DIM, w=700)
    return (r0 + (len(rows) - 1) * st + 12) - y


def _panel_ladder(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    actual, true, league, owed = d["actual"], d["true_talent"], d["league"], d["owed"]
    subj = d.get("subject", "the bats")
    c0 = _section_header(c, x, y + 12, "BUT — REGRESSION TO WHAT?", "method: The Book (Tango)")
    lo = min(actual, true, league) - 0.012
    hi = max(actual, true, league) + 0.012
    rx0, rx1 = x + 94, x + w - 10

    def rx(v: float) -> float:
        return rx0 + (v - lo) / (hi - lo) * (rx1 - rx0)

    ly = c0 + 26
    c.line(rx0, ly, rx1, ly, _HAIR, 1)
    c.line(rx(min(actual, true)), ly, rx(max(actual, true)), ly, GOLD, 3, op=0.55)
    c.line(rx(league), ly - 13, rx(league), ly + 13, SLATE, 1.5, dash="2 3")
    c.text(rx(league), ly - 17, f"league {_avg(league)}", 8.5, SLATE, anchor="middle", w=700)
    c.circle(rx(actual), ly, 4.4, _RED)
    c.text(rx(actual), ly + 18, f"actual {_avg(actual)}", 9, _RED, anchor="middle", w=700)
    c.circle(rx(true), ly, 4.4, PAPER, GOLD, 2.4)
    c.text(rx(true), ly + 18, f"true ≈ {_avg(true)}", 9, BROWN, anchor="middle", w=700)
    c.text((rx(actual) + rx(true)) / 2, ly - 7, f"{owed:+d}", 8.5, GOLD, anchor="middle", w=700)
    c.text(x, ly - 26, f"regress {subj} (220-PA prior)", 9, BROWN_DIM)
    return (ly + 26) - y


def _panel_pctbars(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    rows = d["rows"]
    subj = d.get("subject", "")
    c0 = _section_header(c, x, y + 12, f"{subj.upper()} — SAVANT PROFILE", "percentile rank")
    bx0, bx1 = x + 96, x + w - 36
    r0, st = c0 + 8, 17
    for i, (label, pct) in enumerate(rows):
        yy = r0 + i * st
        col = _tone_color(pct, 40, 60)
        c.text(x, yy + 3, label, 10, INK)
        c.rect(bx0, yy - 3.5, bx1 - bx0, 7, "rgba(28,23,20,.08)", rx=3.5)
        c.rect(bx0, yy - 3.5, (bx1 - bx0) * pct / 100, 7, col, rx=3.5)
        c.circle(bx0 + (bx1 - bx0) * pct / 100, yy, 5, PAPER, col, 2)
        c.text(x + w, yy + 3, f"{pct}", 10, col, anchor="end", w=700)
    return (r0 + (len(rows) - 1) * st + 12) - y


def _panel_hbars(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    """Labeled horizontal bars (e.g. pitch-mix counts).

    Each row is ``(label, value, note)``: the label sits left, a bar runs out to
    a width proportional to ``value / max(values)``, the ``note`` (e.g. avg velo)
    trails the bar end, and the integer value is right-aligned.
    """
    rows: list[tuple[str, float, str]] = d["rows"][:8]
    title = d["title"]
    right = d.get("right")
    c0 = _section_header(c, x, y + 12, title, right)
    if not rows:
        return (c0 + 6) - y
    peak = max((v for _, v, _ in rows), default=1.0) or 1.0
    bx0, bx1 = x + 92, x + w - 40
    r0, st = c0 + 8, 16
    for i, (label, value, note) in enumerate(rows):
        yy = r0 + i * st
        bw = (bx1 - bx0) * (value / peak)
        c.text(x, yy + 3, label, 10, INK)
        c.line(bx0, yy, bx0 + bw, yy, BROWN, 5, op=0.85, cap="round")
        if note:
            c.text(bx0 + bw + 8, yy + 3, note, 9, BROWN_DIM, w=600)
        c.text(x + w, yy + 3, str(int(value)), 10, INK, anchor="end", w=700)
    return (r0 + (len(rows) - 1) * st + 12) - y


def _panel_statline(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    """A row of big stat blocks: each ``(label, value_str)`` as value-over-label.

    Used for a box-score line (e.g. IP / K / R / BB) — punchy, scannable.
    """
    blocks: list[tuple[str, str]] = d["blocks"][:6]
    title = d.get("title")
    c0 = _section_header(c, x, y + 12, title, d.get("right")) if title else y + 12
    if not blocks:
        return (c0 + 6) - y
    n = len(blocks)
    step = w / n
    vy = c0 + 30
    for i, (label, value) in enumerate(blocks):
        cx = x + step * (i + 0.5)
        c.text(cx, vy, value, 30, INK, w=900, ff="Big Shoulders Display", anchor="middle")
        c.text(cx, vy + 14, label, 8.5, BROWN_DIM, w=600, ls=0.8, anchor="middle")
    return (vy + 20) - y


def _panel_hero(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    """A big hero number with a label, an optional plain-language gloss, and context.

    The gloss spells out any jargon (e.g. what "CSW%" means) so a casual fan
    isn't shut out; the context usually carries a league benchmark for meaning.
    """
    value = str(d["value"])
    accent = d.get("accent", INK)
    c.text(x, y + 50, value, 54, accent, w=900, ff="Big Shoulders Display")
    lx = x + 132
    cy = y + 24
    c.text(lx, cy, str(d.get("label", "")), 11, BROWN, w=700, ls=0.8)
    if d.get("gloss"):
        cy += 15
        c.text(lx, cy, str(d["gloss"]), 10, BROWN_DIM)
    for line in textwrap.wrap(str(d.get("context", "")), width=36)[:2]:
        cy += 14
        c.text(lx, cy, line, 10, BROWN_DIM)
    return max(62, (cy + 8) - y)


def _panel_pitchmix(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    """Pitch-mix bars whose *color* encodes swinging-strike rate.

    Each row is ``(label, count, note, swstr_rate)``: bar length = usage, bar
    color = slate (low whiff) → gold (high whiff). The put-away pitch pops.
    """
    rows: list[tuple[str, float, str, float]] = d["rows"][:8]
    c0 = _section_header(c, x, y + 12, d.get("title", "PITCH MIX"), d.get("right"))
    if not rows:
        return (c0 + 6) - y
    peak = max((v for _, v, _, _ in rows), default=1.0) or 1.0
    bx0, bx1 = x + 96, x + w - 132
    r0, st = c0 + 9, 17
    for i, (label, value, note, rate) in enumerate(rows):
        yy = r0 + i * st
        col = _tone_color(rate, 0.08, 0.16)  # SwStr%: <8% cold, >16% nasty
        c.text(x, yy + 3, label, 10, INK)
        c.line(bx0, yy, bx0 + (bx1 - bx0) * (value / peak), yy, col, 6, op=0.9, cap="round")
        c.text(x + w - 118, yy + 3, str(int(value)), 10, INK, anchor="end", w=700)
        if note:
            c.text(x + w - 110, yy + 3, note, 9, BROWN_DIM)
    return (r0 + (len(rows) - 1) * st + 12) - y


def _panel_trend(c: _Canvas, x: float, y: float, w: float, d: dict) -> float:
    """A labeled value line (e.g. fastball velocity by pitch) with end markers."""
    vals: list[float] = d["values"]
    c0 = _section_header(c, x, y + 12, d.get("title", "TREND"), d.get("right"))
    if len(vals) < 2:
        c.text(x, c0 + 14, "not enough pitches yet", 9, _MUTED)
        return (c0 + 20) - y
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    top, bot = c0 + 8, c0 + 44
    px0, px1 = x, x + w

    def sx(i: int) -> float:
        return px0 + i / (len(vals) - 1) * (px1 - px0)

    def sy(v: float) -> float:
        return bot - (v - lo) / span * (bot - top)

    c.poly(" ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(vals)), BROWN)
    c.circle(sx(0), sy(vals[0]), 3.2, BROWN_DIM)
    c.text(sx(0), sy(vals[0]) - 7, f"{vals[0]:.0f}", 9, BROWN_DIM, w=700)
    c.circle(sx(len(vals) - 1), sy(vals[-1]), 3.4, GOLD if vals[-1] >= vals[0] else SLATE)
    c.text(sx(len(vals) - 1), sy(vals[-1]) - 7, f"{vals[-1]:.0f}", 9, BROWN, anchor="end", w=700)
    return (bot + 6) - y


_PANELS = {
    "dumbbell": _panel_dumbbell,
    "gauge": _panel_gauge,
    "sparkline": _panel_sparkline,
    "contact": _panel_contact,
    "ladder": _panel_ladder,
    "pctbars": _panel_pctbars,
    "hbars": _panel_hbars,
    "statline": _panel_statline,
    "hero": _panel_hero,
    "pitchmix": _panel_pitchmix,
    "trend": _panel_trend,
}


# --------------------------------------------------------------------------- #
# composition
# --------------------------------------------------------------------------- #
def compose(angle: StoryAngle) -> str:
    """Compose the full infographic SVG for an angle, sizing the canvas to fit."""
    c = _Canvas()
    # header
    c.text(_ML, 33, f"SAN DIEGO PADRES  ·  {angle.subject.upper()}", 9, BROWN_DIM, w=600, ls=2.0)
    c.text(_ML - 2, 72, angle.title, 40, INK, w=800, ff="Big Shoulders Display", ls=0.5)
    # Brand mark — the real logo, always (hard gate: xfriars_logo_uri raises if missing).
    lw = 86
    lh = lw * 334 / 1414
    c.parts.append(
        f'<image href="{xfriars_logo_uri()}" x="{_W - _MR - lw:.1f}" y="22" '
        f'width="{lw}" height="{lh:.1f}"/>'
    )
    if angle.headshot:
        cx, cy, rr = _W - _MR - lw - 40, 31, 22
        c.parts.append(
            f'<defs><clipPath id="hsclip"><circle cx="{cx}" cy="{cy}" r="{rr}"/></clipPath></defs>'
            f'<image href="{angle.headshot}" x="{cx - rr}" y="{cy - rr}" '
            f'width="{rr * 2}" height="{rr * 2}" preserveAspectRatio="xMidYMid slice" '
            f'clip-path="url(#hsclip)"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{rr}" fill="none" stroke="{GOLD}" stroke-width="2"/>'
        )
    sub_lines = textwrap.wrap(angle.headline, width=64)[:2]
    y = 92
    for ln in sub_lines:
        c.text(_ML, y, ln, 12, BROWN)
        y += 15
    y += 8

    # panels
    for spec in angle.panels:
        fn = _PANELS.get(spec.kind)
        if fn is None:
            continue
        c.line(_ML, y, _W - _MR, y, _HAIR)
        h = fn(c, _ML, y, _CW, dict(spec.data))
        y += h + _GAP

    # confidence + caveat strip
    c.line(_ML, y - _GAP + 6, _W - _MR, y - _GAP + 6, _HAIR)
    conf_col = {"high": SLATE, "moderate": BROWN_DIM, "low": HOT}[angle.confidence]
    c.text(_ML, y, f"CONFIDENCE: {angle.confidence.upper()}", 8.5, conf_col, w=700, ls=0.8)
    if angle.caveats:
        c.text(_ML + 118, y, "· " + " · ".join(angle.caveats), 8.5, _MUTED)
    y += 18

    # footer
    c.line(_ML, y - 12, _W - _MR, y - 12, _HAIR)
    c.text(_ML, y, f"SOURCE: {angle.source.upper()} · THROUGH {angle.as_of}", 8, _MUTED, ls=0.5)
    handle = f'<tspan fill="{BROWN}" font-weight="700">@xFriars</tspan>'
    c.parts.append(
        f'<text x="{_W - _MR}" y="{y:.1f}" text-anchor="end" font-size="9" fill="{_MUTED}">'
        f"{handle} · SD BASEBALL INTELLIGENCE</text>"
    )
    total_h = y + 14

    head = (
        f'<svg viewBox="0 0 {_W} {total_h:.0f}" width="{_W}" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="Space Grotesk,sans-serif" '
        f'role="img" aria-label="{_esc(angle.headline)}">'
        f'<rect x="0" y="0" width="{_W}" height="{total_h:.0f}" fill="{PAPER}"/>'
        f'<rect x="0" y="0" width="{_W}" height="5" fill="{BROWN}"/>'
    )
    return head + "".join(c.parts) + "</svg>"


def _stat_token(unit: str, value: float) -> str | None:
    if unit == "woba":
        return _avg(value)
    if unit in ("pts", "count", "pct"):
        return str(int(value))
    if unit == "mph":
        return f"{value:.1f}"
    return None


def audit_rendered(angle: StoryAngle, svg: str) -> list[str]:
    """Confirm every ``shown`` Stat value actually appears on the rendered card.

    Parity with the repo's digit-audit: a claimed number can't silently drift or
    drop between the corpus and the canvas. (Headline-number backing is enforced
    upstream against the corpus by :func:`angles.audit_angle`, since the headline
    is always drawn as the subhead and would trivially satisfy a self-check here.)

    Returns:
        Human-readable violations; empty means the card is consistent.
    """
    # Audit against rendered TEXT only (not coordinates/colors in attributes), and
    # require a whole-number match so "5" can't satisfy itself inside "0.05" or "25".
    text = " ".join(re.findall(r">([^<]+)<", svg))
    violations: list[str] = []
    for st in angle.stats:
        if not st.shown:
            continue
        token = _stat_token(st.unit, st.value)
        if token is None:
            continue
        if not re.search(rf"(?<![\d.]){re.escape(token)}(?!\d)", text):
            violations.append(f"{st.key}={token} ({st.label}) not shown on card")
    return violations


def _wrap_html(svg: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        f'@font-face{{font-family:"Big Shoulders Display";'
        f'src:url("file://{BIG_SHOULDERS_TTF}") format("truetype");font-weight:600 900;}}'
        f'@font-face{{font-family:"Space Grotesk";'
        f'src:url("file://{SPACE_GROTESK_TTF}") format("truetype");font-weight:400 700;}}'
        f"html,body{{margin:0;padding:0;background:{PAPER};}}"
        "</style></head><body>"
        f"{svg}</body></html>"
    )


def render_angle(angle: StoryAngle, out_dir: Path, stem: str, *, strict: bool = True) -> Path:
    """Render an angle to a PNG, auditing the numbers first.

    Args:
        angle: The story to render.
        out_dir: Destination directory (created if missing).
        stem: Filename stem.
        strict: When True (default), raise if any asserted number is missing
            from the card — fail visibly rather than ship a wrong graphic.

    Returns:
        The written PNG path.

    Raises:
        ValueError: If ``strict`` and the render audit finds a missing number.
        RenderError: On any Playwright/rendering failure.
    """
    svg = compose(angle)
    problems = audit_rendered(angle, svg)
    if problems and strict:
        raise ValueError("render audit failed:\n  " + "\n  ".join(problems))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stem}.png"
    _html_to_png(_wrap_html(svg), out_path, _W, _svg_height(svg))
    return out_path


def _svg_height(svg: str) -> int:
    """Pull the integer viewBox height back out for the screenshot viewport."""
    vb = svg.split('viewBox="0 0 ', 1)[1].split('"', 1)[0]
    return int(float(vb.split()[1]))
