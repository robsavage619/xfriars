"""Design tokens — single source of truth for brand colors, fonts, and layout.

Consumed by both the Jinja2/CSS template and the matplotlib style.
"""

from __future__ import annotations

from pathlib import Path

# ── Padres brand colors (verified) ────────────────────────────────────────────
BG_DEEP = "#1E1712"  # card background
BG_PANEL = "#2F241D"  # header band (brand brown)
GOLD = "#FFC425"  # brand gold — highlights, rules, watermark
GOLD_DIM = "#B8902A"  # secondary gold for sub-accents
TEXT_PRIMARY = "#F5EFE6"  # warm off-white
TEXT_SECONDARY = "#A89D8F"  # muted sand — labels, footers
ROW_ALT = "#27201A"  # alternating row tint
HIGHLIGHT_BG = "#3D2F1F"  # Padre-row band
HIGHLIGHT_EDGE = "#FFC425"  # 4px left border on the Padre row
POSITIVE = "#7FB069"  # green for positive deltas
NEGATIVE = "#C4574E"  # red for negative deltas

# ── Canvas ─────────────────────────────────────────────────────────────────────
CANVAS_W = 1600
CANVAS_H = 900
# Playwright renders at 800x450 CSS px with device_scale_factor=2 -> 1600x900 px
VIEWPORT_W = 800
VIEWPORT_H = 450
DEVICE_SCALE = 2

# ── Typography ─────────────────────────────────────────────────────────────────
FONT_DISPLAY = "Barlow Condensed"  # titles — vendored woff2/ttf, OFL
FONT_BODY = "Inter"  # body + numbers — vendored woff2/ttf, OFL

# ── Font files (absolute paths for file:// loading at render time) ─────────────
FONTS_DIR = Path(__file__).parent / "templates" / "fonts"
ASSETS_DIR = Path(__file__).parent / "templates" / "assets"
XFRIARS_LOGO_PNG = ASSETS_DIR / "xfriars_logo.png"
INTER_TTF = FONTS_DIR / "InterVariable.ttf"
BARLOW_REGULAR_TTF = FONTS_DIR / "BarlowCondensed-Regular.ttf"
BARLOW_SEMIBOLD_TTF = FONTS_DIR / "BarlowCondensed-SemiBold.ttf"
BARLOW_BOLD_TTF = FONTS_DIR / "BarlowCondensed-Bold.ttf"
