"""Event-level Statcast ingest — per-batted-ball rows for spatial visuals.

Unlike :mod:`padres_analytics.ingest.statcast` (season-aggregate leaderboards),
this pulls pitch-by-pitch data via ``pybaseball.statcast_batter`` and keeps the
balls in play, with the raw ``hc_x``/``hc_y`` coordinates the spray chart needs.

Rows for a (player_id, season) are deleted and replaced on each run — no partial
appends. The coordinate transform to field-feet happens at render time, not here,
so the canonical Statcast values stay intact for provenance.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from padres_analytics.ingest.runs import record_run

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _nan(v: object) -> bool:
    """Return True if v is a float NaN (pandas NA comes in as float nan)."""
    try:
        return math.isnan(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _f(v: object) -> float | None:
    return None if _nan(v) else float(v)  # type: ignore[arg-type]


def _i(v: object) -> int | None:
    return None if _nan(v) else int(float(v))  # type: ignore[arg-type]


def _s(v: object) -> str | None:
    return None if v is None or _nan(v) else str(v)


def ingest_batted_balls(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    player_id: int,
    player_name: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> int:
    """Fetch one hitter's balls in play for a season and store them.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year (e.g. 2024).
        player_id: MLBAM batter id.
        player_name: Display name; resolved from the data when omitted.
        start: ISO start date; defaults to ``{season}-03-01``.
        end: ISO end date; defaults to ``{season}-11-30``.

    Returns:
        Number of batted-ball rows inserted.
    """
    import pybaseball

    start = start or f"{season}-03-01"
    end = end or f"{season}-11-30"

    df = pybaseball.statcast_batter(start, end, player_id)
    if df is None or df.empty:
        logger.warning("batted_balls: empty response for player=%d season=%d", player_id, season)
        conn.execute(
            "DELETE FROM statcast_batted_balls WHERE player_id = ? AND season = ?",
            [player_id, season],
        )
        return 0

    # Balls in play only — type 'X'. Strikeouts/walks (S/B) carry no batted-ball data.
    df = df.query("type == 'X'")
    if df.empty:
        conn.execute(
            "DELETE FROM statcast_batted_balls WHERE player_id = ? AND season = ?",
            [player_id, season],
        )
        return 0

    name = player_name
    if name is None and "player_name" in df.columns:
        name = str(df.iloc[0]["player_name"])  # "Last, First"

    now = _now()
    rows = [
        (
            player_id,
            name,
            season,
            _s(r.get("game_type")),
            r.get("game_date"),
            int(r["game_pk"]),
            int(r["at_bat_number"]),
            int(r["pitch_number"]),
            _s(r.get("events")),
            _s(r.get("bb_type")),
            _s(r.get("description")),
            _s(r.get("stand")),
            _s(r.get("p_throws")),
            _f(r.get("hc_x")),
            _f(r.get("hc_y")),
            _f(r.get("plate_x")),
            _f(r.get("plate_z")),
            _f(r.get("launch_speed")),
            _f(r.get("launch_angle")),
            _i(r.get("launch_speed_angle")),
            _f(r.get("hit_distance_sc")),
            _f(r.get("estimated_woba_using_speedangle")),
            now,
        )
        for r in df.to_dict(orient="records")
    ]

    conn.execute(
        "DELETE FROM statcast_batted_balls WHERE player_id = ? AND season = ?",
        [player_id, season],
    )
    conn.executemany(
        """
        INSERT INTO statcast_batted_balls VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    logger.info(
        "batted_balls: inserted %d rows for player=%d season=%d", len(rows), player_id, season
    )
    return len(rows)


def ingest_pitches(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    pitcher_id: int,
    pitcher_name: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> int:
    """Fetch one pitcher's pitch-by-pitch data for a season and store it.

    Keeps every pitch carrying a ``pitch_type`` and movement (``pfx_x``/``pfx_z``),
    plus location (``plate_x``/``plate_z``) and release — the raw inputs for
    arsenal/movement, zones, and location heatmaps.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year.
        pitcher_id: MLBAM pitcher id.
        pitcher_name: Display name; resolved from the data when omitted.
        start: ISO start date; defaults to ``{season}-03-01``.
        end: ISO end date; defaults to ``{season}-11-30``.

    Returns:
        Number of pitch rows inserted.
    """
    import pybaseball

    start = start or f"{season}-03-01"
    end = end or f"{season}-11-30"

    df = pybaseball.statcast_pitcher(start, end, pitcher_id)
    if df is None or df.empty:
        logger.warning("pitches: empty response for pitcher=%d season=%d", pitcher_id, season)
        conn.execute(
            "DELETE FROM statcast_pitches WHERE pitcher_id = ? AND season = ?",
            [pitcher_id, season],
        )
        return 0

    df = df.query("pitch_type.notna()", engine="python")
    if df.empty:
        conn.execute(
            "DELETE FROM statcast_pitches WHERE pitcher_id = ? AND season = ?",
            [pitcher_id, season],
        )
        return 0

    name = pitcher_name
    if name is None and "player_name" in df.columns:
        name = str(df.iloc[0]["player_name"])

    now = _now()
    rows = [
        (
            pitcher_id,
            name,
            season,
            _s(r.get("game_type")),
            r.get("game_date"),
            int(r["game_pk"]),
            int(r["at_bat_number"]),
            int(r["pitch_number"]),
            _s(r.get("pitch_type")),
            _f(r.get("release_speed")),
            _f(r.get("pfx_x")),
            _f(r.get("pfx_z")),
            _f(r.get("plate_x")),
            _f(r.get("plate_z")),
            _f(r.get("sz_top")),
            _f(r.get("sz_bot")),
            _f(r.get("release_pos_x")),
            _f(r.get("release_pos_z")),
            _s(r.get("description")),
            _s(r.get("stand")),
            _s(r.get("p_throws")),
            now,
        )
        for r in df.to_dict(orient="records")
    ]

    conn.execute(
        "DELETE FROM statcast_pitches WHERE pitcher_id = ? AND season = ?",
        [pitcher_id, season],
    )
    conn.executemany(
        "INSERT INTO statcast_pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    logger.info("pitches: inserted %d rows for pitcher=%d season=%d", len(rows), pitcher_id, season)
    return len(rows)


def ingest_batter_pitches(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    batter_id: int,
    batter_name: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> int:
    """Fetch every pitch a hitter faced for a season (not just balls in play).

    Keeps location, the Statcast attack ``zone``, the pitch ``description`` and
    ``type`` (swing vs take), ``delta_run_exp`` (pitch run value), and the bat-
    tracking fields — the inputs for swing/take run value and bat-speed cards.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year.
        batter_id: MLBAM batter id.
        batter_name: Display name; resolved from the data when omitted.
        start: ISO start date; defaults to ``{season}-03-01``.
        end: ISO end date; defaults to ``{season}-11-30``.

    Returns:
        Number of pitch rows inserted.
    """
    import pybaseball

    start = start or f"{season}-03-01"
    end = end or f"{season}-11-30"

    df = pybaseball.statcast_batter(start, end, batter_id)
    if df is None or df.empty:
        logger.warning("batter_pitches: empty for batter=%d season=%d", batter_id, season)
        conn.execute(
            "DELETE FROM statcast_batter_pitches WHERE batter_id = ? AND season = ?",
            [batter_id, season],
        )
        return 0

    df = df.query("pitch_type.notna()", engine="python")
    name = batter_name
    if name is None and "player_name" in df.columns:
        name = str(df.iloc[0]["player_name"])

    now = _now()
    rows = [
        (
            batter_id,
            name,
            season,
            _s(r.get("game_type")),
            r.get("game_date"),
            int(r["game_pk"]),
            int(r["at_bat_number"]),
            int(r["pitch_number"]),
            _s(r.get("pitch_type")),
            _f(r.get("plate_x")),
            _f(r.get("plate_z")),
            _f(r.get("sz_top")),
            _f(r.get("sz_bot")),
            _i(r.get("zone")),
            _s(r.get("description")),
            _s(r.get("type")),
            _f(r.get("delta_run_exp")),
            _f(r.get("bat_speed")),
            _f(r.get("swing_length")),
            _f(r.get("estimated_woba_using_speedangle")),
            _s(r.get("stand")),
            _s(r.get("p_throws")),
            now,
        )
        for r in df.to_dict(orient="records")
    ]

    conn.execute(
        "DELETE FROM statcast_batter_pitches WHERE batter_id = ? AND season = ?",
        [batter_id, season],
    )
    conn.executemany(
        "INSERT INTO statcast_batter_pitches VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    logger.info(
        "batter_pitches: inserted %d rows for batter=%d season=%d", len(rows), batter_id, season
    )
    return len(rows)


def ingest_batted_balls_for(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    players: list[tuple[int, str | None]],
) -> dict[str, int]:
    """Ingest batted balls for several hitters; one ``ingest_runs`` row each.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year.
        players: ``(player_id, player_name)`` pairs.

    Returns:
        Dict mapping ``"<name|id>"`` → rows inserted. Per-player failures are
        logged and recorded as 0 rather than aborting the batch.
    """
    source = f"baseball-savant/batted-balls/{season}"
    results: dict[str, int] = {}
    for player_id, player_name in players:
        key = player_name or str(player_id)
        try:
            with record_run(conn, f"{source}/{player_id}") as run:
                n = ingest_batted_balls(conn, season, player_id, player_name)
                results[key] = n
                run["rows_written"] = n
        except Exception as exc:
            logger.error("batted_balls ingest failed for player=%d: %s", player_id, exc)
            results[key] = 0
    return results
