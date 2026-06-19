"""Design tokens — single source of truth for brand colors, fonts, and layout.

Consumed by both the Jinja2/CSS template and the matplotlib style.
"""

from __future__ import annotations

from pathlib import Path

# ── Palette v3 — editorial light ("Goldsberry"): paper canvas, espresso ink,
# gold demoted to a single hairline accent. No flood, no glow. The number is the
# loudest thing on the card via size/weight, not color.
PAPER = "#F4F1EA"  # warm paper canvas — flat, the card background
PAPER_PANEL = "#ECE6DB"  # barely-darker surface for subtle bands/alt rows
INK = "#1C1714"  # near-black espresso — names, titles, the big numbers
INK_SOFT = "#5A4636"  # softened brown-ink for secondary emphasis
BROWN = "#4A3526"  # deep Friar brown — top rule, wordmark, kicker accent
BROWN_DIM = "#8A6A4A"  # muted brown — kickers, small-caps labels
GOLD = "#C99A2E"  # brand gold, deepened for paper — single accent rule only
TEXT_MUTED = "#9A8E80"  # warm gray-sand — captions, axis text, sub-labels
HAIRLINE = "rgba(28, 23, 20, 0.12)"  # thin dividers on paper
HOT = "#C0392B"  # protagonist accent on scatter (Savant-hot, editorial red)

ROW_ALT = PAPER_PANEL  # alternating row tint
HIGHLIGHT_BG = "#F3E7CB"  # gold wash for the Padre row (light)
HIGHLIGHT_EDGE = GOLD  # gold left border on the Padre row
POSITIVE = "#3B6D11"  # green for positive deltas (legible on paper)
NEGATIVE = "#A32D2D"  # red for negative deltas

# ── Back-compat aliases — existing imports/templates keep working, now light ───
BG_DEEP = PAPER  # was the dark canvas; now the paper canvas
BG_PANEL = PAPER_PANEL
TEXT_WHITE = INK  # "white carries hierarchy" → now ink carries it
TEXT_PRIMARY = INK
TEXT_SECONDARY = TEXT_MUTED
GOLD_DIM = BROWN_DIM

# ── Canvas ─────────────────────────────────────────────────────────────────────
# Legacy landscape cards (table_card, bar_card) — unchanged.
CANVAS_W = 1600
CANVAS_H = 900
# Playwright renders at 800x450 CSS px with device_scale_factor=2 -> 1600x900 px
VIEWPORT_W = 800
VIEWPORT_H = 450
DEVICE_SCALE = 2

# New ChartDataset cards — mobile-first portrait (4:5), the X-feed sweet spot.
# 540x675 CSS px at device_scale 2 -> 1080x1350 px.
CARD_VIEWPORT_W = 540
CARD_VIEWPORT_H = 675
# Square alt (1080x1080) for cards that read better balanced.
CARD_SQUARE_VIEWPORT_W = 540
CARD_SQUARE_VIEWPORT_H = 540

# ── Typography ─────────────────────────────────────────────────────────────────
FONT_DISPLAY = "Barlow Condensed"  # titles — vendored woff2/ttf, OFL
FONT_BODY = "Inter"  # body + numbers — vendored woff2/ttf, OFL

# ── Font files (absolute paths for file:// loading at render time) ─────────────
FONTS_DIR = Path(__file__).parent / "templates" / "fonts"
ASSETS_DIR = Path(__file__).parent / "templates" / "assets"
STATIC_DIR = Path(__file__).parent / "static"
XFRIARS_LOGO_PNG = ASSETS_DIR / "xfriars_logo.png"
D3_JS = STATIC_DIR / "d3.v7.min.js"
PLOT_JS = STATIC_DIR / "plot.min.js"  # Observable Plot, vendored (P1+)
INTER_TTF = FONTS_DIR / "InterVariable.ttf"
BARLOW_REGULAR_TTF = FONTS_DIR / "BarlowCondensed-Regular.ttf"
BARLOW_SEMIBOLD_TTF = FONTS_DIR / "BarlowCondensed-SemiBold.ttf"
BARLOW_BOLD_TTF = FONTS_DIR / "BarlowCondensed-Bold.ttf"
BEBAS_NEUE_TTF = FONTS_DIR / "BebasNeue-Regular.ttf"
DM_SANS_TTF = FONTS_DIR / "DMSans-Variable.ttf"
BIG_SHOULDERS_TTF = FONTS_DIR / "BigShouldersDisplay-Variable.ttf"
SPACE_GROTESK_TTF = FONTS_DIR / "SpaceGrotesk-Variable.ttf"
