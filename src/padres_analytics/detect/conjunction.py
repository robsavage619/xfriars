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
    scope = _coverage_phrase(metric.coverage)
    return f"{player_name} has the best {metric.label} of any Padre {scope} ({val_str})"


def _coverage_phrase(coverage: str) -> str:
    """Render a coverage tag into clean prose (e.g. 'since_2015' -> 'since 2015').

    Args:
        coverage: A metric coverage tag.

    Returns:
        Human-readable scope phrase, never starting with a dangling article.
    """
    mapping = {
        "since_2015": "in the Statcast era (since 2015)",
        "statcast_era": "in the Statcast era",
        "franchise_1969": "in franchise history",
        "mlb_all": "",
    }
    if coverage in mapping:
        return mapping[coverage]
    if coverage.startswith("since_"):
        return f"since {coverage.removeprefix('since_')}"
    return coverage.replace("_", " ")


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


# A conjunction is only interesting when its members measure *different* things.
# Exit velocity, max EV and hard-hit% are three names for one skill: chaining them
# manufactures a guaranteed-unique claim out of a single underlying trait, and the
# geometric-mean rarity treats correlated marks as if they were independent.
# One member per family, so "elite at X and Y" means two genuinely separate things.
_METRIC_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("expected_outcome", ("xba", "xslg", "xwoba", "est_woba", "xiso", "gap_woba", "xera")),
    ("contact_quality", ("exit_velocity", "max_ev", "hard_hit", "barrel", "brl", "sweet_spot")),
    ("swing", ("bat_speed", "swing_length", "squared_up", "blast")),
    ("discipline", ("chase", "whiff", "k_percent", "bb_percent", "zone_contact")),
    ("speed", ("sprint_speed", "baserunning", "steal")),
    ("defense", ("oaa", "arm_strength", "arm_value", "range", "framing", "pop_time")),
    ("power_output", ("home_run", "_hr", "slg", "iso")),
    ("velocity", ("fastball_velo", "release_speed", "spin")),
)

# More than this and a card stops being a story and becomes a stat dump.
MAX_CONJUNCTION_MEMBERS = 3


def metric_family(metric_id: str) -> str:
    """Coarse family for a metric id — correlated metrics share one.

    Args:
        metric_id: A registry or discovered metric id (e.g. ``pctl_B_max_ev``).

    Returns:
        The family name, or the metric id itself when it matches none (an
        unmatched metric is treated as its own family — never silently merged).
    """
    lowered = metric_id.lower()
    for family, needles in _METRIC_FAMILIES:
        if any(n in lowered for n in needles):
            return family
    return metric_id


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
        # One hit per *family* (best rarity), not per metric — see _METRIC_FAMILIES.
        best_per_family: dict[str, Any] = {}
        for h in player_hits:
            fam = metric_family(h.metric.id)
            if (
                fam not in best_per_family
                or h.lens_result.rarity > best_per_family[fam].lens_result.rarity
            ):
                best_per_family[fam] = h

        if len(best_per_family) < 2:
            continue

        # Strongest few, so the claim stays legible and the independence
        # assumption behind the geometric mean stays defensible.
        selected = sorted(
            best_per_family.values(),
            key=lambda h: h.lens_result.rarity,
            reverse=True,
        )[:MAX_CONJUNCTION_MEMBERS]

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
                metric_ids=[h.metric.id for h in selected],
                combined_framing=combined_framing,
                combined_rarity=combined,
            )
        )

    groups.sort(key=lambda g: g.combined_rarity, reverse=True)
    return groups


def count_players_meeting_all(
    conn: duckdb.DuckDBPyConnection,
    hits: list,
) -> int | None:
    """Count MLB players who simultaneously meet every member hit's mark.

    This is what earns a "one of N players" claim. Without it, a conjunction is
    just two facts printed next to each other; with it, the compound itself is
    the finding. Each member contributes a threshold at the focal player's own
    value (``>=`` for higher-is-better metrics, ``<=`` otherwise), so the count
    answers: how many players are at least this good at all of these at once?

    Args:
        conn: DB connection.
        hits: Member _Hit objects (one per distinct metric).

    Returns:
        Player count, or None when the members span tables/years that can't be
        joined or any query fails — callers must fall back to plain conjunction
        framing rather than asserting uniqueness.
    """
    if len(hits) < 2:
        return None

    years = {h.metric_year for h in hits}
    if len(years) != 1:
        logger.debug("conjunction.count: members span years %s; no uniqueness claim", years)
        return None
    year = years.pop()

    selects: list[str] = []
    params: list[Any] = []
    for i, hit in enumerate(hits):
        metric = hit.metric
        src = resolve_table(conn, metric.table)
        value_expr = metric.derived_expr or metric.value_col
        cmp_op = ">=" if metric.direction == "higher" else "<="
        where = f"AND ({metric.filter_sql})" if metric.filter_sql else ""
        selects.append(
            f"""
            SELECT {metric.id_col} AS pid
            FROM {src}
            WHERE {metric.year_col} = ?
              AND {value_expr} IS NOT NULL
              AND {value_expr} {cmp_op} ?
              {where}
            """
        )
        params.extend([year, hit.focal_value])
        del i

    joined = " INTERSECT ".join(f"({s})" for s in selects)
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM ({joined})", params).fetchone()
    except Exception as exc:
        logger.debug("conjunction.count: query failed: %s", exc)
        return None

    if not row or row[0] is None:
        return None
    return int(row[0])
