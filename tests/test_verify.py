"""Tests for the digit audit and Path B verification gate."""

from __future__ import annotations

import duckdb
import pytest

from padres_analytics.tweets.verify import (
    VerificationError,
    digit_audit,
    verify_path_b,
)

# ── digit_audit ───────────────────────────────────────────────────────────────


def test_digit_audit_passes_when_all_numbers_in_facts() -> None:
    facts = {"wins": 8, "losses": 3, "total_games": 11}
    offenders = digit_audit("Padres are 8-3 in 11 games on this date.", facts)
    assert offenders == []


def test_digit_audit_catches_invented_number() -> None:
    facts = {"wins": 8, "losses": 3}
    # "42" is not in facts
    offenders = digit_audit("Padres have 42 wins.", facts)
    assert "42" in offenders


def test_digit_audit_leading_zero_normalization() -> None:
    """'.394' and '0.394' should match each other in facts."""
    facts = {"avg": ".394", "year": 1994}
    offenders = digit_audit("Gwynn hit .394 in 1994.", facts)
    assert offenders == []


def test_digit_audit_year_must_be_in_facts() -> None:
    """Years in the caption must appear in facts_json."""
    facts = {"wins": 8, "losses": 3, "year": 1998}
    offenders = digit_audit("The 1998 Padres won 8 games on this date.", facts)
    assert offenders == []


def test_digit_audit_rejects_year_not_in_facts() -> None:
    facts = {"wins": 8}
    offenders = digit_audit("The 2005 Padres won 8 games.", facts)
    assert "2005" in offenders


# ── verify_path_b ─────────────────────────────────────────────────────────────


def test_path_b_passes_valid_facts(padres_db: duckdb.DuckDBPyConnection) -> None:
    facts = {
        "kind": "table",
        "title": "Test",
        "as_of": "2024-06-09",
        "columns": ["Year", "Opp", "Score"],
        "rows": [["2023", "MIL", "5-3"]],
        "wins": 1,
        "losses": 0,
        "total_games": 1,
        "source": "test",
        "headline": "test",
        "claim_scope": "since_1990",
    }
    prov = [{"source_table": "hist.game_logs", "sql": "SELECT 1", "as_of": "2024-06-09"}]
    result = verify_path_b(padres_db, "cid_test", facts, prov)
    assert result["passed"] is True
    assert result["path"] == "B"
    assert result["single_source"] is True


def test_path_b_fails_on_wins_plus_losses_exceeds_total(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    facts = {"wins": 5, "losses": 5, "total_games": 8}
    prov = [{"source_table": "hist.game_logs", "sql": "SELECT 1", "as_of": "2024-06-09"}]
    with pytest.raises(VerificationError, match="wins"):
        verify_path_b(padres_db, "cid_bad", facts, prov)


def test_path_b_fails_on_missing_provenance_field(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    facts = {"wins": 1, "losses": 0, "total_games": 1}
    prov = [{"source_table": "hist.game_logs", "sql": "SELECT 1"}]  # missing as_of
    with pytest.raises(VerificationError, match="as_of"):
        verify_path_b(padres_db, "cid_prov", facts, prov)


def test_path_b_fails_on_too_many_rows(padres_db: duckdb.DuckDBPyConnection) -> None:
    facts = {"rows": [["x"] * 3] * 11}  # 11 rows — exceeds max of 10
    prov = [{"source_table": "hist.game_logs", "sql": "SELECT 1", "as_of": "2024-06-09"}]
    with pytest.raises(VerificationError, match="10"):
        verify_path_b(padres_db, "cid_rows", facts, prov)
