"""Schema-driven metric discovery — the engine finds metrics, nobody types them.

Introspects the real Statcast tables and emits a ``MetricSpec`` per scannable
column, so adding a column upstream auto-creates a metric with zero config:

- **Percentile tables** (``*_percentile_ranks``) are pre-oriented by Savant
  (higher percentile = better, always), so every numeric column becomes a
  ``percentile_elite`` metric with no direction guessing.
- **Expected tables** auto-pair actual↔expected columns into luck/regression
  "gap" metrics (``est_woba - woba``, ``era - xera``) by naming convention.

This replaces the hand-curated TOML registry as the *source* of metrics; the
registry survives only as optional label/threshold overrides.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from padres_analytics.detect.registry import MetricSpec
from padres_analytics.detect.sql import resolve_table

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Columns that are identifiers/metadata, never metrics.
_SKIP_COLS: frozenset[str] = frozenset(
    {
        "player_id",
        "year",
        "season",
        "team_id",
        "ingested_at",
        "pa",
        "bip",
        "attempts",
        "competitive_runs",
        "brl",
        "barrels",
    }
)

_NUMERIC_TYPES: frozenset[str] = frozenset(
    {"DOUBLE", "INTEGER", "BIGINT", "FLOAT", "DECIMAL", "HUGEINT", "SMALLINT", "REAL"}
)

# Human labels for known columns; unknown columns fall back to title-cased names.
_LABELS: dict[str, str] = {
    "xwoba": "xwOBA",
    "xba": "xBA",
    "xslg": "xSLG",
    "xiso": "xISO",
    "xobp": "xOBP",
    "brl_percent": "Barrel %",
    "exit_velocity": "Exit Velocity",
    "max_ev": "Max Exit Velo",
    "hard_hit_percent": "Hard-Hit %",
    "k_percent": "Strikeout %",
    "bb_percent": "Walk %",
    "whiff_percent": "Whiff %",
    "chase_percent": "Chase %",
    "arm_strength": "Arm Strength",
    "sprint_speed": "Sprint Speed",
    "oaa": "Outs Above Average",
    "bat_speed": "Bat Speed",
    "squared_up_rate": "Squared-Up Rate",
    "swing_length": "Swing Length",
    "xera": "xERA",
    "fb_velocity": "Fastball Velocity",
    "fb_spin": "Fastball Spin",
    "curve_spin": "Curveball Spin",
}

_PERCENTILE_TABLES = ("statcast_batter_percentile_ranks", "statcast_pitcher_percentile_ranks")
# (table, [(actual, expected, gap_label, direction_expr)])
_EXPECTED_GAPS = (
    ("statcast_batting_expected", "woba", "est_woba", "xwOBA-wOBA Gap", "est_woba - woba"),
    ("statcast_pitching_expected", "era", "xera", "ERA-xERA Gap", "era - xera"),
)


def _label(col: str) -> str:
    return _LABELS.get(col, col.replace("_", " ").title())


def _numeric_cols(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    """Return scannable numeric column names for a table, or []."""
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return []
    return [r[1] for r in info if r[2] in _NUMERIC_TYPES and r[1] not in _SKIP_COLS]


def discover_metrics(conn: duckdb.DuckDBPyConnection) -> list[MetricSpec]:
    """Discover every scannable metric from the live schema.

    Args:
        conn: Read-mode connection with hist attached.

    Returns:
        Auto-generated MetricSpec list (percentile metrics + expected-gap metrics).
    """
    specs: list[MetricSpec] = []

    # 1. Percentile tables → one pre-oriented metric per numeric column.
    for table in _PERCENTILE_TABLES:
        src = resolve_table(conn, table)
        ctx = "P" if "pitcher" in table else "B"
        for col in _numeric_cols(conn, src.replace("hist.", "")):
            specs.append(
                MetricSpec(
                    id=f"pctl_{ctx}_{col}",
                    label=_label(col),
                    table=table,
                    value_col=col,
                    metric_type="rate",
                    direction="higher",  # percentiles are pre-oriented
                    value_format=".0f",
                    coverage="since_2015",
                    population=f"qualified_{ctx}",
                    lenses=["percentile_elite", "rank"],
                )
            )

    # 2. Expected tables → auto-paired luck/regression gap metrics.
    for table, actual, expected, label, expr in _EXPECTED_GAPS:
        src = resolve_table(conn, table)
        cols = _numeric_cols(conn, src.replace("hist.", ""))
        if actual in cols and expected in cols:
            specs.append(
                MetricSpec(
                    id=f"gap_{actual}",
                    label=label,
                    table=table,
                    value_col=actual,
                    derived_expr=expr,
                    metric_type="differential",
                    direction="higher",  # positive gap = unlucky, regression coming
                    value_format="+.3f",
                    coverage="since_2015",
                    population=f"gap_{actual}",
                    lenses=["extremeness", "rank"],
                    filter_sql="pa >= 50",
                )
            )

    logger.info("discovery: %d metrics auto-discovered from schema", len(specs))
    return specs
