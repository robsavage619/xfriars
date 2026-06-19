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


def build_hr_spray(
    conn: duckdb.DuckDBPyConnection,
    player_id: int,
    season: int,
    *,
    as_of: date | None = None,
) -> SpatialDataset | None:
    """Assemble a home-run spray ``SpatialDataset`` — landing spots + true distance.

    Distance is Statcast's ``hit_distance_sc`` (a trajectory model), never derived
    from the landing coordinates. The longest HR is labeled on the card.

    Args:
        conn: Read connection to padres.db.
        player_id: MLBAM batter id.
        season: Season year.
        as_of: Card date; defaults to today.

    Returns:
        A validated ``SpatialDataset`` (card="hr"), or ``None`` when the hitter
        has no plottable regular-season home runs.
    """
    rows = conn.execute(
        """
        SELECT player_name, hc_x, hc_y, hit_distance_sc
        FROM statcast_batted_balls
        WHERE player_id = ? AND season = ? AND game_type = 'R'
          AND events = 'home_run' AND hc_x IS NOT NULL AND hc_y IS NOT NULL
        """,
        [player_id, season],
    ).fetchall()
    if not rows:
        return None

    name_raw = rows[0][0]
    longest = max((r[3] for r in rows if r[3] is not None), default=None)
    dists = [r[3] for r in rows if r[3] is not None]
    avg_dist = sum(dists) / len(dists) if dists else None

    points: list[SpatialPoint] = []
    for _name, hc_x, hc_y, dist in rows:
        x = round((hc_x - _HC_X0) * _HC_SCALE, 1)
        y = round((_HC_Y0 - hc_y) * _HC_SCALE, 1)
        label = f"{dist:.0f} ft" if (dist is not None and dist == longest) else None
        points.append(SpatialPoint(x=x, y=y, kind="home_run", value=dist, label=label))

    n = len(points)
    name = _display_name(name_raw, str(player_id))
    ctx = ""
    if longest is not None:
        ctx = f"Longest {longest:.0f} ft"
        if avg_dist is not None:
            ctx += f" · avg {avg_dist:.0f} ft"
    return SpatialDataset(
        card="hr",
        title=name,
        subtitle=f"Home-run spray · {season}",
        as_of=as_of or date.today(),
        points=points,
        hero={"value": str(n), "label": "Home Runs", "context": ctx},
        n=n,
        coverage=f"{season} season",
        handedness="All",
        park="All parks",
        note="Landing direction · distance = Statcast hit_distance_sc (true carry)",
        source="Baseball Savant",
        headline=f"{name} {season} home runs ({n})",
        claim_scope=f"{season} season",
    )
