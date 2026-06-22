"""Render the ``manager_history`` angle to a rookie-manager ranking card.

A leaderboard layout: rookie managers who inherited a playoff team, ranked by
first-year winning %, with the Padres' live line highlighted and the closest
historical comp called out. The cohort is cited reference data; the Padres' own
numbers come from the angle's audited :class:`Stat` corpus.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from padres_analytics.detect.angles import StoryAngle
from padres_analytics.render.cards import _html_to_png
from padres_analytics.render.manager_card import _height, _wrap_html
from padres_analytics.render.story_infographic import _esc, xfriars_logo_uri
from padres_analytics.render.tokens import (
    BROWN,
    BROWN_DIM,
    GOLD,
    INK,
    NEGATIVE,
    PAPER,
    POSITIVE,
    SLATE,
    TEXT_MUTED,
)

_W = 480
_ML, _MR = 26, 26
_HIGHLIGHT_BG = "#F3E7CB"  # gold wash for the Padres row
_COMP_BG = "#EAF0F1"  # slate wash for the comp callout
_LINE = "rgba(28,23,20,.28)"  # the handed→delivered connector


class RenderAuditError(ValueError):
    """Raised when a marquee number is missing from the rendered history card."""


def _pct(v: float) -> str:
    return f".{round(v * 1000):03d}"


def _sentence(s: str) -> str:
    """Capitalize the first letter only, preserving proper nouns (e.g. World Series)."""
    return s[:1].upper() + s[1:] if s else s


def compose_history(angle: StoryAngle) -> str:
    """Compose the handed-vs-delivered dumbbell card SVG, sizing the canvas to fit."""
    data: dict = dict(angle.panels[0].data) if angle.panels else {}
    rows: list[dict] = list(data.get("rows", []))

    lo, hi = 0.480, 0.630
    ax0, ax1 = _ML + 110, _W - _MR - 52

    def gx(v: float) -> float:
        return ax0 + (v - lo) / (hi - lo) * (ax1 - ax0)

    p: list[str] = []
    p.append(
        f'<text x="{_ML}" y="33" font-size="9" fill="{BROWN_DIM}" font-weight="600" '
        f'letter-spacing="2">EVERY ROOKIE HANDED A PLAYOFF TEAM · 2012-2026</text>'
    )
    lw = 86
    p.append(
        f'<image href="{xfriars_logo_uri()}" x="{_W - _MR - lw:.1f}" y="20" '
        f'width="{lw}" height="{lw * 334 / 1414:.1f}"/>'
    )
    title_lines = (
        textwrap.wrap(angle.title, width=14)[:2] if len(angle.title) > 12 else [angle.title]
    )
    ty = 84
    for ln in title_lines:
        p.append(
            f'<text x="{_ML - 2}" y="{ty}" font-family="Big Shoulders Display" font-weight="800" '
            f'font-size="44" fill="{INK}">{_esc(ln)}</text>'
        )
        ty += 42
    y = ty + 20
    for ln in textwrap.wrap(angle.thesis, 72)[:3]:
        p.append(f'<text x="{_ML}" y="{y:.1f}" font-size="11.5" fill="{BROWN}">{_esc(ln)}</text>')
        y += 15
    y += 12

    # legend
    p.append(
        f'<circle cx="{_ML + 4}" cy="{y - 3:.1f}" r="4.5" fill="{PAPER}" stroke="{INK}" '
        f'stroke-width="1.5"/>'
    )
    p.append(
        f'<text x="{_ML + 14}" y="{y:.1f}" font-size="10" fill="{TEXT_MUTED}">handed (2025)</text>'
    )
    p.append(f'<circle cx="{_ML + 116}" cy="{y - 3:.1f}" r="5" fill="{INK}"/>')
    p.append(
        f'<text x="{_ML + 126}" y="{y:.1f}" font-size="10" fill="{TEXT_MUTED}">year one</text>'
    )
    p.append(
        f'<text x="{ax1 + 6}" y="{y:.1f}" font-size="10" fill="{TEXT_MUTED}">win% swing</text>'
    )
    y += 14

    # axis ticks
    for t in (0.500, 0.550, 0.600):
        p.append(
            f'<line x1="{gx(t):.1f}" y1="{y:.1f}" x2="{gx(t):.1f}" '
            f'y2="{y + len(rows) * 38 + 4:.1f}" stroke="rgba(28,23,20,.08)" stroke-width="1"/>'
        )
        p.append(
            f'<text x="{gx(t):.1f}" y="{y - 3:.1f}" text-anchor="middle" font-size="8.5" '
            f'fill="{TEXT_MUTED}">{_pct(t)}</text>'
        )
    y += 8

    row_h = 38
    comp = None
    for r in rows:
        deliv = float(r["win_pct"])
        handed = float(r["prior_pct"])
        delta = int(r["delta"])
        is_pad = bool(r["subject"])
        is_comp = bool(r["note"]) and not is_pad
        if is_comp:
            comp = r
        color = GOLD if is_pad else (SLATE if is_comp else BROWN_DIM)
        cy = y + row_h / 2
        if is_pad:
            p.append(
                f'<rect x="{_ML - 4}" y="{y:.1f}" width="{_W - 2 * _ML + 8}" height="{row_h}" '
                f'fill="{_HIGHLIGHT_BG}"/>'
            )
        last = r["manager"].split(" ", 1)[-1]
        weight = 700 if is_pad else 500
        p.append(
            f'<text x="{_ML}" y="{cy - 1:.1f}" font-size="13" fill="{INK}" '
            f'font-weight="{weight}">{_esc(last)}</text>'
        )
        p.append(
            f'<text x="{_ML}" y="{cy + 12:.1f}" font-size="9" fill="{TEXT_MUTED}">'
            f"'{str(r['year'])[2:]} {_esc(r['team'])} · {r['wins']}-{r['losses']}</text>"
        )
        # connector + dots (handed -> delivered)
        p.append(
            f'<line x1="{gx(handed):.1f}" y1="{cy:.1f}" x2="{gx(deliv):.1f}" y2="{cy:.1f}" '
            f'stroke="{_LINE}" stroke-width="2"/>'
        )
        p.append(
            f'<circle cx="{gx(handed):.1f}" cy="{cy:.1f}" r="4.5" fill="{PAPER}" '
            f'stroke="{INK}" stroke-width="1.5"/>'
        )
        p.append(f'<circle cx="{gx(deliv):.1f}" cy="{cy:.1f}" r="6" fill="{color}"/>')
        dcol = POSITIVE if delta >= 0 else NEGATIVE
        p.append(
            f'<text x="{ax1 + 6}" y="{cy + 4:.1f}" font-family="Big Shoulders Display" '
            f'font-weight="700" font-size="14" fill="{dcol}">{delta:+d}</text>'
        )
        y += row_h

    # comp callout — the redemption road
    if comp is not None:
        y += 8
        ch = 62
        p.append(
            f'<rect x="{_ML}" y="{y:.1f}" width="{_W - 2 * _ML}" height="{ch}" fill="{_COMP_BG}"/>'
        )
        p.append(f'<rect x="{_ML}" y="{y:.1f}" width="4" height="{ch}" fill="{SLATE}"/>')
        last = comp["manager"].split(" ", 1)[-1].upper()
        p.append(
            f'<text x="{_ML + 16}" y="{y + 24:.1f}" font-family="Big Shoulders Display" '
            f'font-weight="700" font-size="18" fill="{SLATE}">THE {_esc(last)} ROAD</text>'
        )
        p.append(
            f'<text x="{_ML + 16}" y="{y + 45:.1f}" font-size="12" fill="{INK}">'
            f"{comp['year']}: {comp['wins']}-{comp['losses']} and doubted. "
            f'<tspan font-weight="700" fill="{SLATE}">{_esc(_sentence(comp["note"]))}.</tspan>'
            f"</text>"
        )
        y += ch

    # footer
    y += 24
    p.append(
        f'<line x1="{_ML}" y1="{y - 12:.1f}" x2="{_W - _MR}" y2="{y - 12:.1f}" '
        f'stroke="rgba(28,23,20,.12)"/>'
    )
    p.append(
        f'<text x="{_ML}" y="{y:.1f}" font-size="8" fill="{TEXT_MUTED}" letter-spacing="0.5">'
        f"SOURCE: {_esc(angle.source.upper())} · THROUGH {angle.as_of}</text>"
    )
    p.append(
        f'<text x="{_W - _MR}" y="{y + 13:.1f}" text-anchor="end" font-size="11" '
        f'fill="{BROWN}" font-weight="700">@xFriars</text>'
    )
    total_h = y + 22

    head = (
        f'<svg viewBox="0 0 {_W} {total_h:.0f}" width="{_W}" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="Space Grotesk,sans-serif" role="img" '
        f'aria-label="{_esc(angle.title)} — rookie manager first-year winning percentage">'
        f'<rect x="0" y="0" width="{_W}" height="{total_h:.0f}" fill="{PAPER}"/>'
        f'<rect x="0" y="0" width="{_W}" height="5" fill="{BROWN}"/>'
    )
    return head + "".join(p) + "</svg>"


def audit_history(angle: StoryAngle, svg: str) -> list[str]:
    """Confirm the Padres' live marquee numbers are drawn (record + winning %)."""
    sm = {s.key: s.value for s in angle.stats}
    needed: list[str] = []
    if "mgr_wins" in sm and "mgr_losses" in sm:
        needed.append(f"{int(sm['mgr_wins'])}-{int(sm['mgr_losses'])}")
    if "mgr_winpct" in sm:
        needed.append(_pct(sm["mgr_winpct"]))
    return [n for n in needed if n not in svg]


def render_manager_history_card(
    angle: StoryAngle, out_dir: Path, stem: str, *, strict: bool = True
) -> Path:
    """Render the manager-history angle to a PNG, auditing the live numbers first.

    Args:
        angle: The ``manager_history`` story angle.
        out_dir: Destination directory (created if missing).
        stem: Filename stem.
        strict: When True, raise if a Padres marquee number is missing from the card.

    Returns:
        The written PNG path.

    Raises:
        RenderAuditError: If ``strict`` and an asserted number is not drawn.
        RenderError: On any Playwright/rendering failure.
    """
    svg = compose_history(angle)
    problems = audit_history(angle, svg)
    if problems and strict:
        raise RenderAuditError("manager-history render audit failed:\n  " + "\n  ".join(problems))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stem}.png"
    _html_to_png(_wrap_html(svg), out_path, _W, _height(svg))
    return out_path
