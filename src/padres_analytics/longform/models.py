"""Data models for long-form articles and a Markdown loader.

An article lives at ``articles/<slug>/article.md``: a YAML frontmatter block
(metadata + optional inline figures) followed by a Markdown body. Figures may
also be split into a sibling ``figures.yaml`` for readability when the data is
large. The body references figures with a shortcode on its own line::

    [[figure:war_leaders]]

Prose is authored by hand (chat-driven) — nothing here calls an LLM.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

_FRONTMATTER_FENCE = "---"


class ArticleError(ValueError):
    """Raised when an article source is malformed. Never silently swallowed."""


class Series(BaseModel):
    """One named line in a multi-series line chart."""

    name: str
    x: list[float | str]
    y: list[float]

    @model_validator(mode="after")
    def _lengths_match(self) -> Series:
        if len(self.x) != len(self.y):
            raise ValueError(
                f"series {self.name!r}: x has {len(self.x)} points, y has {len(self.y)}"
            )
        return self


class Point(BaseModel):
    """One labeled point in a scatter plot."""

    label: str
    x: float
    y: float


FigureKind = Literal["bar", "line", "scatter", "table", "image"]


class Figure(BaseModel):
    """A renderable figure. Chart kinds become PNGs; ``table`` becomes HTML.

    Each kind reads a different subset of fields; ``validate_kind`` enforces the
    required ones so a malformed figure fails at load, not at render.
    """

    id: str
    kind: FigureKind
    title: str = ""
    caption: str = ""
    source: str = ""

    # bar / single-series line
    x: list[str | float] = Field(default_factory=list)
    y: list[float] = Field(default_factory=list)
    # multi-series line
    series: list[Series] = Field(default_factory=list)
    # scatter
    points: list[Point] = Field(default_factory=list)
    # shared chart options
    highlight: str | None = None
    x_label: str = ""
    y_label: str = ""

    # table
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str | float]] = Field(default_factory=list)
    highlight_row: int | None = None

    # image passthrough — path relative to the article source directory
    src: str | None = None

    @model_validator(mode="after")
    def _validate_kind(self) -> Figure:
        if self.kind == "bar":
            if not self.x or not self.y:
                raise ValueError(f"figure {self.id!r}: bar needs non-empty x and y")
            if len(self.x) != len(self.y):
                raise ValueError(f"figure {self.id!r}: bar x/y length mismatch")
        elif self.kind == "line":
            if not self.series and not (self.x and self.y):
                raise ValueError(f"figure {self.id!r}: line needs series or x+y")
            if not self.series and len(self.x) != len(self.y):
                raise ValueError(f"figure {self.id!r}: line x/y length mismatch")
        elif self.kind == "scatter":
            if not self.points:
                raise ValueError(f"figure {self.id!r}: scatter needs points")
        elif self.kind == "table":
            if not self.columns or not self.rows:
                raise ValueError(f"figure {self.id!r}: table needs columns and rows")
            if any(len(r) != len(self.columns) for r in self.rows):
                raise ValueError(f"figure {self.id!r}: table row width != column count")
        elif self.kind == "image" and not self.src:
            raise ValueError(f"figure {self.id!r}: image needs src")
        return self


class Article(BaseModel):
    """A long-form deep dive: metadata, a Markdown body, and figures."""

    slug: str
    title: str
    subtitle: str = ""
    dek: str = ""
    author: str = "Rob Savage"
    date: str = ""
    tags: list[str] = Field(default_factory=list)
    hero_image: str | None = None
    canonical: str | None = None
    body_md: str = ""
    figures: list[Figure] = Field(default_factory=list)

    @field_validator("date", mode="before")
    @classmethod
    def _coerce_date(cls, v: object) -> str:
        """Accept a YAML date/datetime (or string) and store an ISO date string."""
        if v is None:
            return ""
        iso = getattr(v, "isoformat", None)
        if callable(iso):
            return str(iso())[:10]
        return str(v)

    @property
    def figures_by_id(self) -> dict[str, Figure]:
        """Map figure id → Figure, raising on duplicate ids."""
        out: dict[str, Figure] = {}
        for fig in self.figures:
            if fig.id in out:
                raise ArticleError(f"duplicate figure id {fig.id!r} in {self.slug!r}")
            out[fig.id] = fig
        return out


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split a ``---``-fenced YAML frontmatter block from the Markdown body.

    Args:
        text: Raw file contents.

    Returns:
        A ``(frontmatter_yaml, body_markdown)`` tuple.

    Raises:
        ArticleError: If the opening or closing fence is missing.
    """
    stripped = text.lstrip("﻿").lstrip()
    if not stripped.startswith(_FRONTMATTER_FENCE):
        raise ArticleError("article must start with a '---' YAML frontmatter block")
    rest = stripped[len(_FRONTMATTER_FENCE) :]
    end = rest.find(f"\n{_FRONTMATTER_FENCE}")
    if end == -1:
        raise ArticleError("frontmatter block is not closed with '---'")
    fm = rest[:end]
    body = rest[end + len(_FRONTMATTER_FENCE) + 1 :].lstrip("\n")
    return fm, body


def load_article(src_dir: Path) -> Article:
    """Load and validate an article from its source directory.

    Args:
        src_dir: Directory containing ``article.md`` (and optionally
            ``figures.yaml``).

    Returns:
        The validated :class:`Article`.

    Raises:
        ArticleError: If files are missing or fail validation.
    """
    md_path = src_dir / "article.md"
    if not md_path.is_file():
        raise ArticleError(f"no article.md in {src_dir}")

    fm_text, body = _split_frontmatter(md_path.read_text(encoding="utf-8"))
    try:
        meta = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise ArticleError(f"invalid frontmatter YAML in {md_path}: {exc}") from exc
    if not isinstance(meta, dict):
        raise ArticleError(f"frontmatter must be a mapping in {md_path}")

    figures = list(meta.pop("figures", []) or [])
    figures_yaml = src_dir / "figures.yaml"
    if figures_yaml.is_file():
        try:
            extra = yaml.safe_load(figures_yaml.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as exc:
            raise ArticleError(f"invalid figures.yaml in {src_dir}: {exc}") from exc
        if not isinstance(extra, list):
            raise ArticleError("figures.yaml must be a list of figure mappings")
        figures.extend(extra)

    meta.setdefault("slug", src_dir.name)
    meta["body_md"] = body
    meta["figures"] = figures

    try:
        article = Article.model_validate(meta)
    except ValidationError as exc:
        raise ArticleError(f"article {src_dir.name!r} failed validation:\n{exc}") from exc

    # Surface unresolved figure references early rather than at render.
    _ = article.figures_by_id
    return article
