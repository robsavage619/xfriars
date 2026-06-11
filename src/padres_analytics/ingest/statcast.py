"""Baseball Savant ingest — pulls current-season Statcast data into padres.db.

Four tables are refreshed per run:
  statcast_batter_percentile_ranks
  statcast_batting_expected
  statcast_sprint_speed
  statcast_batter_exitvelo_barrels

Uses pybaseball (already a project dependency) to fetch from Baseball Savant.
Each table's existing rows for the target season are deleted and replaced on
every run — no partial appends.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from padres_analytics.ingest.runs import record_run

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Minimum thresholds passed to pybaseball (0 = all players)
_MIN_PA = 0
_MIN_BBE = 0
_MIN_SPRINT_OPP = 0


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _ingest_percentile_ranks(
    conn: duckdb.DuckDBPyConnection,
    season: int,
) -> int:
    """Fetch Statcast batter percentile rankings and upsert into padres.db.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year.

    Returns:
        Number of rows inserted.
    """
    import pybaseball

    df = pybaseball.statcast_batter_percentile_ranks(season)
    if df is None or df.empty:
        logger.warning("statcast percentile_ranks: empty response for season=%d", season)
        return 0

    now = _now()
    rows = [
        (
            int(r["player_id"]),
            str(r["player_name"]),
            int(r["year"]),
            r["xwoba"] if not _nan(r["xwoba"]) else None,
            r["xba"] if not _nan(r["xba"]) else None,
            r["xslg"] if not _nan(r["xslg"]) else None,
            r["xiso"] if not _nan(r["xiso"]) else None,
            r["xobp"] if not _nan(r["xobp"]) else None,
            r["brl"] if not _nan(r["brl"]) else None,
            r["brl_percent"] if not _nan(r["brl_percent"]) else None,
            r["exit_velocity"] if not _nan(r["exit_velocity"]) else None,
            r["max_ev"] if not _nan(r["max_ev"]) else None,
            r["hard_hit_percent"] if not _nan(r["hard_hit_percent"]) else None,
            r["k_percent"] if not _nan(r["k_percent"]) else None,
            r["bb_percent"] if not _nan(r["bb_percent"]) else None,
            r["whiff_percent"] if not _nan(r["whiff_percent"]) else None,
            r["chase_percent"] if not _nan(r["chase_percent"]) else None,
            r["arm_strength"] if not _nan(r["arm_strength"]) else None,
            r["sprint_speed"] if not _nan(r["sprint_speed"]) else None,
            r["oaa"] if not _nan(r["oaa"]) else None,
            r["bat_speed"] if not _nan(r["bat_speed"]) else None,
            r["squared_up_rate"] if not _nan(r["squared_up_rate"]) else None,
            r["swing_length"] if not _nan(r["swing_length"]) else None,
            now,
        )
        for r in df.to_dict(orient="records")
    ]

    conn.execute("DELETE FROM statcast_batter_percentile_ranks WHERE year = ?", [season])
    conn.executemany(
        """
        INSERT INTO statcast_batter_percentile_ranks
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    logger.info("statcast percentile_ranks: inserted %d rows for season=%d", len(rows), season)
    return len(rows)


def _ingest_batting_expected(
    conn: duckdb.DuckDBPyConnection,
    season: int,
) -> int:
    """Fetch Statcast expected batting stats and upsert into padres.db.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year.

    Returns:
        Number of rows inserted.
    """
    import pybaseball

    df = pybaseball.statcast_batter_expected_stats(season, minPA=_MIN_PA)
    if df is None or df.empty:
        logger.warning("statcast batting_expected: empty response for season=%d", season)
        return 0

    df = df.rename(columns={"last_name, first_name": "player_name"})
    now = _now()
    rows = [
        (
            int(r["player_id"]),
            str(r["player_name"]),
            int(r["year"]),
            int(r["pa"]) if not _nan(r["pa"]) else None,
            int(r["bip"]) if not _nan(r["bip"]) else None,
            float(r["ba"]) if not _nan(r["ba"]) else None,
            float(r["est_ba"]) if not _nan(r["est_ba"]) else None,
            float(r["slg"]) if not _nan(r["slg"]) else None,
            float(r["est_slg"]) if not _nan(r["est_slg"]) else None,
            float(r["woba"]) if not _nan(r["woba"]) else None,
            float(r["est_woba"]) if not _nan(r["est_woba"]) else None,
            now,
        )
        for r in df.to_dict(orient="records")
    ]

    conn.execute("DELETE FROM statcast_batting_expected WHERE year = ?", [season])
    conn.executemany(
        "INSERT INTO statcast_batting_expected VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    logger.info("statcast batting_expected: inserted %d rows for season=%d", len(rows), season)
    return len(rows)


def _ingest_sprint_speed(
    conn: duckdb.DuckDBPyConnection,
    season: int,
) -> int:
    """Fetch Statcast sprint speed and upsert into padres.db.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year.

    Returns:
        Number of rows inserted.
    """
    import pybaseball

    df = pybaseball.statcast_sprint_speed(season, min_opp=_MIN_SPRINT_OPP)
    if df is None or df.empty:
        logger.warning("statcast sprint_speed: empty response for season=%d", season)
        return 0

    df = df.rename(columns={"last_name, first_name": "player_name"})
    now = _now()
    rows = [
        (
            int(r["player_id"]),
            str(r["player_name"]),
            season,
            float(r["sprint_speed"]) if not _nan(r["sprint_speed"]) else None,
            int(r["competitive_runs"]) if not _nan(r["competitive_runs"]) else None,
            now,
        )
        for r in df.to_dict(orient="records")
    ]

    conn.execute("DELETE FROM statcast_sprint_speed WHERE year = ?", [season])
    conn.executemany(
        "INSERT INTO statcast_sprint_speed VALUES (?,?,?,?,?,?)",
        rows,
    )
    logger.info("statcast sprint_speed: inserted %d rows for season=%d", len(rows), season)
    return len(rows)


def _ingest_exitvelo_barrels(
    conn: duckdb.DuckDBPyConnection,
    season: int,
) -> int:
    """Fetch Statcast exit velocity / barrel data and upsert into padres.db.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year.

    Returns:
        Number of rows inserted.
    """
    import pybaseball

    df = pybaseball.statcast_batter_exitvelo_barrels(season, minBBE=_MIN_BBE)
    if df is None or df.empty:
        logger.warning("statcast exitvelo_barrels: empty response for season=%d", season)
        return 0

    df = df.rename(columns={"last_name, first_name": "player_name"})
    now = _now()
    rows = [
        (
            int(r["player_id"]),
            str(r["player_name"]),
            season,
            int(r["attempts"]) if not _nan(r["attempts"]) else None,
            float(r["avg_hit_speed"]) if not _nan(r["avg_hit_speed"]) else None,
            float(r["max_hit_speed"]) if not _nan(r["max_hit_speed"]) else None,
            int(r["barrels"]) if not _nan(r["barrels"]) else None,
            float(r["brl_percent"]) if not _nan(r["brl_percent"]) else None,
            now,
        )
        for r in df.to_dict(orient="records")
    ]

    conn.execute("DELETE FROM statcast_batter_exitvelo_barrels WHERE year = ?", [season])
    conn.executemany(
        "INSERT INTO statcast_batter_exitvelo_barrels VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    logger.info("statcast exitvelo_barrels: inserted %d rows for season=%d", len(rows), season)
    return len(rows)


def _nan(v: object) -> bool:
    """Return True if v is a float NaN (pandas NA comes in as float nan)."""
    try:
        import math

        return math.isnan(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def ingest_statcast(
    conn: duckdb.DuckDBPyConnection,
    season: int,
) -> dict[str, int]:
    """Pull all four Statcast tables from Baseball Savant for a given season.

    Existing rows for ``season`` are deleted and replaced on each call.
    Uses pybaseball's cached HTTP layer — run ``pybaseball.cache.enable()``
    before calling to avoid redundant network requests during development.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year (e.g. 2026).

    Returns:
        Dict mapping table name → rows inserted.

    Raises:
        RuntimeError: If all four fetches fail (individual failures are logged
            but do not abort the run).
    """
    source = f"baseball-savant/statcast/{season}"
    results: dict[str, int] = {}
    errors: list[str] = []

    fetchers = [
        ("statcast_batter_percentile_ranks", _ingest_percentile_ranks),
        ("statcast_batting_expected", _ingest_batting_expected),
        ("statcast_sprint_speed", _ingest_sprint_speed),
        ("statcast_batter_exitvelo_barrels", _ingest_exitvelo_barrels),
    ]

    for table, fn in fetchers:
        try:
            with record_run(conn, f"{source}/{table}") as run:
                n = fn(conn, season)
                results[table] = n
                run["rows_written"] = n
        except Exception as exc:
            logger.error("statcast ingest failed for %s season=%d: %s", table, season, exc)
            errors.append(f"{table}: {exc}")
            results[table] = 0

    if len(errors) == len(fetchers):
        raise RuntimeError(
            f"All Statcast fetches failed for season={season}:\n" + "\n".join(errors)
        )

    return results
