"""Render chart figures to premium PNGs, reusing the card engine.

Charts are hand-drawn D3 SVG on the paper-card stock — the same Jinja2 →
Playwright → PNG pipeline the X cards use — so articles share one visual
language with the cards. Baking to PNG (not live JS) means the charts survive
Medium's importer, which re-hosts ``<img>`` sources and strips scripts.

Figure titles and captions are *not* burned into the PNG; the article template
renders them as real HTML text so they stay selectable and SEO-readable after
the Medium import.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from padres_analytics.render import tokens
from padres_analytics.render.cards import RenderError, html_to_png

from .models import Figure

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)

# Wide editorial figure — 16:9-ish, rendered at 2x for retina-crisp output.
FIG_VIEWPORT_W = 1040
FIG_VIEWPORT_H = 560

_CHART_TEMPLATES: dict[str, str] = {
    "bar": "fig_bar.html.j2",
    "line": "fig_line.html.j2",
    "scatter": "fig_scatter.html.j2",
}

CHART_KINDS = frozenset(_CHART_TEMPLATES)


class FigureRenderError(RuntimeError):
    """Raised when a chart figure cannot be rendered. Never silently swallowed."""


def _figure_tokens() -> dict[str, str]:
    """The palette/font subset the figure templates need, as file:// resolvable paths."""
    return {
        "paper": tokens.PAPER,
        "paper_panel": tokens.PAPER_PANEL,
        "ink": tokens.INK,
        "ink_soft": tokens.INK_SOFT,
        "brown": tokens.BROWN,
        "brown_dim": tokens.BROWN_DIM,
        "gold": tokens.GOLD,
        "hot": tokens.HOT,
        "slate": tokens.SLATE,
        "text_muted": tokens.TEXT_MUTED,
        "hairline": tokens.HAIRLINE,
        "positive": tokens.POSITIVE,
        "negative": tokens.NEGATIVE,
        "d3_js": str(tokens.D3_JS),
        "space_grotesk_ttf": str(tokens.SPACE_GROTESK_TTF),
        "big_shoulders_ttf": str(tokens.BIG_SHOULDERS_TTF),
    }


def render_chart(spec: Figure, out_path: Path) -> Path:
    """Render a chart figure (bar/line/scatter) to ``out_path`` as a PNG.

    Args:
        spec: A figure whose ``kind`` is one of :data:`CHART_KINDS`.
        out_path: Destination ``.png`` path; parent dirs are created.

    Returns:
        ``out_path``.

    Raises:
        FigureRenderError: If the figure kind is not a chart kind.
        RenderError: If Playwright rendering fails.
    """
    template_name = _CHART_TEMPLATES.get(spec.kind)
    if template_name is None:
        raise FigureRenderError(f"{spec.kind!r} is not a chart kind (figure {spec.id!r})")

    template = _JINJA_ENV.get_template(template_name)
    html = template.render(
        spec=json.dumps(spec.model_dump(mode="json"), default=str),
        **_figure_tokens(),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        html_to_png(html, out_path, FIG_VIEWPORT_W, FIG_VIEWPORT_H)
    except RenderError as exc:
        raise FigureRenderError(f"figure {spec.id!r} failed to render: {exc}") from exc
    return out_path
