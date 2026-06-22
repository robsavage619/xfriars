"""Project paths and configuration constants."""

from __future__ import annotations

import logging
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

# Allow env override so tests and alternate worktrees can point elsewhere.
if "PADRES_DB_PATH" in os.environ:
    _env = Path(os.environ["PADRES_DB_PATH"])
    if _env.suffix != ".db":
        raise ValueError(f"PADRES_DB_PATH must end in .db, got: {_env}")
    DUCKDB_PATH = _env
else:
    DUCKDB_PATH = DATA_DIR / "duckdb" / "padres.db"

# MLB API team constants — never hardcode inline; always import from here.
PADRES_TEAM_ID = 135  # MLB Stats API
BREF = "SDP"  # Baseball-Reference bwar_* tables
RETRO = "SDN"  # Retrosheet event/game files

MLB_STATS_API_BASE = "https://statsapi.mlb.com/api/v1"

INBOX_DIR = PROJECT_ROOT / "inbox"
CARDS_DIR = DATA_DIR / "cards"

# Long-form articles (Medium deep dives). Sources are authored under
# articles/<slug>/; rendered output lands in docs/articles/<slug>/ which is the
# GitHub Pages root, so each article gets a public URL for Medium's
# "Import a story" flow (sets the canonical link back to us).
ARTICLES_SRC_DIR = PROJECT_ROOT / "articles"
DOCS_DIR = PROJECT_ROOT / "docs"
ARTICLES_OUT_DIR = DOCS_DIR / "articles"

# Public base URL for rendered articles. Override with PADRES_PAGES_BASE_URL if a
# custom domain is configured. Trailing slash is normalized off.
PAGES_BASE_URL = os.environ.get(
    "PADRES_PAGES_BASE_URL", "https://robsavage619.github.io/xfriars"
).rstrip("/")


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger for CLI and library entrypoints.

    Args:
        level: Logging level constant. Defaults to INFO.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
