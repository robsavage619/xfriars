"""MLB logo and player photo asset utilities.

Downloads are cached under data/mlb_assets/ (gitignored).
All paths returned are absolute and suitable for file:// loading at render time.
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Path constants ─────────────────────────────────────────────────────────────

_ASSETS_DIR = Path(__file__).parents[3] / "data" / "mlb_assets"
_LOGOS_DIR = _ASSETS_DIR / "logos"
_PHOTOS_DIR = _ASSETS_DIR / "photos"

# ── BRef team code → MLBAM team ID ────────────────────────────────────────────

BREF_TO_MLBAM: dict[str, int] = {
    "ARI": 109,
    "ATL": 144,
    "BAL": 110,
    "BOS": 111,
    "CHN": 112,
    "CHA": 145,
    "CIN": 113,
    "CLE": 114,
    "COL": 115,
    "DET": 116,
    "HOU": 117,
    "KCA": 118,
    "LAA": 108,
    "LAN": 119,
    "MIA": 146,
    "MIL": 158,
    "MIN": 142,
    "NYN": 121,
    "NYA": 147,
    "OAK": 133,
    "PHI": 143,
    "PIT": 134,
    "SDP": 135,
    "SEA": 136,
    "SFN": 137,
    "STL": 138,
    "TBA": 139,
    "TEX": 140,
    "TOR": 141,
    "WSN": 120,
    # common short-code aliases used in some views
    "SD": 135,
    "LAD": 119,
    "SF": 137,
    "NYY": 147,
    "NYM": 121,
    "WSH": 120,
    "CWS": 145,
    "CHC": 112,
    "KC": 118,
    "TB": 139,
}

_LOGO_URL = "https://www.mlbstatic.com/team-logos/{mlbam_id}.svg"
_PHOTO_URL = (
    "https://img.mlbstatic.com/mlb-photos/image/upload"
    "/d_people:generic:headshot:67:current.png"
    "/w_213,q_auto:best/v1/people/{mlb_id}/headshot/67/current"
)


# ── Download helpers ───────────────────────────────────────────────────────────


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "xFriars/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp, dest.open("wb") as f:
        f.write(resp.read())


def team_logo_path(bref_code: str) -> Path | None:
    """Return the local path to a team logo SVG, downloading if needed.

    Args:
        bref_code: Baseball Reference team code (e.g. "SDP", "LAN").

    Returns:
        Absolute Path to the cached SVG, or None if the code is unknown.
    """
    mlbam_id = BREF_TO_MLBAM.get(bref_code.upper())
    if mlbam_id is None:
        return None

    dest = _LOGOS_DIR / f"{mlbam_id}.svg"
    if not dest.exists():
        url = _LOGO_URL.format(mlbam_id=mlbam_id)
        try:
            _fetch(url, dest)
            logger.debug("Downloaded team logo: %s → %s", bref_code, dest)
        except Exception as exc:
            logger.warning("Failed to download logo for %s: %s", bref_code, exc)
            return None

    return dest


def player_photo_path(mlb_id: int) -> Path | None:
    """Return the local path to a player headshot PNG, downloading if needed.

    Args:
        mlb_id: MLBAM player ID.

    Returns:
        Absolute Path to the cached PNG, or None on failure.
    """
    dest = _PHOTOS_DIR / f"{mlb_id}.png"
    if not dest.exists():
        url = _PHOTO_URL.format(mlb_id=mlb_id)
        try:
            _fetch(url, dest)
            logger.debug("Downloaded player photo: %d → %s", mlb_id, dest)
        except Exception as exc:
            logger.warning("Failed to download photo for %d: %s", mlb_id, exc)
            return None

    return dest


def mlb_photo_url(mlb_id: int) -> str:
    """Return the MLB CDN photo URL for a player (for browser/React use)."""
    return _PHOTO_URL.format(mlb_id=mlb_id)


def mlb_team_logo_url(bref_code: str) -> str | None:
    """Return the MLB CDN logo URL for a team (for browser/React use)."""
    mlbam_id = BREF_TO_MLBAM.get(bref_code.upper())
    if mlbam_id is None:
        return None
    return _LOGO_URL.format(mlbam_id=mlbam_id)
