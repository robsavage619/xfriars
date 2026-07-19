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

# Season-grain tables a hypothesis may scan (no window).
_SEASON_TABLES = (
    "statcast_batter_exitvelo_barrels",
    "statcast_batting_expected",
    "statcast_sprint_speed",
    "statcast_batter_percentile_ranks",
)
# Game-grain tables — one row per event, usable with a rolling `window`.
_GAME_TABLES = (
    "statcast_batted_balls",
    "statcast_batter_pitches",
    "statcast_pitches",
)
_SCANNABLE_TABLES = _SEASON_TABLES + _GAME_TABLES
# Identifiers and structural counters are numeric but meaningless as metrics —
# "average batter_id" is not a stat. The pitch tables key on batter_id/pitcher_id
# rather than player_id, so both must be excluded or they become proposable.
_SKIP_COLS = frozenset(
    {
        "player_id",
        "batter_id",
        "pitcher_id",
        "year",
        "season",
        "ingested_at",
        "team_id",
        "game_pk",
        "at_bat_number",
        "pitch_number",
    }
)


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
        catalog.append(
            {
                "table": table,
                "grain": "game" if table in _GAME_TABLES else "season",
                "supports_window": table in _GAME_TABLES,
                "max_year": max_year(conn, table),
                "numeric_columns": cols,
            }
        )
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
        """
        SELECT subject, angle_key, MAX(captured_at) AS last_seen
        FROM post_metrics
        GROUP BY subject, angle_key
        ORDER BY last_seen DESC
        LIMIT 25
        """,
    )
    return [{"subject": s, "angle": a, "as_of": str(d)} for s, a, d in rows]


def _split_vocabulary() -> dict:
    """The categorical splits the engine can render, and the metrics they cross.

    Handed to Claude so proposals can name a split by column and value instead of
    trying to write one — the literal is rendered engine-side from the allowlist,
    so this widens what can be *asked* without widening what can be *executed*.
    """
    try:
        from padres_analytics.detect.aggregates import BATTER_AGGS
        from padres_analytics.detect.splits import CONTRAST_PAIRS, ENUM_COLUMNS
    except Exception as exc:
        logger.debug("context: split vocabulary unavailable: %s", exc)
        return {}

    return {
        "legal_splits": {col: sorted(vals) for col, vals in ENUM_COLUMNS.items()},
        "contrast_pairs": {name: [a.key(), b.key()] for name, (a, b) in CONTRAST_PAIRS.items()},
        "aggregate_metrics": [
            {
                "id": m.id,
                "label": m.label,
                "table": m.table,
                "direction": m.direction,
                "gloss": m.gloss,
                "stabilization_n": m.stabilization_n,
                "incompatible_splits": sorted(m.excluded_split_columns),
            }
            for m in BATTER_AGGS
        ],
        "note": (
            "Splits are rendered by the engine from this allowlist. Reference them "
            "by column and value; never write a string literal yourself."
        ),
    }


def _referee_history(conn: duckdb.DuckDBPyConnection) -> dict:
    """What the referee panel keeps rejecting, and why.

    The compounding half of the loop: proposal quality improves when the proposer
    can see which shapes of claim get refuted, not just which ones found data.
    """
    try:
        from padres_analytics.review.store import block_rate_by_lens, failure_mode_counts

        return {
            "common_failure_modes": failure_mode_counts(conn)[:10],
            "block_rate_by_lens": block_rate_by_lens(conn),
            "note": (
                "These are reasons cards were blocked *after* clearing the numeric "
                "gates. A proposal shaped like a repeat offender will likely be "
                "blocked too — propose around them."
            ),
        }
    except Exception as exc:
        logger.debug("context: referee history unavailable: %s", exc)
        return {}


def _learned_preferences(conn: duckdb.DuckDBPyConnection) -> dict:
    """Which claim shapes have historically survived editorial review."""
    try:
        rows = conn.execute(
            """
            SELECT feature, multiplier, n_pos, n_total
            FROM learned_priors
            WHERE run_id = (SELECT run_id FROM learning_runs ORDER BY created_at DESC LIMIT 1)
              AND multiplier != 1.0
            ORDER BY ABS(multiplier - 1.0) DESC
            LIMIT 15
            """
        ).fetchall()
    except Exception as exc:
        logger.debug("context: learned priors unavailable: %s", exc)
        return {}
    if not rows:
        return {"note": "No feature has enough editorial history to prefer yet."}
    return {
        "features": [
            {"feature": r[0], "multiplier": round(float(r[1]), 3), "kept": r[2], "seen": r[3]}
            for r in rows
        ],
        "note": "Above 1.0 survives review more often than average; below 1.0 less.",
    }


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
        "split_vocabulary": _split_vocabulary(),
        "referee_history": _referee_history(conn),
        "editorial_preferences": _learned_preferences(conn),
        "instructions": (
            "Propose 5-10 HypothesisSpec objects as a JSON array. Only reference "
            "tables/columns in metric_catalog. Do NOT re-propose anything in "
            "'explored' that came back 'below_gate' or 'no_data'. filter_sql and "
            "derived_expr must be pure numeric expressions over listed columns — no "
            "string literals, no subqueries. Set a rolling 'window' (e.g. "
            '{"days": 15}) ONLY on a table with supports_window=true; on those, the '
            "value_col is averaged per player over the last N days. Favor angles the "
            "existing detectors and recent_candidates miss.\n\n"
            "Read 'split_vocabulary' before proposing: the engine can now slice by "
            "handedness, pitch class and zone, and can rank a player's *gap* between "
            "two splits against the league's distribution of that same gap. That "
            "contrast shape is where the interesting questions live — a level ('he "
            "chases a lot') is weaker than a contrast ('he chases far more against "
            "breaking balls than anyone else'). Respect 'incompatible_splits'.\n\n"
            "Read 'referee_history': those are reasons claims were rejected after "
            "passing every numeric gate. Proposing the same shape again wastes a slot."
        ),
    }
