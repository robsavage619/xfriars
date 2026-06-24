"""Shared DuckDB query helpers for all detectors and the generic scanner."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_SD_TEAM_BREF = "SDP"


def fmt_name(raw: str) -> str:
    """Convert 'Last, First' Statcast format to 'First Last'."""
    if "," in raw:
        last, first = raw.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return raw


def ordinal(n: float | int) -> str:
    """Format a number as an ordinal string ('1st', '42nd', '95th')."""
    i = round(n)
    if 11 <= i % 100 <= 13:
        return f"{i}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(i % 10, "th")
    return f"{i}{suffix}"


def resolve_table(conn: duckdb.DuckDBPyConnection, name: str) -> str:
    """Prefer main. (padres.db) over hist. (trades.db) when the table is populated.

    Args:
        conn: Connection with hist attached.
        name: Unqualified table name.

    Returns:
        Qualified table reference (plain name for main., 'hist.name' for fallback).
    """
    # COUNT(*) rather than MAX(year): game-grain tables key on ``season``/``game_date``
    # and have no ``year`` column, so a year probe wrongly falls through to hist.
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
        if row and row[0]:
            return name
    except Exception:
        pass
    return f"hist.{name}"


def max_year(conn: duckdb.DuckDBPyConnection, table: str) -> int | None:
    """Return the maximum year in a table, checking main. then hist.

    Args:
        conn: Connection with hist attached.
        table: Unqualified table name.

    Returns:
        Maximum year, or None if table is absent in both schemas.
    """
    src = resolve_table(conn, table)
    try:
        row = conn.execute(f"SELECT MAX(year) FROM {src}").fetchone()
        return row[0] if row and row[0] is not None else None
    except Exception:
        return None


def padre_ids(conn: duckdb.DuckDBPyConnection, year: int) -> set[int]:
    """Return MLBAM IDs for all Padre players in bwar_player_seasons for a given year.

    Args:
        conn: Connection with hist attached.
        year: Season year.

    Returns:
        Set of mlb_id integers on the SDP roster that year.
    """
    try:
        rows = conn.execute(
            "SELECT mlb_id FROM hist.bwar_player_seasons WHERE year_id = ? AND team_id = ?",
            [year, _SD_TEAM_BREF],
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        logger.debug("padre_ids: bwar_player_seasons not available for year=%d", year)
        return set()


_SD_MLBAM = 135


def padre_ids_roster(conn: duckdb.DuckDBPyConnection, season: int) -> set[int]:
    """Return Padre MLBAM IDs from the 40-man roster for a season.

    The 40-man (``team_rosters``) is the authoritative membership source, unlike
    ``bwar_player_seasons`` which records WAR accrued for a team. Prefers the
    freshly-ingested real ``main.team_rosters`` (``pad ingest roster``) over the
    simulated ``hist.team_rosters`` so non-Padres can't leak into Padre cards.

    Args:
        conn: Connection with hist attached.
        season: Roster season.

    Returns:
        Set of mlb_id integers on the SD 40-man that season, or empty if absent.
    """
    queries = (
        ("team_rosters", "team_id = ?", [_SD_MLBAM, season]),  # fresh real ingest (main)
        ("hist.team_rosters", "team_bref = 'SD'", [season]),  # simulated fallback
    )
    for table, team_clause, params in queries:
        try:
            rows = conn.execute(
                f"SELECT player_id FROM {table} "
                f"WHERE {team_clause} AND season = ? AND roster_type = '40Man'",
                params,
            ).fetchall()
        except Exception:
            continue
        if rows:
            return {r[0] for r in rows}
    logger.debug("padre_ids_roster: no team_rosters available for season=%d", season)
    return set()


def padre_ids_latest(conn: duckdb.DuckDBPyConnection) -> set[int]:
    """Return Padre IDs from the most recent bwar year available.

    Args:
        conn: Connection with hist attached.

    Returns:
        Set of mlb_id integers, or empty set if bwar data is absent.
    """
    try:
        row = conn.execute("SELECT MAX(year_id) FROM hist.bwar_player_seasons").fetchone()
        if not row or row[0] is None:
            return set()
        return padre_ids(conn, row[0])
    except Exception:
        logger.debug("padre_ids_latest: bwar_player_seasons not available")
        return set()
