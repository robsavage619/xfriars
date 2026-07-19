"""Statistical lenses for the generic scanner.

Each lens takes population data and a focal (Padre) value and returns a
LensResult with a rarity score and a pre-verified framing string. All lenses
are distribution-free (empirical ECDF) by default.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LensResult:
    """Output of one lens applied to one player/metric pair."""

    rarity: float
    framing: str
    claim_scope: str
    lens: str


def _ecdf_percentile(values: list[float], focal: float, higher_is_better: bool) -> float:
    """Fraction of population values that focal outperforms (strict less-than)."""
    if not values:
        return 0.5
    if higher_is_better:
        return sum(1 for v in values if v < focal) / len(values)
    return sum(1 for v in values if v > focal) / len(values)


def extremeness_lens(
    *,
    focal_value: float,
    population_values: list[float],
    metric_label: str,
    player_name: str,
    higher_is_better: bool,
    value_format: str,
    unit: str,
    claim_scope: str,
    stabilization_n: int,
    focal_n: int | None = None,
) -> LensResult | None:
    """ECDF-tail rarity for a rate or differential metric.

    Applies empirical-Bayes shrinkage toward 0.5 while the sample is still
    accumulating below stabilization_n. Returns None when the result is below the
    top-20% threshold (rarity < 0.80) — those aren't interesting enough to emit.

    Args:
        focal_value: The Padre player's metric value.
        population_values: All qualified MLB values (including the Padre).
        metric_label: Display label (e.g. 'Barrel %').
        player_name: Humanized name for the framing string.
        higher_is_better: True if a higher value is better.
        value_format: Python format spec (e.g. '.1f', '+.3f').
        unit: Unit suffix appended to value (e.g. 'ft/s', '%', '').
        claim_scope: Scope tag for the framing (e.g. 'since_2015').
        stabilization_n: Sample size at which the metric is considered reliable.
        focal_n: The *focal player's own* observation count (pitches, PAs, batted
            balls). When supplied this drives the shrinkage, which is what
            empirical Bayes actually calls for — how much we trust this player's
            number depends on how much of his data we have, not on how many
            other players are in the league. Falls back to population size for
            season-grain metrics that carry no per-player count.

    Returns:
        LensResult, or None if the result is unreliable or not notable.
    """
    pop_n = len(population_values)
    if pop_n < 10:
        return None

    sample_n = focal_n if focal_n is not None else pop_n
    if sample_n < max(10, stabilization_n // 5):
        return None

    ecdf_pct = _ecdf_percentile(population_values, focal_value, higher_is_better)

    shrink = min(1.0, sample_n / stabilization_n)
    rarity = 0.5 + (ecdf_pct - 0.5) * shrink
    rarity = max(0.0, min(1.0, rarity))

    if rarity < 0.80:
        return None

    val_str = f"{focal_value:{value_format}}"
    if unit:
        val_str = f"{val_str} {unit}"

    pct_rank = round(ecdf_pct * 100)
    direction = "top" if higher_is_better else "bottom"
    framing = (
        f"{player_name} is in the {direction} {100 - pct_rank}% of MLB "
        f"in {metric_label} ({val_str})"
    )

    return LensResult(rarity=rarity, framing=framing, claim_scope=claim_scope, lens="extremeness")


def rank_lens(
    *,
    focal_rank: int,
    population_size: int,
    player_name: str,
    focal_value: float,
    metric_label: str,
    value_format: str,
    unit: str,
    claim_scope: str,
) -> LensResult | None:
    """Top-N rank rarity.

    Surfaces the result only when the Padre ranks in the top quartile, capped at
    rank 15 so this never fires for a mid-table result in a small population.

    Args:
        focal_rank: 1-based rank (1 = best).
        population_size: Number of qualified players.
        player_name: Humanized name for the framing string.
        focal_value: The Padre player's metric value.
        metric_label: Display label.
        value_format: Python format spec.
        unit: Unit suffix.
        claim_scope: Scope tag.

    Returns:
        LensResult, or None if rank is not notable.
    """
    cutoff = min(15, max(1, population_size // 4))
    if population_size == 0 or focal_rank > cutoff:
        return None

    rarity = max(0.0, 1.0 - (focal_rank - 1) / cutoff)

    val_str = f"{focal_value:{value_format}}"
    if unit:
        val_str = f"{val_str} {unit}"

    framing = f"{player_name} ranks #{focal_rank} in MLB in {metric_label} ({val_str})"

    return LensResult(rarity=rarity, framing=framing, claim_scope=claim_scope, lens="rank")


def pace_lens(
    *,
    current_value: float,
    games_played: int,
    season_games: int,
    player_name: str,
    metric_label: str,
    milestone: float,
    unit: str,
    claim_scope: str,
) -> LensResult | None:
    """'On pace for X' milestone countdown for counting stats.

    Returns None when fewer than 10 games have been played (too noisy), or when
    the player is not on pace to reach the milestone.

    Args:
        current_value: Season total so far.
        games_played: Games played by the player this season.
        season_games: Full season game count (typically 162).
        player_name: Humanized name.
        metric_label: Stat label (e.g. 'HR').
        milestone: Target number to reach (e.g. 40.0 HR).
        unit: Unit label (e.g. 'HR').
        claim_scope: Scope tag.

    Returns:
        LensResult, or None if pace is not notable.
    """
    if games_played < 10 or games_played >= season_games:
        return None

    pace = current_value * season_games / games_played

    if current_value >= milestone:
        framing = f"{player_name} has already reached {milestone:.0f} {unit} this season"
        rarity = 0.95
    elif pace >= milestone:
        remaining = milestone - current_value
        framing = (
            f"{player_name} is on pace for {pace:.0f} {unit} "
            f"({remaining:.0f} away from {milestone:.0f})"
        )
        rarity = min(0.90, 0.5 + (pace - milestone) / max(milestone, 1.0))
    else:
        return None

    return LensResult(rarity=rarity, framing=framing, claim_scope=claim_scope, lens="pace")


def milestone_proximity_lens(
    *,
    focal_value: float,
    milestone: float,
    metric_label: str,
    player_name: str,
    value_format: str,
    unit: str,
    claim_scope: str,
    proximity_pct: float = 0.10,
) -> LensResult | None:
    """'Within N of milestone' lens for counting and rate stats.

    Fires when the player is within ``proximity_pct`` (default 10%) below the
    threshold and not yet past it — close enough to be notable but not already
    there (pace_lens handles the 'on pace' angle for counting stats).

    Args:
        focal_value: The Padre player's current value.
        milestone: The notable threshold (e.g. 20.0 for barrel rate).
        metric_label: Display label.
        player_name: Humanized name.
        value_format: Python format spec.
        unit: Unit suffix.
        claim_scope: Scope tag.
        proximity_pct: Fraction below milestone that counts as "close".

    Returns:
        LensResult, or None if not within proximity or already past milestone.
    """
    if milestone <= 0:
        return None
    distance_pct = (milestone - focal_value) / milestone
    if not (0.0 < distance_pct <= proximity_pct):
        return None

    remaining = milestone - focal_value
    val_str = f"{focal_value:{value_format}}"
    if unit:
        val_str = f"{val_str} {unit}"
    milestone_str = f"{milestone:{value_format}}"
    if unit:
        milestone_str = f"{milestone_str} {unit}"

    remaining_str = f"{remaining:{value_format}}"
    framing = (
        f"{player_name} is {remaining_str}{' ' + unit if unit else ''} away from "
        f"{milestone_str} in {metric_label} ({val_str} now)"
    )
    # Rarity scales linearly from 0.80 (at 10% away) to 0.95 (almost there)
    rarity = 0.80 + 0.15 * (1.0 - distance_pct / proximity_pct)

    return LensResult(
        rarity=rarity, framing=framing, claim_scope=claim_scope, lens="milestone_proximity"
    )


def percentile_elite_lens(
    *,
    percentile: float,
    metric_label: str,
    player_name: str,
    claim_scope: str,
    threshold: float = 85.0,
) -> LensResult | None:
    """Fire when a player sits in an elite percentile of a pre-oriented metric.

    Savant percentile tables are already direction-oriented (higher = better),
    so this needs no direction config — it reads the percentile straight and
    frames it in native Statcast language. Powers schema-discovered metrics.

    Args:
        percentile: The 0-100 Savant percentile (higher always better).
        metric_label: Humanized metric label.
        player_name: Humanized player name.
        claim_scope: Scope tag.
        threshold: Minimum percentile to fire (default 85th).

    Returns:
        LensResult, or None below threshold.
    """
    from padres_analytics.detect.sql import ordinal

    if percentile < threshold:
        return None
    pct_from_top = round(100 - percentile)
    tier = "elite" if percentile >= 95 else "well above average"
    tail = "the best in MLB" if pct_from_top <= 0 else f"top {pct_from_top}% in MLB"
    framing = (
        f"{player_name} ranks in the {ordinal(percentile)} percentile in "
        f"{metric_label} — {tail} ({tier})"
    )
    return LensResult(
        rarity=min(percentile / 100.0, 0.99),
        framing=framing,
        claim_scope=claim_scope,
        lens="percentile_elite",
    )


def bh_surviving_indices(rarities: list[float], alpha: float = 0.05) -> set[int]:
    """Return original indices that survive Benjamini-Hochberg FDR correction.

    Treats (1 - rarity) as a p-value proxy. Signals with rarity near 1.0 almost
    always survive; borderline signals near the threshold are penalized by the
    multiplicity of the full daily test battery.

    Args:
        rarities: List of rarity scores in [0, 1], one per candidate.
        alpha: FDR control level (typically 0.05).

    Returns:
        Set of indices (into ``rarities``) that survive.
    """
    m = len(rarities)
    if m == 0:
        return set()

    indexed = sorted(range(m), key=lambda i: 1.0 - rarities[i])
    surviving: set[int] = set()
    for rank, orig_idx in enumerate(indexed, start=1):
        p_proxy = 1.0 - rarities[orig_idx]
        if p_proxy <= (rank / m) * alpha:
            surviving.add(orig_idx)
    return surviving
