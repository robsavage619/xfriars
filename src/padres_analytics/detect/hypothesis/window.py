"""Rolling last-N-day fetch for windowed hypotheses (the recency primitive).

Season-grain metrics can't surface "what changed this week" — the value barely
moves day to day. A windowed spec instead aggregates a per-event value over the
last N days from a game-grain table, producing a leaderboard the same lenses
consume. The spec is already validated before it reaches here, so ``value_col``
and ``filter_sql`` are safe; the date column and aggregate come from fixed
allowlists, never from the LLM.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from padres_analytics.detect.hypothesis.spec import HypothesisSpec
from padres_analytics.detect.sql import fmt_name, resolve_table

if TYPE_CHECKING:
    import duckdb

# A per-player window must clear this many events before it's trustworthy.
# Pitch-grain tables carry far more rows per player per day than batted-ball
# tables, so a flat floor would let a two-game sample through — hence per-table.
MIN_WINDOW_EVENTS = 8
_MIN_EVENTS_BY_TABLE: dict[str, int] = {
    "statcast_batter_pitches": 50,
    "statcast_pitches": 50,
}

# Per-event subject columns. Pitch tables are keyed by the player whose POV they
# record, so a single hardcoded ``player_id`` silently fails on both of them.
_ID_COLS_BY_TABLE: dict[str, tuple[str, str]] = {
    "statcast_batter_pitches": ("batter_id", "batter_name"),
    "statcast_pitches": ("pitcher_id", "pitcher_name"),
}
_DEFAULT_ID_COLS = ("player_id", "player_name")

# Candidate per-event date columns, in priority order.
_DATE_COLS = ("game_date", "date")


def min_events_for(table: str) -> int:
    """Minimum per-player events required in the window for a given table."""
    return _MIN_EVENTS_BY_TABLE.get(table.split(".")[-1], MIN_WINDOW_EVENTS)


def id_columns_for(table: str) -> tuple[str, str]:
    """Return the (id_col, name_col) pair identifying the subject of each event."""
    return _ID_COLS_BY_TABLE.get(table.split(".")[-1], _DEFAULT_ID_COLS)


def date_column(conn: duckdb.DuckDBPyConnection, table: str) -> str | None:
    """Return the per-event date column for a table, or None if it is season-grain."""
    src = resolve_table(conn, table)
    try:
        info = conn.execute(f"PRAGMA table_info('{src}')").fetchall()
    except Exception:
        return None
    cols = {str(r[1]).lower() for r in info}
    return next((c for c in _DATE_COLS if c in cols), None)


def fetch_window_rows(
    conn: duckdb.DuckDBPyConnection,
    spec: HypothesisSpec,
    as_of: date,
    date_col: str,
) -> tuple[list[tuple[int, str, float]], str]:
    """Aggregate a validated metric over the last ``window.days`` days, league-wide.

    Args:
        conn: DB connection.
        spec: A *validated* windowed hypothesis spec.
        as_of: Window end date (inclusive).
        date_col: The per-event date column (from :func:`date_column`).

    Returns:
        ``(rows, resolved_table)`` where rows are (player_id, name, value),
        ordered best-first per the metric's direction. ``rate``/``differential``
        aggregate by mean; ``counting``/``ordinal`` by sum.
    """
    assert spec.window is not None
    src = resolve_table(conn, spec.table)
    agg = "AVG" if spec.metric_type in ("rate", "differential") else "SUM"
    order = "DESC" if spec.direction == "higher" else "ASC"
    where = f"AND ({spec.filter_sql})" if spec.filter_sql else ""
    start = as_of - timedelta(days=spec.window.days)
    id_col, name_col = id_columns_for(spec.table)
    min_events = min_events_for(spec.table)

    sql = f"""
        SELECT {id_col}, ANY_VALUE({name_col}), {agg}({spec.value_col})
        FROM {src}
        WHERE {date_col} BETWEEN ? AND ?
          AND {spec.value_col} IS NOT NULL {where}
        GROUP BY {id_col}
        HAVING COUNT(*) >= ?
        ORDER BY {agg}({spec.value_col}) {order}
    """
    rows = conn.execute(sql, [start, as_of, min_events]).fetchall()
    return [(int(r[0]), fmt_name(str(r[1])), float(r[2])) for r in rows], src
