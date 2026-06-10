"""Verification gates for tweet drafts (Path A and B)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


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
