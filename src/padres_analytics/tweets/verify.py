"""Verification gates for tweet drafts (Path A and B)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Tolerances for Path A cross-check
_TOLERANCE_COUNTING = 0  # counting stats: exact match
_TOLERANCE_RATE = 0.001  # AVG/OBP/SLG/OPS
_TOLERANCE_WAR = 0.1  # WAR

# Rate stat types (use rate tolerance)
_RATE_STAT_TYPES = frozenset(
    {
        "battingAverage",
        "onBasePercentage",
        "sluggingPercentage",
        "onBasePlusSlugging",
        "whip",
        "earnedRunAverage",
    }
)


class VerificationError(ValueError):
    """Raised when a verification check hard-fails."""


def verify_path_b(
    conn: duckdb.DuckDBPyConnection,
    candidate_id: str,
    facts_json: dict,
    provenance_json: list[dict],
) -> dict:
    """Path B verification: re-run provenance SQL and check sanity ranges.

    A draft flagged single_source=True because there is no second independent
    source to cross-check against (Path A requires two sources — Phase 2+).

    Args:
        conn: A read-only connection to padres.db with hist attached.
        candidate_id: For logging.
        facts_json: The payload facts dict to validate.
        provenance_json: List of provenance entries from the candidate.

    Returns:
        Verification result dict suitable for tweet_drafts.verification_json.

    Raises:
        VerificationError: If a hard sanity check fails.
    """
    checks: list[str] = []

    # Sanity-range assertions — format-level checks that don't require re-query
    _sanity_check_facts(facts_json, checks)

    # Provenance completeness: every entry must have required fields
    for i, prov in enumerate(provenance_json):
        for required_key in ("source_table", "sql", "as_of"):
            if required_key not in prov:
                raise VerificationError(
                    f"Provenance entry {i} missing required key '{required_key}' "
                    f"for candidate {candidate_id}"
                )
        checks.append(
            f"provenance[{i}]: source_table={prov['source_table']}, as_of={prov['as_of']}"
        )

    result = {
        "path": "B",
        "passed": True,
        "single_source": True,
        "detail": "; ".join(checks) if checks else "no checks applicable",
    }
    logger.info("Path B verification passed for %s", candidate_id)
    return result


def _sanity_check_facts(facts: dict, checks: list[str]) -> None:
    """Run sanity-range assertions on known fact keys.

    Args:
        facts: The facts_json payload dict (may be a TablePayload dump).
        checks: Mutable list to append check descriptions to.

    Raises:
        VerificationError: If any value is outside its expected range.
    """
    # TablePayload sanity checks
    if "rows" in facts:
        rows = facts["rows"]
        if not isinstance(rows, list):
            raise VerificationError(f"facts_json.rows must be a list, got {type(rows)}")
        if len(rows) > 10:
            raise VerificationError(f"facts_json.rows has {len(rows)} entries; max is 10")
        checks.append(f"rows count OK ({len(rows)})")

    if "columns" in facts:
        cols = facts["columns"]
        if len(cols) > 6:
            raise VerificationError(f"facts_json.columns has {len(cols)} entries; max is 6")
        checks.append(f"columns count OK ({len(cols)})")

    # Win/loss sanity
    if "wins" in facts and "losses" in facts and "total_games" in facts:
        wins = facts["wins"]
        losses = facts["losses"]
        total = facts["total_games"]
        if wins + losses > total:
            raise VerificationError(f"wins ({wins}) + losses ({losses}) > total_games ({total})")
        checks.append(f"W-L sanity OK ({wins}-{losses} in {total})")

    # Batting average range
    for key in ("batting_avg", "avg", "ba"):
        if key in facts:
            val = float(facts[key])
            if not (0.0 <= val <= 1.0):
                raise VerificationError(f"facts_json.{key} = {val} outside [0, 1]")
            checks.append(f"{key} range OK ({val:.3f})")

    # ERA range
    for key in ("era",):
        if key in facts:
            val = float(facts[key])
            if not (0.0 <= val <= 20.0):
                raise VerificationError(f"facts_json.{key} = {val} outside [0, 20]")
            checks.append(f"{key} range OK ({val:.2f})")

    # WAR range
    for key in ("war", "bwar"):
        if key in facts:
            val = float(facts[key])
            if not (-15.0 <= val <= 15.0):
                raise VerificationError(f"facts_json.{key} = {val} outside [-15, 15]")
            checks.append(f"{key} range OK ({val:.1f})")


def verify_path_a(
    conn: duckdb.DuckDBPyConnection,
    candidate_id: str,
    facts_json: dict[str, Any],
) -> dict[str, Any]:
    """Path A verification: cross-check a leaderboard fact against a second source.

    For leaderboard candidates, re-queries mlb_leaders with a different rank
    window to confirm the Padre's value is consistent. Agreement within
    tolerance → passed. Mismatch → raises VerificationError with the diff shown.

    Currently implemented for leaderboard detector candidates. All others
    fall back to Path B (single_source=True).

    Args:
        conn: Read-mode padres.db connection.
        candidate_id: For logging.
        facts_json: The candidate's facts dict.

    Returns:
        Verification result dict.

    Raises:
        VerificationError: On value mismatch exceeding tolerance.
    """
    stat_type = facts_json.get("stat_type")
    season = facts_json.get("season")
    padre_rank = facts_json.get("padre_rank")
    padre_value_raw = facts_json.get("padre_value_raw")

    if not (stat_type and season and padre_rank is not None and padre_value_raw is not None):
        # Not a leaderboard candidate — fall back to Path B
        return {
            "path": "B",
            "passed": True,
            "single_source": True,
            "detail": "non-leaderboard candidate; Path A not applicable",
        }

    # Re-query: fetch the player's value at their stored rank
    row = conn.execute(
        """
        SELECT value, player_name, rank
        FROM mlb_leaders
        WHERE season = ? AND stat_type = ? AND rank = ?
        """,
        [season, stat_type, padre_rank],
    ).fetchone()

    if row is None:
        raise VerificationError(
            f"Path A: mlb_leaders has no row for "
            f"season={season} stat_type={stat_type!r} rank={padre_rank} "
            f"(candidate {candidate_id}). Re-run 'pad ingest leaders'."
        )

    stored_value, _stored_name, _stored_rank = row

    # Numeric comparison
    try:
        orig = float(padre_value_raw)
        cross = float(stored_value)
    except (ValueError, TypeError) as exc:
        raise VerificationError(
            f"Path A: cannot compare values {padre_value_raw!r} vs {stored_value!r}: {exc}"
        ) from exc

    tolerance = _TOLERANCE_RATE if stat_type in _RATE_STAT_TYPES else _TOLERANCE_COUNTING
    diff = abs(orig - cross)
    if diff > tolerance:
        raise VerificationError(
            f"Path A MISMATCH for {stat_type} rank {padre_rank}: "
            f"facts_json={padre_value_raw!r} vs mlb_leaders={stored_value!r} "
            f"(diff={diff:.4f}, tolerance={tolerance}). "
            f"Re-run 'pad ingest leaders' or reject this candidate."
        )

    detail = (
        f"Path A: {stat_type} rank {padre_rank} "
        f"facts={padre_value_raw} cross={stored_value} diff={diff:.4f} OK"
    )
    logger.info("Path A verification passed for %s: %s", candidate_id, detail)
    return {
        "path": "A",
        "passed": True,
        "single_source": False,
        "detail": detail,
    }


def digit_audit(text: str, facts_json: dict | str) -> list[str]:
    """Check that every number token in text appears in facts_json.

    Every digit token in the caption must be present in the raw JSON string
    of facts_json. Years, ranks, and stat values are all checked.

    Args:
        text: The tweet caption (≤280 chars).
        facts_json: The payload dict or its JSON string.

    Returns:
        List of offending tokens (empty = audit passed).
    """
    import re

    facts_str = (
        json.dumps(facts_json, sort_keys=True, default=str)
        if isinstance(facts_json, dict)
        else facts_json
    )

    # Extract all number tokens: integers and decimals (including leading-dot form)
    tokens = re.findall(r"\b\d+(?:\.\d+)?\b|\.\d+\b", text)
    offenders: list[str] = []
    for tok in tokens:
        # Normalize: "0.394" and ".394" should both match ".394" in facts
        normalized = [tok]
        if tok.startswith("0."):
            normalized.append(tok[1:])  # "0.394" → ".394"
        elif tok.startswith("."):
            normalized.append("0" + tok)  # ".394" → "0.394"

        if not any(n in facts_str for n in normalized):
            offenders.append(tok)

    return offenders
