"""Render an :class:`Article` to a self-contained, Medium-importable HTML page.

Output layout (under the GitHub Pages root)::

    docs/articles/<slug>/index.html
    docs/articles/<slug>/figures/<id>.png
    docs/articles/<slug>/assets/<copied images>

Inline images use *relative* URLs so the page previews locally and Medium's
importer resolves them against the fetched page URL; ``canonical`` and the
Open Graph tags use the absolute Pages URL.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from .figures import CHART_KINDS, render_chart
from .models import Article, ArticleError, Figure, load_article

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)

_MD_EXTENSIONS = ["tables", "footnotes", "attr_list", "sane_lists", "smarty", "md_in_html"]
_FIGURE_RE = re.compile(r"^\[\[figure:([\w-]+)\]\]\s*$", re.MULTILINE)
_WORDS_PER_MINUTE = 230


@dataclass(frozen=True)
class RenderResult:
    """Where an article landed and how to reach it."""

    slug: str
    out_dir: Path
    index_html: Path
    public_url: str


def _table_html(fig: Figure) -> str:
    """Render a table figure as a styled HTML ``<table>``."""
    head = "".join(f"<th>{escape(c)}</th>" for c in fig.columns)
    body_rows = []
    for i, row in enumerate(fig.rows):
        cls = ' class="hl"' if fig.highlight_row == i else ""
        cells = "".join(f"<td>{escape(str(c))}</td>" for c in row)
        body_rows.append(f"<tr{cls}>{cells}</tr>")
    cap = _caption_html(fig)
    return (
        '<figure class="fig fig-table">'
        f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
        f"{cap}</figure>"
    )


def _caption_html(fig: Figure) -> str:
    """Build the ``<figcaption>`` from title/caption/source, or empty string."""
    if not (fig.title or fig.caption or fig.source):
        return ""
    parts = []
    if fig.title:
        parts.append(f'<span class="fig-title">{escape(fig.title)}</span>')
    if fig.caption:
        parts.append(f"<span>{escape(fig.caption)}</span>")
    if fig.source:
        parts.append(f'<span class="fig-source">{escape(fig.source)}</span>')
    return f"<figcaption>{' '.join(parts)}</figcaption>"


def _figure_block_html(fig: Figure) -> str:
    """Build the inline HTML block for a non-table figure (chart or image)."""
    if fig.kind == "image":
        rel = f"assets/{Path(fig.src).name}" if fig.src else ""
    else:
        rel = f"figures/{fig.id}.png"
    return (
        '<figure class="fig">'
        f'<img src="{rel}" alt="{escape(fig.title or fig.id)}" />'
        f"{_caption_html(fig)}</figure>"
    )


def _inject_figures(body_md: str, figures: dict[str, Figure]) -> str:
    """Replace ``[[figure:id]]`` shortcodes with raw figure HTML blocks.

    Raises:
        ArticleError: If a shortcode references an unknown figure id.
    """

    def repl(match: re.Match[str]) -> str:
        fid = match.group(1)
        fig = figures.get(fid)
        if fig is None:
            raise ArticleError(f"body references unknown figure id {fid!r}")
        html = _table_html(fig) if fig.kind == "table" else _figure_block_html(fig)
        return f"\n\n{html}\n\n"

    return _FIGURE_RE.sub(repl, body_md)


def _reading_minutes(body_md: str) -> int:
    words = len(re.findall(r"\w+", body_md))
    return max(1, round(words / _WORDS_PER_MINUTE))


def render_article(
    article: Article,
    src_dir: Path,
    out_root: Path,
    base_url: str,
) -> RenderResult:
    """Render an article to ``out_root/<slug>/`` and return where it landed.

    Args:
        article: The validated article.
        src_dir: Source directory (for resolving image figures).
        out_root: ``docs/articles`` (the Pages articles root).
        base_url: Public Pages base URL (no trailing slash).

    Returns:
        A :class:`RenderResult`.

    Raises:
        ArticleError: On unresolved figure references or missing image sources.
    """
    out_dir = out_root / article.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    figures = article.figures_by_id

    # Render charts; copy passthrough images.
    for fig in article.figures:
        if fig.kind in CHART_KINDS:
            render_chart(fig, out_dir / "figures" / f"{fig.id}.png")
        elif fig.kind == "image":
            src = src_dir / (fig.src or "")
            if not src.is_file():
                raise ArticleError(f"figure {fig.id!r}: image src not found: {src}")
            dest = out_dir / "assets" / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    hero_rel = None
    if article.hero_image:
        hero_src = src_dir / article.hero_image
        if not hero_src.is_file():
            raise ArticleError(f"hero_image not found: {hero_src}")
        dest = out_dir / "assets" / hero_src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hero_src, dest)
        hero_rel = f"assets/{hero_src.name}"

    body_with_figs = _inject_figures(article.body_md, figures)
    md = markdown.Markdown(extensions=_MD_EXTENSIONS, output_format="html")
    body_html = md.convert(body_with_figs)

    public_url = f"{base_url}/articles/{article.slug}/"
    canonical = article.canonical or public_url
    og_image = None
    if hero_rel:
        og_image = f"{public_url}{hero_rel}"
    else:
        first_chart = next((f for f in article.figures if f.kind in CHART_KINDS), None)
        if first_chart:
            og_image = f"{public_url}figures/{first_chart.id}.png"

    template = _JINJA_ENV.get_template("article.html.j2")
    html = template.render(
        article=article,
        body_html=Markup(body_html),
        hero_rel=hero_rel,
        canonical=canonical,
        public_url=public_url,
        og_image=og_image,
        reading_minutes=_reading_minutes(article.body_md),
    )
    index_html = out_dir / "index.html"
    index_html.write_text(html.rstrip() + "\n", encoding="utf-8")
    logger.info("rendered article %r → %s", article.slug, index_html)

    return RenderResult(
        slug=article.slug,
        out_dir=out_dir,
        index_html=index_html,
        public_url=public_url,
    )


def render_from_dir(src_dir: Path, out_root: Path, base_url: str) -> RenderResult:
    """Load an article from ``src_dir`` and render it. Convenience wrapper."""
    article = load_article(src_dir)
    return render_article(article, src_dir, out_root, base_url)


@dataclass(frozen=True)
class IndexEntry:
    """One article's metadata for the Pages landing index."""

    slug: str
    title: str
    dek: str
    date: str


def write_pages_index(docs_dir: Path, articles: list[Article]) -> Path:
    """Write the GitHub Pages landing page listing all articles.

    Also drops a ``.nojekyll`` marker so Pages serves the raw HTML/assets
    untouched (Jekyll would otherwise ignore ``_``-prefixed paths).

    Args:
        docs_dir: The Pages root (``docs/``).
        articles: Articles to list, newest-first by date.

    Returns:
        Path to the written ``index.html``.
    """
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / ".nojekyll").touch()
    entries = sorted(
        (IndexEntry(a.slug, a.title, a.dek, a.date) for a in articles),
        key=lambda e: e.date,
        reverse=True,
    )
    template = _JINJA_ENV.get_template("article_index.html.j2")
    html = template.render(entries=entries)
    out = docs_dir / "index.html"
    out.write_text(html.rstrip() + "\n", encoding="utf-8")
    logger.info("wrote Pages index → %s (%d articles)", out, len(entries))
    return out
