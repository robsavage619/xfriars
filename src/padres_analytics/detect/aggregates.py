"""Pitch-level aggregate metrics — plate discipline and swing decisions.

The scanner only ever read player-season summary tables, so the richest data in
the database (hundreds of thousands of pitch rows carrying swing decisions, bat
speed and run value) was unreachable. These metrics aggregate that grain into
rates the existing lenses can consume unchanged.

Two things are deliberately in code rather than config. The numerator and
denominator predicates are engine-authored SQL, never caller text. And the
stabilization points come from the sample-size literature (swing and chase rates
stabilize far faster than wOBA), so a rate over 60 pitches is shrunk toward the
mean instead of being reported as elite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from padres_analytics.detect.registry import MetricSpec
from padres_analytics.detect.splits import SplitSpec, render_predicate
from padres_analytics.detect.sql import fmt_name, resolve_table

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# A swing is any offer at the pitch. Statcast spells this out across several
# description values rather than flagging it directly.
_SWING = (
    "description IN ('foul', 'hit_into_play', 'swinging_strike', "
    "'foul_tip', 'swinging_strike_blocked', 'foul_bunt', 'missed_bunt', 'bunt_foul_tip')"
)
# A whiff is a swing that misses entirely — a foul tip is contact, so it is not
# a whiff even though it is caught for a strike.
_WHIFF = "description IN ('swinging_strike', 'swinging_strike_blocked', 'missed_bunt')"
_IN_ZONE = "zone BETWEEN 1 AND 9"
_OUT_ZONE = "zone > 9"


@dataclass(frozen=True)
class AggMetric:
    """A rate computed over event rows, with its own denominator.

    The numerator must *imply* the denominator: every row it counts has to be a
    row the denominator counts too. Otherwise the rate is comparing unrelated
    populations and can exceed 100% — a chase rate of 99.8% is not a hitter with
    no discipline, it is a broken fraction.

    Attributes:
        numerator: SQL boolean expression counted in the numerator. Must be a
            subset of the denominator's rows.
        denominator: SQL boolean expression defining the opportunity set. Getting
            this right is the whole game — a chase rate over *all* pitches rather
            than out-of-zone pitches is a different, meaningless stat.
        stabilization_n: Opportunities before the rate is treated as reliable.
    """

    id: str
    label: str
    table: str
    numerator: str
    denominator: str
    direction: Literal["higher", "lower"]
    stabilization_n: int
    gloss: str
    unit: str = "%"
    value_format: str = ".1f"
    scale: float = 100.0
    subject: Literal["batter", "pitcher"] = "batter"
    coverage: str = "since_2024"
    excluded_split_columns: frozenset[str] = frozenset()
    """Splits that are incoherent for this metric. A chase rate is *defined* on
    out-of-zone pitches, so slicing it by zone leaves an empty or meaningless
    denominator. Not every metric-by-split combination is a question, and
    generating all of them mechanically is how an engine produces confident
    nonsense."""

    def accepts(self, split: SplitSpec | None) -> bool:
        """True when this split is coherent for this metric."""
        return split is None or split.column not in self.excluded_split_columns

    def to_metric_spec(self, split: SplitSpec | None = None) -> MetricSpec:
        """A MetricSpec carrying this metric's lens configuration.

        The rows are fetched by :func:`fetch_agg_rows`; this only supplies
        labels, direction, format and stabilization to the shared lens code.
        """
        label = f"{self.label} {split.display()}" if split else self.label
        return MetricSpec(
            id=f"{self.id}__{split.key()}" if split else self.id,
            label=label,
            table=self.table,
            value_col="rate",
            metric_type="rate",
            direction=self.direction,
            value_format=self.value_format,
            unit=self.unit,
            stabilization_n=self.stabilization_n,
            population="agg",
            coverage=self.coverage,
            lenses=["extremeness"],
        )


# Stabilization points follow the standard sample-size guidance: swing decisions
# settle quickly, contact quality slowly. These are opportunity counts, not PAs.
BATTER_AGGS: tuple[AggMetric, ...] = (
    AggMetric(
        id="chase_rate",
        label="Chase Rate",
        table="statcast_batter_pitches",
        numerator=f"{_SWING} AND {_OUT_ZONE}",
        denominator=_OUT_ZONE,
        direction="lower",
        stabilization_n=250,
        gloss="How often he swings at pitches outside the strike zone. Lower is better.",
        # The denominator is already out-of-zone pitches.
        excluded_split_columns=frozenset({"zone_bucket"}),
    ),
    AggMetric(
        id="whiff_rate",
        label="Whiff Rate",
        table="statcast_batter_pitches",
        numerator=_WHIFF,
        denominator=_SWING,
        direction="lower",
        stabilization_n=200,
        gloss="How often he swings and misses entirely. Lower is better.",
    ),
    AggMetric(
        id="zone_contact",
        label="Zone Contact",
        table="statcast_batter_pitches",
        numerator=f"{_SWING} AND {_IN_ZONE} AND NOT ({_WHIFF})",
        denominator=f"{_SWING} AND {_IN_ZONE}",
        direction="higher",
        stabilization_n=200,
        gloss="How often he makes contact when he swings at strikes. Higher is better.",
        # The denominator is already in-zone swings.
        excluded_split_columns=frozenset({"zone_bucket"}),
    ),
    AggMetric(
        id="swing_rate",
        label="Swing Rate",
        table="statcast_batter_pitches",
        numerator=_SWING,
        denominator="TRUE",
        direction="higher",
        stabilization_n=300,
        gloss="How often he offers at anything — aggression, not quality.",
    ),
)


def bat_tracking_metrics() -> tuple[AggMetric, ...]:
    """Bat-tracking averages, which are means rather than rates.

    Kept separate because the numerator/denominator shape doesn't apply — the
    value is an average of a measured column over swings.
    """
    return ()


def fetch_agg_rows(
    conn: duckdb.DuckDBPyConnection,
    metric: AggMetric,
    year: int,
    split: SplitSpec | None = None,
    min_opportunities: int | None = None,
) -> tuple[list[tuple[int, str, float]], dict[int, int], str]:
    """Aggregate a rate per player over event rows, league-wide.

    Args:
        conn: DB connection.
        metric: The aggregate to compute.
        year: Season.
        split: Optional categorical condition, rendered by the engine.
        min_opportunities: Denominator floor; defaults to a fifth of the
            stabilization point, matching the lens's own reliability gate.

    Returns:
        ``(rows, sample_sizes, resolved_table)`` where rows are
        ``(player_id, name, value)`` best-first and ``sample_sizes`` maps player
        id to denominator count — the per-player n that drives shrinkage.
    """
    src = resolve_table(conn, metric.table)
    id_col = "batter_id" if metric.subject == "batter" else "pitcher_id"
    name_col = "batter_name" if metric.subject == "batter" else "pitcher_name"
    floor = min_opportunities or max(10, metric.stabilization_n // 5)
    order = "DESC" if metric.direction == "higher" else "ASC"

    where = [f"season = {int(year)}"]
    if split is not None:
        where.append(render_predicate(split))
    where_sql = " AND ".join(where)

    sql = f"""
        SELECT {id_col} AS pid,
               ANY_VALUE({name_col}) AS pname,
               SUM(CASE WHEN {metric.numerator} THEN 1 ELSE 0 END) * {metric.scale}
                   / NULLIF(SUM(CASE WHEN {metric.denominator} THEN 1 ELSE 0 END), 0) AS rate,
               SUM(CASE WHEN {metric.denominator} THEN 1 ELSE 0 END) AS opportunities
        FROM {src}
        WHERE {where_sql}
        GROUP BY {id_col}
        HAVING SUM(CASE WHEN {metric.denominator} THEN 1 ELSE 0 END) >= {int(floor)}
        ORDER BY rate {order}
    """
    try:
        rows = conn.execute(sql).fetchall()
    except Exception as exc:
        logger.warning("aggregates: %s fetch failed: %s", metric.id, exc)
        return [], {}, src

    out: list[tuple[int, str, float]] = []
    sizes: dict[int, int] = {}
    for pid, pname, rate, opportunities in rows:
        if rate is None:
            continue
        out.append((int(pid), fmt_name(str(pname)), float(rate)))
        sizes[int(pid)] = int(opportunities)
    return out, sizes, src


def rate_is_bounded(conn: duckdb.DuckDBPyConnection, metric: AggMetric, year: int) -> bool:
    """True when no player's rate exceeds 100% — i.e. the fraction is well-formed.

    A cheap invariant check on the numerator-implies-denominator rule. Any
    violation is a construction bug, not a finding, so this is worth asserting
    in tests rather than trusting by inspection.
    """
    rows, _sizes, _src = fetch_agg_rows(conn, metric, year)
    return all(value <= metric.scale + 1e-9 for _pid, _name, value in rows)


# At or above this share of the qualified population, the event tables are the
# league for practical purposes and the sample caveat stops being informative.
LEAGUE_COVERAGE_THRESHOLD = 0.90


def population_label(
    conn: duckdb.DuckDBPyConnection,
    measured: int,
    year: int,
    min_pa: int = 100,
) -> str:
    """Describe the comparison population as honestly as its coverage allows.

    Event ingest fills player by player, so for a long time the "league" in a
    split claim was really whoever had been ingested. Calling that group
    "qualified MLB hitters" describes a convenience sample as the league — a
    referee blocked exactly that. But once coverage is near-complete the caveat
    becomes noise that undersells a real league comparison.

    So the label is derived from measured coverage rather than fixed either way,
    and it corrects itself as ingest fills in.

    Args:
        conn: DB connection.
        measured: Players actually in the comparison.
        year: Season.
        min_pa: Plate-appearance floor defining "qualified".

    Returns:
        A population label safe to put on a card.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM statcast_batting_expected WHERE year = ? AND pa >= ?",
            [year, min_pa],
        ).fetchone()
    except Exception as exc:
        logger.debug("aggregates: qualified population unavailable: %s", exc)
        return f"{measured} hitters with pitch-level data"

    qualified = int(row[0]) if row and row[0] else 0
    if qualified <= 0:
        return f"{measured} hitters with pitch-level data"

    coverage = measured / qualified
    if coverage >= LEAGUE_COVERAGE_THRESHOLD:
        # Deliberately does *not* say "qualified (min N PA)". The measured group
        # is whoever cleared the metric's own pitch minimum, which is not the
        # same set as the PA-qualified population it is compared against — some
        # measured hitters sit below the PA bar. Attributing a qualification the
        # group doesn't necessarily hold is the same error as calling a partial
        # sample the league, just smaller.
        return f"{measured} MLB hitters (league-wide pitch-level coverage)"
    return (
        f"{measured} of {qualified} qualified MLB hitters with pitch-level data "
        f"— not the full league"
    )
