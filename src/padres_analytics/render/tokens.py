"""Design tokens — single source of truth for brand colors, fonts, and layout.

Consumed by both the Jinja2/CSS template and the matplotlib style.
"""

from __future__ import annotations

from pathlib import Path

# ── Palette v2 — slick tech-forward dark; gold is data-ink/accent, never a flood
BG_DEEP = "#1A1410"  # near-black warm canvas, flat
BG_PANEL = "#211A14"  # barely-lighter panel (sparing use)
GOLD = "#FFC425"  # brand gold — data ink, key numbers, small-caps labels, hairlines
GOLD_DIM = "#B8902A"  # secondary gold for sub-accents
TEXT_WHITE = "#FFFFFF"  # titles, names, big numbers — white carries hierarchy
TEXT_PRIMARY = "#F5EFE6"  # warm off-white body
TEXT_SECONDARY = "#9C9189"  # muted gray-sand — labels, axis text, captions
ROW_ALT = "#27201A"  # alternating row tint
HIGHLIGHT_BG = "#3D2F1F"  # Padre-row band
HIGHLIGHT_EDGE = "#FFC425"  # 4px left border on the Padre row
POSITIVE = "#7FB069"  # green for positive deltas
NEGATIVE = "#C4574E"  # red for negative deltas

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
