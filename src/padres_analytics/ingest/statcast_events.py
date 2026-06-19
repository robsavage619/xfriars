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
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    logger.info(
        "batted_balls: inserted %d rows for player=%d season=%d", len(rows), player_id, season
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
