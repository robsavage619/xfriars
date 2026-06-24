"""Context-pack assembly — what Claude sees before proposing hypotheses.

Every section is sourced from tables that already exist and is queried
defensively: a missing table degrades to an empty section, never an error, so
the pack is always producible. The pack is the LLM's grounding — it names what
is queryable (so proposals can't reference absent columns) and what has already
been explored (so it stops returning to the same well).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.discovery import discover_metrics
from padres_analytics.detect.hypothesis.store import explored_json
from padres_analytics.detect.sql import max_year, resolve_table

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Tables a hypothesis may scan — the queryable surface advertised to Claude.
_SCANNABLE_TABLES = (
    "statcast_batter_exitvelo_barrels",
    "statcast_batting_expected",
    "statcast_sprint_speed",
    "statcast_batter_percentile_ranks",
)
_SKIP_COLS = frozenset({"player_id", "year", "ingested_at", "team_id"})


def _rows(conn: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[tuple]:
    try:
        return conn.execute(sql, params or []).fetchall()
    except Exception as exc:
        logger.debug("context: query failed (%s): %s", sql.split()[0], exc)
        return []


def _numeric_columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    src = resolve_table(conn, table)
    try:
        info = conn.execute(f"PRAGMA table_info('{src}')").fetchall()
    except Exception:
        return []
    return [
        str(r[1])
        for r in info
        if str(r[2]).upper() in {"DOUBLE", "FLOAT", "INTEGER", "BIGINT", "DECIMAL"}
        and str(r[1]) not in _SKIP_COLS
    ]


def _catalog(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """The queryable surface: each scannable table with its numeric columns + freshness."""
    catalog: list[dict] = []
    for table in _SCANNABLE_TABLES:
        cols = _numeric_columns(conn, table)
        if not cols:
            continue
        catalog.append({"table": table, "max_year": max_year(conn, table), "numeric_columns": cols})
    return catalog


def _standings(conn: duckdb.DuckDBPyConnection) -> dict:
    rows = _rows(
        conn,
        """
        SELECT wins, losses, win_pct, games_back
        FROM standings
        WHERE team_id = 135
        ORDER BY season DESC
        LIMIT 1
        """,
    )
    if not rows:
        return {}
    w, ls, win_pct, gb = rows[0]
    return {"record": f"{w}-{ls}", "win_pct": win_pct, "games_back": gb}


def _recent_candidates(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = _rows(
        conn,
        """
        SELECT detector, subject, novelty_score
        FROM stat_candidates
        ORDER BY ingested_at DESC
        LIMIT 25
        """,
    )
    return [{"detector": d, "subject": s, "score": round(float(n), 2)} for d, s, n in rows]


def _already_posted(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = _rows(
        conn,
        "SELECT subject, angle, as_of FROM post_metrics ORDER BY as_of DESC LIMIT 25",
    )
    return [{"subject": s, "angle": a, "as_of": str(d)} for s, a, d in rows]


def build_context_pack(conn: duckdb.DuckDBPyConnection, as_of: date) -> dict:
    """Assemble the full context pack Claude reasons over to propose hypotheses.

    Args:
        conn: Read connection with hist attached.
        as_of: Reference date.

    Returns:
        A JSON-serializable dict. Every section degrades to empty on missing data.
    """
    discovered = []
    try:
        discovered = [m.id for m in discover_metrics(conn)]
    except Exception as exc:
        logger.debug("context: discover_metrics failed: %s", exc)

    return {
        "as_of": str(as_of),
        "standings": _standings(conn),
        "metric_catalog": _catalog(conn),
        "existing_metric_ids": discovered,
        "recent_candidates": _recent_candidates(conn),
        "already_posted": _already_posted(conn),
        "explored": explored_json(conn),
        "instructions": (
            "Propose 5-10 HypothesisSpec objects as a JSON array. Only reference "
            "tables/columns in metric_catalog. Do NOT re-propose anything in "
            "'explored' that came back 'below_gate' or 'no_data'. filter_sql and "
            "derived_expr must be pure numeric expressions over listed columns — no "
            "string literals, no subqueries. Favor angles the existing detectors and "
            "recent_candidates miss."
        ),
    }
