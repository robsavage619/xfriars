"""Scaffold a new article source from a template, optionally seeded from a story angle."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class ScaffoldError(ValueError):
    """Raised when an article cannot be scaffolded (e.g. slug clash)."""


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lower-kebab a string for use as an article slug."""
    return _SLUG_RE.sub("-", text.strip().lower()).strip("-")


_TEMPLATE = """\
---
title: {title}
subtitle: {subtitle}
dek: {dek}
author: Rob Savage
date: {date}
tags: [Padres, Analytics]
# hero_image: hero.jpg   # optional — drop a file in this folder and uncomment
figures:
  - id: example
    kind: bar
    title: "EXAMPLE — replace with a real figure"
    caption: "Placeholder data. Delete this figure once you add your own."
    source: "FanGraphs"
    x: [Tatis, Machado, Bogaerts, Cronenworth]
    y: [3.8, 3.1, 2.4, 1.6]
    highlight: Tatis
    y_label: fWAR
---

Open with the scene, not the thesis. One concrete moment a casual fan would
recognize — a swing, a game, a number that jumped — then widen out to the
question this piece answers.

## The setup

Explain the idea in plain language before any jargon. When you introduce a
stat, define it in the same sentence the first time it appears.

[[figure:example]]

## What the numbers say

Let the figure carry the weight; the prose points at what to notice. Every
number in the text should be one a reader can find on the card.

## What it means

Land the takeaway. Keep the register of a sharp friend who happens to know the
numbers cold — confident, not breathless. No "buckle up," no "simply put."
"""


def new_article(
    out_root: Path,
    slug: str,
    title: str,
    date: str,
    subtitle: str = "",
    dek: str = "",
) -> Path:
    """Create ``out_root/<slug>/article.md`` from the starter template.

    Args:
        out_root: The ``articles/`` source root.
        slug: Article slug (already validated/slugified by the caller).
        title: Working title.
        date: ISO date string for the byline.
        subtitle: Optional deck/subtitle.
        dek: Optional standfirst paragraph.

    Returns:
        Path to the created ``article.md``.

    Raises:
        ScaffoldError: If the article directory already exists.
    """
    art_dir = out_root / slug
    if art_dir.exists():
        raise ScaffoldError(f"article {slug!r} already exists at {art_dir}")
    art_dir.mkdir(parents=True)
    md_path = art_dir / "article.md"
    md_path.write_text(
        _TEMPLATE.format(title=title, subtitle=subtitle, dek=dek, date=date),
        encoding="utf-8",
    )
    logger.info("scaffolded article %r → %s", slug, md_path)
    return md_path
