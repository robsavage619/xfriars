"""Build spatial visual datasets (spray, …) from stored event-level Statcast.

Reads ``statcast_batted_balls`` and applies the canonical coordinate transform
and the rigor harness (n / coverage / handedness / park / caveat) the card face
requires. Keeps the transform in one place so every spatial card agrees.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.candidates import SpatialDataset, SpatialPoint

if TYPE_CHECKING:
    import duckdb

# Statcast Gameday pixel → field-feet, home plate at origin, +y toward center.
_HC_X0 = 125.42
_HC_Y0 = 198.27
_HC_SCALE = 2.5

# Spray sample-size floor (below this, label as illustrative, not predictive).
_SPRAY_FLOOR = 50

_EXTRA_BASE = {"double", "triple"}


def _kind(events: str | None) -> str:
    """Map a Statcast ``events`` outcome to a spray fill class."""
    if events == "home_run":
        return "home_run"
    if events in _EXTRA_BASE:
        return events  # "double" / "triple" → gold (XBH)
    if events == "single":
        return "single"
    return "out"


def _display_name(raw: str | None, fallback: str) -> str:
    """Turn Statcast's ``"Last, First"`` into ``"First Last"``."""
    if not raw:
        return fallback
    if ", " in raw:
        last, first = raw.split(", ", 1)
        return f"{first} {last}"
    return raw


def build_spray(
    conn: duckdb.DuckDBPyConnection,
    player_id: int,
    season: int,
    *,
    vs_hand: str | None = None,
    as_of: date | None = None,
) -> SpatialDataset | None:
    """Assemble a spray-chart ``SpatialDataset`` for one hitter from stored events.

    Args:
        conn: Read connection to padres.db.
        player_id: MLBAM batter id.
        season: Season year.
        vs_hand: Filter to pitcher handedness ``"R"``/``"L"``; ``None`` = all.
        as_of: Card date; defaults to today.

    Returns:
        A validated ``SpatialDataset`` (card="spray"), or ``None`` when no
        plottable batted balls exist (missing hit coordinates included).
    """
    # Regular season only — "2024 season" must not silently include spring training
    # (game_type 'S') or postseason ('F'/'D'/'L'/'W'), which would inflate the totals.
    sql = """
        SELECT player_name, events, stand, hc_x, hc_y
        FROM statcast_batted_balls
        WHERE player_id = ? AND season = ? AND game_type = 'R'
          AND hc_x IS NOT NULL AND hc_y IS NOT NULL
    """
    params: list[object] = [player_id, season]
    if vs_hand in ("R", "L"):
        sql += " AND p_throws = ?"
        params.append(vs_hand)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return None

    name_raw = rows[0][0]
    stand = rows[0][2]
    points: list[SpatialPoint] = []
    pull = 0
    for _name, events, _stand, hc_x, hc_y in rows:
        x = (hc_x - _HC_X0) * _HC_SCALE
        y = (_HC_Y0 - hc_y) * _HC_SCALE
        points.append(SpatialPoint(x=round(x, 1), y=round(y, 1), kind=_kind(events)))
        # Pull side: RH batter pulls to left field (x<0); LH pulls to right (x>0).
        if (stand == "R" and x < 0) or (stand == "L" and x > 0):
            pull += 1

    n = len(points)
    pull_rate = pull / n if n else 0.0
    hand = {"R": "vs RHP", "L": "vs LHP"}.get(vs_hand or "", "All")
    note = f"Pull rate {pull_rate:.0%} · shift-era (post-2023); spray shows tendency, not outcomes"
    if n < _SPRAY_FLOOR:
        note = f"Small sample ({n} BBE) — illustrative, not predictive · {note}"

    name = _display_name(name_raw, str(player_id))
    return SpatialDataset(
        card="spray",
        title=name,
        subtitle=f"Batted-ball spray · {season}",
        as_of=as_of or date.today(),
        points=points,
        n=n,
        coverage=f"{season} season",
        handedness=hand,
        park="All parks",
        note=note,
        source="Baseball Savant",
        headline=f"{name} {season} spray ({n} BBE)",
        claim_scope=f"{season} season",
    )
