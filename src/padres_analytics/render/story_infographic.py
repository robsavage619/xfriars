"""Render a :class:`LuckStory` to a multi-module editorial-light infographic PNG.

The layout is a vertical magazine column: macro hook, a per-player dumbbell
(actual vs. expected wOBA), a two-up luck gauge + volatility sparkline, a
contact-quality strip, and a regression-to-the-mean counterpoint ladder. Every
number comes from the story object; this module only positions them.
"""

from __future__ import annotations

from pathlib import Path

from padres_analytics.detect.luck_story import REGRESSION_PA_PRIOR, LuckStory
from padres_analytics.render.cards import _html_to_png
from padres_analytics.render.tokens import (
    BIG_SHOULDERS_TTF,
    BROWN,
    BROWN_DIM,
    DEVICE_SCALE,
    GOLD,
    HOT,
    INK,
    PAPER,
    SLATE,
    SPACE_GROTESK_TTF,
    TEXT_MUTED,
)

_W, _H = 480, 752
_ML, _MR = 26, 26
_HAIR = "rgba(28,23,20,.12)"
_ZEBRA = "rgba(28,23,20,.035)"
_RED = HOT
_MUTED = TEXT_MUTED


class _Canvas:
    """Tiny SVG element accumulator with rounded-coordinate helpers."""

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
        self, x: float, y: float, w: float, h: float, fill: str, op: float | None = None
    ) -> None:
        o = f' opacity="{op}"' if op is not None else ""
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}"{o}/>'
        )


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ordinal_avg(v: float) -> str:
    """Format a rate stat like .314 without the leading zero."""
    return f"{v:.3f}".lstrip("0")


def build_svg(story: LuckStory) -> str:
    """Compose the infographic SVG string from a story object."""
    c = _Canvas()
    c.parts.append(
        f'<svg viewBox="0 0 {_W} {_H}" width="{_W}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Space Grotesk,sans-serif" role="img" '
        f'aria-label="San Diego Padres contact-vs-results infographic">'
    )
    c.rect(0, 0, _W, _H, PAPER)
    c.rect(0, 0, _W, 5, BROWN)

    # ---------- header ----------
    c.text(_ML, 33, "SAN DIEGO PADRES  ·  WHY THE BATS WENT QUIET", 9, BROWN_DIM, w=600, ls=2.4)
    c.text(_ML - 2, 72, "HIT INTO HARD LUCK", 42, INK, w=800, ff="Big Shoulders Display", ls=0.5)
    c.text(
        _W - _MR,
        39,
        "xFriars",
        22,
        GOLD,
        w=900,
        anchor="end",
        ff="Big Shoulders Display",
        italic=True,
    )
    c.text(_ML, 93, "The funk is a results problem, not a contact problem — the lineup", 12, BROWN)
    c.text(_ML, 108, "has earned more at the plate than the scoreboard shows.", 12, BROWN)

    # ---------- dumbbell: actual vs expected wOBA ----------
    c.text(_ML, 136, "EVERY REGULAR IS OWED", 11, INK, w=700, ls=1.4)
    c.text(_W - _MR, 136, "wOBA  vs  expected wOBA", 9.5, BROWN_DIM, anchor="end", w=500)
    dumb = story.dumbbell[:10]
    dlo, dhi = 0.225, 0.360
    px0, px1 = 130, 430

    def dx(v: float) -> float:
        return px0 + (v - dlo) / (dhi - dlo) * (px1 - px0)

    axis_top, row0, step = 150, 166, 17.6
    axis_bot = row0 + (len(dumb) - 1) * step + 9
    for tick in (0.250, 0.300, 0.350):
        x = dx(tick)
        c.line(x, axis_top, x, axis_bot, _HAIR)
        c.text(x, axis_top - 4, _ordinal_avg(tick), 8.5, _MUTED, anchor="middle")
    for i, (name, woba, xwoba) in enumerate(dumb):
        y = row0 + i * step
        if i % 2 == 1:
            c.rect(_ML - 2, y - step / 2, _W - _ML - _MR + 4, step, _ZEBRA)
        xa, xe = dx(woba), dx(xwoba)
        lo, hi = min(xa, xe), max(xa, xe)
        c.text(_ML - 4, y + 3.3, name, 11, INK, w=500)
        c.line(lo, y, hi, y, SLATE if xwoba > woba else GOLD, 2.4, op=0.45)
        c.circle(xe, y, 4.2, PAPER, GOLD, 2)
        c.circle(xa, y, 4.2, _RED if xwoba > woba else INK)
        gap = round((woba - xwoba) * 1000)
        c.text(
            _W - _MR, y + 3.3, f"{gap:+d}", 10, SLATE if gap < 0 else BROWN_DIM, anchor="end", w=700
        )
    leg_y = axis_bot + 16
    c.circle(_ML + 4, leg_y, 4, INK)
    c.text(_ML + 14, leg_y + 3.3, "actual wOBA", 9.5, BROWN)
    c.circle(_ML + 116, leg_y, 4, PAPER, GOLD, 2)
    c.text(_ML + 126, leg_y + 3.3, "expected (xwOBA)", 9.5, BROWN)
    c.text(_W - _MR, leg_y + 3.3, "pts owed →", 9.5, _MUTED, anchor="end")

    div1 = leg_y + 18
    c.line(_ML, div1, _W - _MR, div1, _HAIR)

    # ---------- two-up: luck gauge | volatility sparkline ----------
    col_y = div1 + 20
    c.text(_ML, col_y, "THE TEAM LUCK GAP", 10.5, INK, w=700, ls=1.2)
    midx = 248
    c.text(midx, col_y, "A STREAKY STRETCH", 10.5, INK, w=700, ls=1.2)

    glo, ghi, gx0, gx1 = 0.260, 0.330, _ML, 214

    def gx(v: float) -> float:
        return gx0 + (v - glo) / (ghi - glo) * (gx1 - gx0)

    bar_y, bw = col_y + 22, 11
    c.text(
        gx(story.team_xwoba),
        bar_y - 6,
        f"{_ordinal_avg(story.team_xwoba)} expected",
        9,
        BROWN,
        anchor="middle",
        w=700,
    )
    c.rect(gx0, bar_y, gx(story.team_xwoba) - gx0, bw, GOLD, op=0.28)
    c.rect(gx0, bar_y, gx(story.team_woba) - gx0, bw, _RED)
    c.line(gx(story.team_xwoba), bar_y - 3, gx(story.team_xwoba), bar_y + bw + 3, GOLD, 2)
    c.text(
        gx(story.team_woba),
        bar_y + bw + 13,
        f"{_ordinal_avg(story.team_woba)} actual",
        9,
        _RED,
        anchor="middle",
        w=700,
    )
    big_y = bar_y + 52
    c.text(_ML, big_y, str(story.luck_gap_pts), 34, SLATE, w=900, ff="Big Shoulders Display")
    c.text(_ML + 60, big_y - 13, "points of wOBA", 10, BROWN, w=600)
    c.text(_ML + 60, big_y - 1, "left on the table,", 10, BROWN)
    c.text(_ML + 60, big_y + 11, f"across {story.team_pa:,} PA", 10, BROWN)

    sx0, sx1 = midx, _W - _MR
    sy_top, sy_bot = col_y + 18, col_y + 62
    vals = story.daily_avg or [0.0]
    slo, shi = 0.0, 0.400

    def spx(i: int) -> float:
        return sx0 + (i / (len(vals) - 1) if len(vals) > 1 else 0) * (sx1 - sx0)

    def spy(v: float) -> float:
        return sy_bot - (v - slo) / (shi - slo) * (sy_bot - sy_top)

    mean = sum(vals) / len(vals)
    c.line(sx0, spy(mean), sx1, spy(mean), _MUTED, 1, dash="2 3")
    pts = " ".join(f"{spx(i):.1f},{spy(v):.1f}" for i, v in enumerate(vals))
    c.parts.append(f'<polyline points="{pts}" fill="none" stroke="{BROWN}" stroke-width="1.6"/>')
    imin, imax = vals.index(min(vals)), vals.index(max(vals))
    c.circle(spx(imin), spy(vals[imin]), 3.4, _RED)
    c.text(
        spx(imin), spy(vals[imin]) + 13, _ordinal_avg(vals[imin]), 9, _RED, anchor="middle", w=700
    )
    c.circle(spx(imax), spy(vals[imax]), 3.4, GOLD)
    c.text(
        spx(imax), spy(vals[imax]) - 6, _ordinal_avg(vals[imax]), 9, BROWN, anchor="middle", w=700
    )
    span = f"{story.spark_span[0]} to {story.spark_span[1]}" if story.spark_span[0] else ""
    c.text(midx, sy_bot + 18, f"team batting average by game · {span}", 9, _MUTED)

    div2 = big_y + 22
    c.line(_ML, div2, _W - _MR, div2, _HAIR)

    # ---------- contact strip ----------
    c_y = div2 + 20
    c.text(_ML, c_y, "THE CONTACT IS REAL", 10.5, INK, w=700, ls=1.2)
    c.text(_W - _MR, c_y, "avg exit velocity (mph)", 9.5, BROWN_DIM, anchor="end", w=500)
    contact = story.contact[:5]
    clo, chi, cx0, cx1 = 86.0, 92.5, 118, 392

    def cxp(v: float) -> float:
        return cx0 + (max(clo, min(chi, v)) - clo) / (chi - clo) * (cx1 - cx0)

    crow0, cstep = c_y + 22, 15
    lg_x = cxp(story.league_ev)
    c.line(lg_x, crow0 - 12, lg_x, crow0 + (len(contact) - 1) * cstep + 7, SLATE, 1, dash="2 3")
    c.text(lg_x, crow0 - 16, f"lg avg {story.league_ev:.1f}", 8.5, SLATE, anchor="middle", w=600)
    for i, (nm, ev) in enumerate(contact):
        y = crow0 + i * cstep
        c.text(_ML, y + 3, nm, 10, INK)
        c.line(cx0, y, cxp(ev), y, BROWN, 5, op=0.85, cap="round")
        c.text(cxp(ev) + 8, y + 3, f"{ev:.1f}", 9.5, BROWN_DIM, w=700)

    # ---------- counterpoint: regression to what? ----------
    div3 = crow0 + (len(contact) - 1) * cstep + 22
    c.line(_ML, div3, _W - _MR, div3, _HAIR)
    cp_y = div3 + 20
    c.text(_ML, cp_y, "BUT — REGRESSION TO WHAT?", 10.5, INK, w=700, ls=1.2)
    c.text(
        _W - _MR, cp_y, "method: Tango et al., The Book (2007)", 8.5, BROWN_DIM, anchor="end", w=500
    )
    rlo, rhi, rx0, rx1 = 0.280, 0.340, 120, 430

    def rx(v: float) -> float:
        return rx0 + (max(rlo, min(rhi, v)) - rlo) / (rhi - rlo) * (rx1 - rx0)

    lad_y = cp_y + 34
    c.line(rx0, lad_y, rx1, lad_y, _HAIR, 1)
    for tick in (0.290, 0.300, 0.310, 0.320, 0.330):
        x = rx(tick)
        c.line(x, lad_y - 3, x, lad_y + 3, _HAIR, 1)
    actual = story.team_woba
    true = story.true_talent
    league = story.league_xwoba
    c.line(rx(actual), lad_y, rx(true), lad_y, GOLD, 3, op=0.55)
    c.line(rx(league), lad_y - 13, rx(league), lad_y + 13, SLATE, 1.5, dash="2 3")
    c.text(
        rx(league),
        lad_y - 17,
        f"league avg {_ordinal_avg(league)}",
        8.5,
        SLATE,
        anchor="middle",
        w=700,
    )
    c.circle(rx(actual), lad_y, 4.4, _RED)
    c.text(
        rx(actual), lad_y + 18, f"actual {_ordinal_avg(actual)}", 9, _RED, anchor="middle", w=700
    )
    c.circle(rx(true), lad_y, 4.4, PAPER, GOLD, 2.4)
    c.text(
        rx(true) + 4,
        lad_y + 18,
        f"true talent ≈ {_ordinal_avg(true)}",
        9,
        BROWN,
        anchor="middle",
        w=700,
    )
    c.text(
        (rx(actual) + rx(true)) / 2,
        lad_y - 7,
        f"+{story.owed_pts} owed",
        8.5,
        GOLD,
        anchor="middle",
        w=700,
    )

    syn_y = lad_y + 38
    c.text(
        _ML,
        syn_y,
        f"Regress the bats with a {REGRESSION_PA_PRIOR}-PA prior and they climb to "
        f"~{_ordinal_avg(true)} — dead even with the",
        11,
        BROWN,
    )
    c.text(
        _ML,
        syn_y + 15,
        f"{_ordinal_avg(league)} league line. The funk is real luck; the ceiling it hides "
        "is real average.",
        11,
        BROWN,
    )

    f_y = syn_y + 36
    c.line(_ML, f_y - 12, _W - _MR, f_y - 12, _HAIR)
    c.text(_ML, f_y, f"SOURCE: {story.source.upper()} · THROUGH {story.as_of}", 8, _MUTED, ls=0.5)
    handle = f'<tspan fill="{BROWN}" font-weight="700">@xFriars</tspan>'
    c.parts.append(
        f'<text x="{_W - _MR}" y="{f_y:.1f}" text-anchor="end" font-size="9" fill="{_MUTED}">'
        f"{handle} · SD BASEBALL INTELLIGENCE</text>"
    )
    c.parts.append("</svg>")
    return "".join(c.parts)


def _wrap_html(svg: str) -> str:
    """Wrap the SVG in a minimal HTML doc with the vendored fonts for offline render."""
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


def render_luck_infographic(story: LuckStory, out_dir: Path, stem: str) -> Path:
    """Render a luck story to a PNG under ``out_dir``.

    Args:
        story: The composed story.
        out_dir: Directory for the PNG (created if missing).
        stem: Filename stem (no extension).

    Returns:
        The written PNG path.

    Raises:
        RenderError: On any Playwright/rendering failure.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stem}.png"
    html = _wrap_html(build_svg(story))
    _html_to_png(html, out_path, _W, _H)
    return out_path


# Surface PNG resolution for callers/tests that want it.
PNG_SIZE = (_W * DEVICE_SCALE, _H * DEVICE_SCALE)
