"""Tests for the long-form article pipeline (models, injection, render)."""

from __future__ import annotations

from pathlib import Path

import pytest

from padres_analytics.longform import render as render_mod
from padres_analytics.longform.models import ArticleError, Figure, load_article
from padres_analytics.longform.render import render_article, write_pages_index
from padres_analytics.longform.scaffold import new_article, slugify

_FRONTMATTER = """\
---
title: The Tatis Surge
subtitle: A look at the numbers
dek: What changed in June.
date: 2026-06-21
tags: [Padres, Tatis]
figures:
  - id: war
    kind: table
    title: WAR leaders
    columns: [Player, fWAR]
    rows: [[Tatis, 3.8], [Machado, 3.1]]
    highlight_row: 0
---

Intro paragraph.

[[figure:war]]

## Section

Closing thought.
"""


def _write(src_dir: Path, text: str = _FRONTMATTER) -> Path:
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "article.md").write_text(text, encoding="utf-8")
    return src_dir


def test_load_article_parses_frontmatter_and_body(tmp_path: Path) -> None:
    art = load_article(_write(tmp_path / "tatis"))
    assert art.slug == "tatis"
    assert art.title == "The Tatis Surge"
    assert art.date == "2026-06-21"  # YAML date coerced to ISO string
    assert art.tags == ["Padres", "Tatis"]
    assert "Intro paragraph." in art.body_md
    assert len(art.figures) == 1


def test_load_article_requires_frontmatter(tmp_path: Path) -> None:
    src = _write(tmp_path / "bad", "no frontmatter here\n")
    with pytest.raises(ArticleError, match="frontmatter"):
        load_article(src)


def test_figure_validation_rejects_mismatched_bar() -> None:
    with pytest.raises(ValueError, match="x/y length mismatch"):
        Figure(id="b", kind="bar", x=["a", "b"], y=[1.0])


def test_table_figure_requires_consistent_widths() -> None:
    with pytest.raises(ValueError, match="row width"):
        Figure(id="t", kind="table", columns=["A", "B"], rows=[["only one"]])


def test_unknown_figure_reference_raises(tmp_path: Path) -> None:
    text = _FRONTMATTER.replace("[[figure:war]]", "[[figure:ghost]]")
    src = _write(tmp_path / "ghost", text)
    art = load_article(src)
    with pytest.raises(ArticleError, match="unknown figure id 'ghost'"):
        render_article(art, src, tmp_path / "out", "https://example.com")


def test_render_table_only_article_produces_html(tmp_path: Path) -> None:
    src = _write(tmp_path / "tatis")
    art = load_article(src)
    result = render_article(art, src, tmp_path / "out", "https://example.com/site")

    assert result.public_url == "https://example.com/site/articles/tatis/"
    html = result.index_html.read_text(encoding="utf-8")
    assert "<table>" in html
    assert 'class="hl"' in html  # highlight_row applied
    assert "WAR leaders" in html
    assert '<link rel="canonical" href="https://example.com/site/articles/tatis/">' in html
    assert "<h2>Section</h2>" in html  # markdown converted


def test_render_chart_figure_invokes_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = """\
---
title: Chart Piece
date: 2026-06-21
figures:
  - id: bars
    kind: bar
    x: [a, b]
    y: [1, 2]
---

Body.

[[figure:bars]]
"""
    src = _write(tmp_path / "chart", text)
    art = load_article(src)

    calls: list[str] = []

    def fake_render_chart(spec: Figure, out_path: Path) -> Path:
        calls.append(spec.id)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x89PNG")
        return out_path

    monkeypatch.setattr(render_mod, "render_chart", fake_render_chart)
    result = render_article(art, src, tmp_path / "out", "https://example.com")

    assert calls == ["bars"]
    assert (result.out_dir / "figures" / "bars.png").is_file()
    html = result.index_html.read_text(encoding="utf-8")
    assert 'src="figures/bars.png"' in html


def test_write_pages_index_and_nojekyll(tmp_path: Path) -> None:
    src = _write(tmp_path / "tatis")
    art = load_article(src)
    docs = tmp_path / "docs"
    out = write_pages_index(docs, [art])

    assert (docs / ".nojekyll").is_file()
    html = out.read_text(encoding="utf-8")
    assert 'href="articles/tatis/"' in html
    assert "The Tatis Surge" in html


def test_slugify() -> None:
    assert slugify("The Tatis Surge!") == "the-tatis-surge"
    assert slugify("  Why xwOBA Matters  ") == "why-xwoba-matters"


def test_new_article_scaffold_roundtrips(tmp_path: Path) -> None:
    new_article(tmp_path, "my-dive", "My Dive", "2026-06-21", subtitle="Sub", dek="Dek")
    art = load_article(tmp_path / "my-dive")
    assert art.title == "My Dive"
    assert art.subtitle == "Sub"
    assert art.figures[0].id == "example"


def test_new_article_rejects_clash(tmp_path: Path) -> None:
    new_article(tmp_path, "dup", "Dup", "2026-06-21")
    with pytest.raises(ValueError, match="already exists"):
        new_article(tmp_path, "dup", "Dup", "2026-06-21")
