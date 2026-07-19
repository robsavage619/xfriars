"""Split contrast — rank a player's *gap* against the league's distribution of gaps.

Most findings worth reading are contrasts rather than levels: not "he chases
32%" but "he chases far more against breaking balls than anyone else does."

The statistical move that makes this honest is the comparison set. A player's
platoon gap means nothing against zero — every hitter has some gap, and
day-to-day variance guarantees a nonzero number. It means something against the
*league distribution of that same gap*, which is what this module builds. That
keeps ECDF extremeness and empirical-Bayes shrinkage applicable unchanged, with
the smaller side's sample driving the shrinkage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from padres_analytics.detect.aggregates import AggMetric, fetch_agg_rows
from padres_analytics.detect.lenses import LensResult, _ecdf_percentile
from padres_analytics.detect.splits import SplitSpec

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Both sides must clear this before a player enters the comparison. A "platoon
# split" over 30 pitches against lefties is noise wearing a narrative.
MIN_SIDE_OPPORTUNITIES = 60

# A differential carries roughly twice the variance of either side, so the gap
# distribution needs more players behind it than a single-metric leaderboard.
MIN_CONTRAST_POPULATION = 40


@dataclass(frozen=True)
class ContrastRow:
    """One player's two-sided split and the gap between them."""

    player_id: int
    player_name: str
    a_value: float
    b_value: float
    a_n: int
    b_n: int

    @property
    def diff(self) -> float:
        """Side A minus side B."""
        return self.a_value - self.b_value

    @property
    def weaker_n(self) -> int:
        """The smaller of the two samples — what the gap's reliability rests on."""
        return min(self.a_n, self.b_n)


def fetch_contrast_rows(
    conn: duckdb.DuckDBPyConnection,
    metric: AggMetric,
    split_a: SplitSpec,
    split_b: SplitSpec,
    year: int,
    min_side: int = MIN_SIDE_OPPORTUNITIES,
) -> list[ContrastRow]:
    """Fetch both sides of a split and inner-join on players who qualify for both.

    A player appearing on only one side is dropped rather than treated as a zero
    gap: he has no measured gap at all, and admitting him would drag the league
    distribution toward the middle and make real gaps look more extreme.

    Args:
        conn: DB connection.
        metric: The rate to compute on each side.
        split_a: First condition (the "against" side of the story).
        split_b: Second condition (the baseline).
        year: Season.
        min_side: Per-side opportunity floor.

    Returns:
        One row per player qualifying on both sides.
    """
    rows_a, sizes_a, _ = fetch_agg_rows(conn, metric, year, split_a, min_opportunities=min_side)
    rows_b, sizes_b, _ = fetch_agg_rows(conn, metric, year, split_b, min_opportunities=min_side)

    by_a = {pid: (name, val) for pid, name, val in rows_a}
    by_b = {pid: val for pid, _, val in rows_b}

    out: list[ContrastRow] = []
    for pid, (name, a_val) in by_a.items():
        if pid not in by_b:
            continue
        out.append(
            ContrastRow(
                player_id=pid,
                player_name=name,
                a_value=a_val,
                b_value=by_b[pid],
                a_n=sizes_a.get(pid, 0),
                b_n=sizes_b.get(pid, 0),
            )
        )
    logger.debug(
        "contrast: %s %s vs %s -> %d qualifying player(s)",
        metric.id,
        split_a.key(),
        split_b.key(),
        len(out),
    )
    return out


def split_contrast_lens(
    *,
    focal: ContrastRow,
    population: list[ContrastRow],
    metric: AggMetric,
    split_a: SplitSpec,
    split_b: SplitSpec,
    claim_scope: str,
) -> LensResult | None:
    """Rank a player's split gap against the league distribution of that gap.

    Args:
        focal: The Padre's contrast row.
        population: Every qualifying player's contrast row, including the focal.
        metric: The underlying rate.
        split_a: First condition.
        split_b: Baseline condition.
        claim_scope: Scope tag — must already carry the split qualifiers.

    Returns:
        LensResult, or None when the population is too thin, the focal sample too
        small, or the gap not extreme enough to be worth a card.
    """
    if len(population) < MIN_CONTRAST_POPULATION:
        logger.debug(
            "contrast: %s population %d < %d; no claim",
            metric.id,
            len(population),
            MIN_CONTRAST_POPULATION,
        )
        return None

    if focal.weaker_n < MIN_SIDE_OPPORTUNITIES:
        return None

    # Rank the *size* of the gap, not its signed value. A split like chase-vs-zone
    # is negative for every hitter, so a signed rank would call the widest gap in
    # baseball "narrower than 96%" — true of the signed number, backwards as
    # English. Magnitude is what "gap" means; direction is carried by printing
    # both rates, which also lets a reversed platoon split surface normally.
    magnitudes = [abs(row.diff) for row in population]
    extremity = _ecdf_percentile(magnitudes, abs(focal.diff), higher_is_better=True)

    shrink = min(1.0, focal.weaker_n / metric.stabilization_n)
    rarity = max(0.0, min(1.0, 0.5 + (extremity - 0.5) * shrink))
    if rarity < 0.85:
        return None

    # Report the *shrunk* percentile, not the raw ECDF rank. Stating a stricter
    # rank than the engine will defend is how a caption outruns its own evidence.
    pct = round(rarity * 100)
    gap = abs(focal.diff)

    # Both sides go in the sentence. A bare differential hides which term drives
    # it — a wide swing-rate gap can come from elite restraint or from sheer
    # aggression on strikes, and those are opposite stories. Showing both rates
    # makes the claim descriptive instead of implying a verdict it can't support.
    # Split labels already read "vs breaking balls", so the sentence must not
    # add another "vs" around them.
    framing = (
        f"{focal.player_name} — {metric.label.lower()}: "
        f"{focal.a_value:.1f}% {split_a.display()}, {focal.b_value:.1f}% {split_b.display()} "
        f"— a {gap:.1f}-point gap, wider than {pct}% of the {len(population)} hitters "
        f"with pitch-level data (min {MIN_SIDE_OPPORTUNITIES} pitches each side)"
    )

    return LensResult(
        rarity=rarity,
        framing=framing,
        claim_scope=claim_scope,
        lens="split_contrast",
    )
