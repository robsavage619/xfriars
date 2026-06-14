"""Conjunction layer: multi-scope framing + named-anchor resolution + conjunction grouping.

Three capabilities:

1. Franchise scope evaluator — queries Statcast history for all SDP seasons to select
   the strongest provable framing tier:
     "franchise record (Statcast era)" > "first Padre since [Name, Year]" > "Statcast era best"
   The selected tier replaces the generic lens framing string. Claude sees only the
   engine-chosen tier and may never upgrade it.

2. Named-anchor resolver — embedded inside the scope evaluator. Finds the most recent
   prior SDP player who held the same feat, so framing says "since [Name] ([Year])."

3. Conjunction grouper — groups _Hit objects by player_id. Players with 2+ distinct
   metrics firing get a ConjunctionGroup whose combined_rarity is the geometric mean of
   the individual rarities. The caption-writer can use the conjunction framing to write
   "only the Nth player this season with X AND Y."
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from padres_analytics.detect.registry import MetricSpec
from padres_analytics.detect.sql import fmt_name, resolve_table

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_SD_TEAM_BREF = "SDP"

ScopeTier = Literal["franchise_record", "first_since", "statcast_era_best", "season_best"]


@dataclass
class ScopeResult:
    """Outcome of the franchise scope evaluator for one player/metric pair."""

    tier: ScopeTier
    framing: str
    prior_holder: str | None = None
    prior_year: int | None = None


def evaluate_franchise_scope(
    conn: duckdb.DuckDBPyConnection,
    metric: MetricSpec,
    player_id: int,
    player_name: str,
    focal_value: float,
    year: int,
    base_framing: str,
) -> ScopeResult:
    """Select the strongest provable franchise-scope framing for a Padre's metric value.

    Queries the Statcast table joined to bwar_player_seasons to find all prior SDP
    seasons. Tiers (strongest to weakest):
      1. franchise_record — focal_value beats every prior SDP season in the table
      2. first_since      — some prior SDP player had a higher value, but not recently
      3. statcast_era_best — focal is the best this season among Padres (fallback)
      4. season_best      — weakest; returned when all other queries fail gracefully

    Args:
        conn: DB connection with hist attached.
        metric: Metric specification (provides table, value_expr, filter_sql).
        player_id: MLBAM ID of the focal (Padre) player.
        player_name: Humanized name.
        focal_value: The focal player's metric value.
        year: Current metric year.
        base_framing: Fallback framing from the lens (used as season_best text).

    Returns:
        ScopeResult with the strongest provable tier and its framing string.
    """
    src = resolve_table(conn, metric.table)
    value_expr = metric.derived_expr if metric.derived_expr else metric.value_col
    where = f"AND ({metric.filter_sql})" if metric.filter_sql else ""
    direction = "DESC" if metric.direction == "higher" else "ASC"

    try:
        return _query_franchise_scope(
            conn=conn,
            src=src,
            value_expr=value_expr,
            where=where,
            direction=direction,
            metric=metric,
            player_id=player_id,
            player_name=player_name,
            focal_value=focal_value,
            year=year,
            base_framing=base_framing,
        )
    except Exception as exc:
        logger.debug(
            "conjunction.evaluate_franchise_scope: query failed metric=%s player=%s: %s",
            metric.id,
            player_name,
            exc,
        )
        return ScopeResult(tier="season_best", framing=base_framing)


def _query_franchise_scope(
    *,
    conn: duckdb.DuckDBPyConnection,
    src: str,
    value_expr: str,
    where: str,
    direction: str,
    metric: MetricSpec,
    player_id: int,
    player_name: str,
    focal_value: float,
    year: int,
    base_framing: str,
) -> ScopeResult:
    """Execute the franchise-scope queries. Raises on DB errors (caller catches)."""
    # Step 1: find the best prior SDP season for this metric (excluding current player + year)
    prior_rows = conn.execute(
        f"""
        SELECT s.{metric.name_col}, s.{metric.year_col}, {value_expr} AS val
        FROM {src} s
        JOIN hist.bwar_player_seasons b
          ON s.{metric.id_col} = b.mlb_id
         AND s.{metric.year_col} = b.year_id
        WHERE b.team_id = ?
          AND s.{metric.id_col} != ?
          AND s.{metric.year_col} < ?
          AND {value_expr} IS NOT NULL
          {where}
        ORDER BY val {direction}
        LIMIT 1
        """,
        [_SD_TEAM_BREF, player_id, year],
    ).fetchall()

    if not prior_rows:
        # No prior SDP data found in table — claim Statcast-era best
        framing = _franchise_record_framing(player_name, focal_value, metric)
        return ScopeResult(tier="franchise_record", framing=framing)

    prior_val = float(prior_rows[0][2])
    prior_name = fmt_name(str(prior_rows[0][0]))
    prior_year = int(prior_rows[0][1])

    is_record = focal_value > prior_val if metric.direction == "higher" else focal_value < prior_val

    if is_record:
        framing = _franchise_record_framing(player_name, focal_value, metric)
        return ScopeResult(
            tier="franchise_record",
            framing=framing,
            prior_holder=prior_name,
            prior_year=prior_year,
        )

    # Step 2: current player is NOT the all-time franchise best.
    # Find the most recent prior SDP player who matched or exceeded this value.
    anchor_rows = conn.execute(
        f"""
        SELECT s.{metric.name_col}, s.{metric.year_col}
        FROM {src} s
        JOIN hist.bwar_player_seasons b
          ON s.{metric.id_col} = b.mlb_id
         AND s.{metric.year_col} = b.year_id
        WHERE b.team_id = ?
          AND s.{metric.id_col} != ?
          AND s.{metric.year_col} < ?
          AND {value_expr} {">="}  ?
          AND {value_expr} IS NOT NULL
          {where}
        ORDER BY s.{metric.year_col} DESC
        LIMIT 1
        """,
        [_SD_TEAM_BREF, player_id, year, focal_value],
    ).fetchall()

    if anchor_rows:
        anchor_name = fmt_name(str(anchor_rows[0][0]))
        anchor_year = int(anchor_rows[0][1])
        framing = _first_since_framing(player_name, focal_value, metric, anchor_name, anchor_year)
        return ScopeResult(
            tier="first_since",
            framing=framing,
            prior_holder=anchor_name,
            prior_year=anchor_year,
        )

    # No prior SDP player matched this value — best in Statcast era for this team
    framing = _franchise_record_framing(player_name, focal_value, metric)
    return ScopeResult(tier="franchise_record", framing=framing)


def _franchise_record_framing(player_name: str, value: float, metric: MetricSpec) -> str:
    val_str = f"{value:{metric.value_format}}"
    if metric.unit:
        val_str = f"{val_str} {metric.unit}"
    return (
        f"{player_name} is the best Padre in the {metric.coverage.replace('_', ' ')} "
        f"in {metric.label} ({val_str})"
    )


def _first_since_framing(
    player_name: str,
    value: float,
    metric: MetricSpec,
    prior_holder: str,
    prior_year: int,
) -> str:
    val_str = f"{value:{metric.value_format}}"
    if metric.unit:
        val_str = f"{val_str} {metric.unit}"
    return (
        f"{player_name} is the first Padre since {prior_holder} ({prior_year}) "
        f"to achieve {val_str} in {metric.label}"
    )


# ── Conjunction grouper ───────────────────────────────────────────────────────


@dataclass
class ConjunctionGroup:
    """A player with 2+ distinct metrics firing — a multi-metric story."""

    player_id: int
    player_name: str
    hits: list  # list[_Hit]; avoid circular import, typed as list
    metric_ids: list[str]
    combined_framing: str
    combined_rarity: float


def find_conjunctions(hits: list) -> list[ConjunctionGroup]:
    """Group _Hit objects by player; return groups with 2+ distinct metrics.

    Combined rarity is the geometric mean of individual lens rarities — a
    pessimistic estimate that ensures conjunctions don't inflate weak singles.

    Args:
        hits: List of _Hit objects from a scan run.

    Returns:
        List of ConjunctionGroup objects, sorted by combined_rarity descending.
    """
    from collections import defaultdict

    by_player: dict[int, list] = defaultdict(list)
    for hit in hits:
        by_player[hit.player_id].append(hit)

    groups: list[ConjunctionGroup] = []
    for pid, player_hits in by_player.items():
        distinct_metrics = list({h.metric.id: h for h in player_hits}.keys())
        if len(distinct_metrics) < 2:
            continue

        # One hit per metric (best rarity)
        best_per_metric: dict[str, Any] = {}
        for h in player_hits:
            mid = h.metric.id
            if (
                mid not in best_per_metric
                or h.lens_result.rarity > best_per_metric[mid].lens_result.rarity
            ):
                best_per_metric[mid] = h

        selected = list(best_per_metric.values())
        rarities = [h.lens_result.rarity for h in selected]
        combined = math.prod(rarities) ** (1.0 / len(rarities))

        player_name = player_hits[0].player_name
        feat_parts = [h.lens_result.framing for h in selected[:3]]
        combined_framing = f"{player_name}: " + " | ".join(feat_parts)

        groups.append(
            ConjunctionGroup(
                player_id=pid,
                player_name=player_name,
                hits=selected,
                metric_ids=list(best_per_metric.keys()),
                combined_framing=combined_framing,
                combined_rarity=combined,
            )
        )

    groups.sort(key=lambda g: g.combined_rarity, reverse=True)
    return groups
