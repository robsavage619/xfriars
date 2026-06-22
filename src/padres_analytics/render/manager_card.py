"""Render the ``manager_case`` angle to a verdict-ledger infographic.

A bespoke layout (distinct from the stacked-panel story cards): a two-line hero,
a charge-by-charge accountability ledger with a verdict per row, a Pythagorean
over/under gauge, and a single red callout naming the real drag. Every number it
draws comes from the angle's audited :class:`Stat` corpus — nothing is invented
here, and a missing marquee number fails the render loudly.
"""

from __future__ import annotations

from pathlib import Path

from padres_analytics.detect.angles import StoryAngle
from padres_analytics.render.cards import _html_to_png
from padres_analytics.render.story_infographic import _esc, xfriars_logo_uri
from padres_analytics.render.tokens import (
    BIG_SHOULDERS_TTF,
    BROWN,
    BROWN_DIM,
    GOLD,
    INK,
    NEGATIVE,
    PAPER,
    POSITIVE,
    SLATE,
    SPACE_GROTESK_TTF,
    TEXT_MUTED,
)

_W = 480
_ML, _MR = 26, 26
_CW = _W - _ML - _MR
_HAIR = "rgba(28,23,20,.12)"


class RenderAuditError(ValueError):
    """Raised when a marquee number is missing from the rendered manager card."""


def _gauge_svg(actual: float, expected: float, y: float) -> tuple[str, float]:
    """Draw the wins-vs-Pythagorean gauge; return (svg, height consumed)."""
    lo = min(actual, expected) - 3.5
    hi = max(actual, expected) + 3.5
    x0, x1 = _ML + 4, _W - _MR - 4

    def gx(v: float) -> float:
        return x0 + (v - lo) / (hi - lo) * (x1 - x0)

    track_y = y + 30
    delta = actual - expected
    parts = [
        f'<line x1="{x0:.1f}" y1="{track_y:.1f}" x2="{x1:.1f}" y2="{track_y:.1f}" '
        f'stroke="rgba(28,23,20,.18)" stroke-width="2"/>',
        f'<line x1="{gx(expected):.1f}" y1="{track_y - 13:.1f}" x2="{gx(expected):.1f}" '
        f'y2="{track_y + 13:.1f}" stroke="{SLATE}" stroke-width="3"/>',
        f'<text x="{gx(expected):.1f}" y="{track_y - 20:.1f}" text-anchor="middle" '
        f'font-size="11" fill="{SLATE}">run margin says {expected:.1f}</text>',
        f'<path d="M {gx(expected):.1f} {track_y:.1f} L {gx(actual) - 11:.1f} {track_y:.1f}" '
        f'stroke="{POSITIVE}" stroke-width="3"/>',
        f'<circle cx="{gx(actual):.1f}" cy="{track_y:.1f}" r="8" fill="{POSITIVE}"/>',
        f'<text x="{gx(actual):.1f}" y="{track_y + 34:.1f}" text-anchor="middle" '
        f'font-family="Big Shoulders Display" font-weight="700" font-size="18" '
        f'fill="{POSITIVE}">{actual:.0f} actual wins · {delta:+.1f}</text>',
    ]
    return "".join(parts), 70.0


def _check(x: float, cy: float) -> str:
    return (
        f'<circle cx="{x:.1f}" cy="{cy:.1f}" r="12" fill="{POSITIVE}"/>'
        f'<path d="M {x - 6:.1f} {cy:.1f} l 4 5 l 8 -10" stroke="{PAPER}" stroke-width="2.6" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    )


def compose_manager(angle: StoryAngle) -> str:
    """Compose the manager-case verdict card SVG, sizing the canvas to fit."""
    data: dict = dict(angle.panels[0].data) if angle.panels else {}
    charges = list(data.get("charges", []))
    gauge = dict(data.get("gauge", {}))
    problem = str(data.get("problem", ""))

    p: list[str] = []
    # header
    p.append(
        f'<text x="{_ML}" y="33" font-size="9" fill="{BROWN_DIM}" font-weight="600" '
        f'letter-spacing="2">2026 · THE MANAGER QUESTION</text>'
    )
    lw = 86
    lh = lw * 334 / 1414
    p.append(
        f'<image href="{xfriars_logo_uri()}" x="{_W - _MR - lw:.1f}" y="20" '
        f'width="{lw}" height="{lh:.1f}"/>'
    )
    p.append(
        f'<text x="{_ML - 2}" y="84" font-family="Big Shoulders Display" font-weight="800" '
        f'font-size="46" fill="{INK}">IT\'S NOT ON</text>'
    )
    p.append(
        f'<text x="{_ML - 2}" y="128" font-family="Big Shoulders Display" font-weight="800" '
        f'font-size="46" fill="{INK}">STAMMEN</text>'
    )
    y = 150
    for ln in _wrap(angle.thesis, 70)[:2]:
        p.append(f'<text x="{_ML}" y="{y:.1f}" font-size="11.5" fill="{BROWN}">{_esc(ln)}</text>')
        y += 15
    y += 10

    # ledger
    p.append(
        f'<text x="{_ML}" y="{y:.1f}" font-size="10.5" fill="{INK}" font-weight="700" '
        f'letter-spacing="1.2">THE CHARGES — AND THE VERDICT</text>'
    )
    y += 12
    row_h = 64
    for i, (charge, ev) in enumerate(charges):
        cy = y + 18
        p.append(_check(_ML + 12, cy))
        p.append(
            f'<text x="{_ML + 34}" y="{cy + 5:.1f}" font-family="Big Shoulders Display" '
            f'font-weight="700" font-size="21" fill="{INK}">{_esc(charge)}</text>'
        )
        for j, ln in enumerate(_wrap(ev, 74)[:2]):
            p.append(
                f'<text x="{_ML + 34}" y="{cy + 24 + j * 14:.1f}" font-size="11.5" '
                f'fill="{INK}" opacity="0.78">{_esc(ln)}</text>'
            )
        if i < len(charges) - 1:
            p.append(
                f'<line x1="{_ML + 12}" y1="{y + row_h - 6:.1f}" x2="{_W - _MR}" '
                f'y2="{y + row_h - 6:.1f}" stroke="{_HAIR}" stroke-width="1"/>'
            )
        y += row_h

    # gauge
    y += 4
    p.append(
        f'<text x="{_ML}" y="{y:.1f}" font-size="10.5" fill="{INK}" font-weight="700" '
        f'letter-spacing="1.2">WINS vs WHAT THE RUN MARGIN PREDICTS</text>'
    )
    gsvg, gh = _gauge_svg(float(gauge.get("actual", 0)), float(gauge.get("expected", 0)), y)
    p.append(gsvg)
    y += gh + 8

    # the real drag
    p.append(
        f'<line x1="{_ML}" y1="{y:.1f}" x2="{_W - _MR}" y2="{y:.1f}" '
        f'stroke="{GOLD}" stroke-width="2"/>'
    )
    y += 22
    p.append(
        f'<text x="{_ML}" y="{y:.1f}" font-size="10.5" fill="{INK}" font-weight="700" '
        f'letter-spacing="1.2">SO WHAT IS DRAGGING THEM?</text>'
    )
    y += 22
    p.append(f'<circle cx="{_ML + 12}" cy="{y - 4:.1f}" r="12" fill="{NEGATIVE}"/>')
    p.append(
        f'<text x="{_ML + 12}" y="{y:.1f}" text-anchor="middle" '
        f'font-family="Big Shoulders Display" font-weight="700" font-size="18" '
        f'fill="{PAPER}">!</text>'
    )
    p.append(
        f'<text x="{_ML + 34}" y="{y:.1f}" font-family="Big Shoulders Display" font-weight="700" '
        f'font-size="20" fill="{NEGATIVE}">The bats — and that\'s not a lineup-card fix.</text>'
    )
    y += 22
    for ln in _wrap(problem, 80)[:3]:
        p.append(f'<text x="{_ML}" y="{y:.1f}" font-size="12" fill="{INK}">{_esc(ln)}</text>')
        y += 16

    # confidence + caveat
    y += 6
    p.append(
        f'<line x1="{_ML}" y1="{y - 12:.1f}" x2="{_W - _MR}" y2="{y - 12:.1f}" stroke="{_HAIR}"/>'
    )
    conf_col = {"high": SLATE, "moderate": BROWN_DIM, "low": NEGATIVE}[angle.confidence]
    p.append(
        f'<text x="{_ML}" y="{y:.1f}" font-size="8.5" fill="{conf_col}" font-weight="700" '
        f'letter-spacing="0.8">CONFIDENCE: {angle.confidence.upper()}</text>'
    )
    if angle.caveats:
        p.append(
            f'<text x="{_ML + 118}" y="{y:.1f}" font-size="8.5" fill="{TEXT_MUTED}">'
            f"· {_esc(' · '.join(angle.caveats))}</text>"
        )
    y += 18

    # footer
    p.append(
        f'<line x1="{_ML}" y1="{y - 12:.1f}" x2="{_W - _MR}" y2="{y - 12:.1f}" stroke="{_HAIR}"/>'
    )
    p.append(
        f'<text x="{_ML}" y="{y:.1f}" font-size="8" fill="{TEXT_MUTED}" letter-spacing="0.5">'
        f"SOURCE: {_esc(angle.source.upper())} · THROUGH {angle.as_of}</text>"
    )
    p.append(
        f'<text x="{_W - _MR}" y="{y:.1f}" text-anchor="end" font-size="9" fill="{TEXT_MUTED}">'
        f'<tspan fill="{BROWN}" font-weight="700">@xFriars</tspan> '
        f"· SD BASEBALL INTELLIGENCE</text>"
    )
    total_h = y + 14

    head = (
        f'<svg viewBox="0 0 {_W} {total_h:.0f}" width="{_W}" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="Space Grotesk,sans-serif" '
        f'role="img" aria-label="{_esc(angle.title)} — {_esc(angle.headline)}">'
        f'<rect x="0" y="0" width="{_W}" height="{total_h:.0f}" fill="{PAPER}"/>'
        f'<rect x="0" y="0" width="{_W}" height="5" fill="{BROWN}"/>'
    )
    return head + "".join(p) + "</svg>"


def audit_manager(angle: StoryAngle, svg: str) -> list[str]:
    """Confirm the marquee numbers are actually drawn on the card."""
    sm = {s.key: s.value for s in angle.stats}
    needed: list[str] = []
    if "mgr_wins" in sm and "mgr_losses" in sm:
        needed.append(f"{int(sm['mgr_wins'])}-{int(sm['mgr_losses'])}")
    if "mgr_pyth" in sm:
        needed.append(f"{sm['mgr_pyth']:.1f}")
    if "mgr_ra" in sm:
        needed.append(f"{sm['mgr_ra']:.2f}")
    for key in ("team_woba", "team_xwoba"):
        if key in sm:
            needed.append(f".{round(sm[key] * 1000):03d}")
    return [n for n in needed if n not in svg]


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=width)


def _height(svg: str) -> int:
    vb = svg.split('viewBox="0 0 ', 1)[1].split('"', 1)[0]
    return int(float(vb.split()[1]))


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


def render_manager_card(
    angle: StoryAngle, out_dir: Path, stem: str, *, strict: bool = True
) -> Path:
    """Render the manager-case angle to a PNG, auditing its numbers first.

    Args:
        angle: The ``manager_case`` story angle.
        out_dir: Destination directory (created if missing).
        stem: Filename stem.
        strict: When True, raise if a marquee number is missing from the card.

    Returns:
        The written PNG path.

    Raises:
        RenderAuditError: If ``strict`` and an asserted number is not drawn.
        RenderError: On any Playwright/rendering failure.
    """
    svg = compose_manager(angle)
    problems = audit_manager(angle, svg)
    if problems and strict:
        raise RenderAuditError("manager render audit failed:\n  " + "\n  ".join(problems))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stem}.png"
    _html_to_png(_wrap_html(svg), out_path, _W, _height(svg))
    return out_path
