"""Detector → spatial-card mapping: pick the visual that best illustrates a stat.

A detected candidate (a text stat about a player) can carry a companion spatial
card — e.g. a "weakness" candidate pairs with a hot/cold zone, a "cold_streak"
with a rolling-xwOBA line. This module maps the detector to that card and pulls
the player id + season out of the candidate's facts so the card can be built.

The card only materializes if the underlying event data is ingested — otherwise
:func:`spatial_companion` returns ``None`` and the text stat stands alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from padres_analytics.detect.spatial import emit_spatial_candidate

if TYPE_CHECKING:
    from datetime import date

    import duckdb

    from padres_analytics.detect.candidates import StatCandidate

# Detector name → the spatial card that best illustrates it (editorial heuristic).
_DETECTOR_CARD: dict[str, str] = {
    "weakness": "hotcold",  # where a hitter does/doesn't do damage
    "cold_streak": "rolling",  # the slump, traced over time
    "hit_streak": "rolling",  # the hot run
    "barrel_rate": "launch",  # contact quality (LA x EV)
    "power_cluster": "launch",
    "statcast_profile": "swingtake",  # the slider already exists; add approach
    "xstats_unlucky": "rolling",  # over/under-performance over the season
    "pitcher_career_chase": "arsenal",  # a pitcher's mix
}

_PLAYER_KEYS = ("padre_player_id", "player_id", "mlb_id", "subject_id", "batter")
_SEASON_KEYS = ("season", "year", "statcast_year")


def suggest_card(detector: str) -> str | None:
    """Return the spatial card a detector maps to, or ``None`` if none fits."""
    return _DETECTOR_CARD.get(detector)


def _scalar_lookup(facts: dict, keys: tuple[str, ...]) -> int | None:
    """Find the first int-coercible value under ``keys`` in facts or facts['facts']."""
    for scope in (facts, facts.get("facts") or {}):
        if not isinstance(scope, dict):
            continue
        for key in keys:
            val = scope.get(key)
            if val is None:
                continue
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
    return None


def extract_player_id(facts: dict) -> int | None:
    """Pull the MLBAM player id out of a candidate's facts (top-level or nested)."""
    return _scalar_lookup(facts, _PLAYER_KEYS)


def extract_season(facts: dict, as_of: date) -> int:
    """Pull the season from facts, falling back to the candidate's as_of year."""
    season = _scalar_lookup(facts, _SEASON_KEYS)
    return season if season is not None else as_of.year


def spatial_companion(
    conn: duckdb.DuckDBPyConnection,
    detector: str,
    facts: dict,
    as_of: date,
) -> StatCandidate | None:
    """Emit the companion spatial candidate for a detected stat, if one applies.

    Args:
        conn: Read connection to padres.db.
        detector: The detector name that produced the text candidate.
        facts: The text candidate's facts_json (carries player id + season).
        as_of: The text candidate's as_of date.

    Returns:
        A spatial ``StatCandidate`` (payload_kind="spatial"), or ``None`` when the
        detector doesn't map, the player id is missing, or the event data needed
        to build the card hasn't been ingested.
    """
    card = suggest_card(detector)
    if card is None:
        return None
    player_id = extract_player_id(facts)
    if player_id is None:
        return None
    season = extract_season(facts, as_of)
    return emit_spatial_candidate(conn, card, player_id, season, as_of=as_of)
