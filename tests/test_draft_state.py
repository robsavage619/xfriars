"""State machine and duplicate guard tests for tweet_drafts."""

from __future__ import annotations

import duckdb
import pytest

from padres_analytics.tweets.draft import StateTransitionError, transition


def _insert_draft(
    conn: duckdb.DuckDBPyConnection,
    draft_id: str,
    candidate_id: str,
    status: str,
    text: str = "Padres are 3-2 on Jun 9 since 1990.",
) -> None:
    """Insert a bare tweet_drafts row for testing."""
    # Insert a minimal stat_candidates row first (FK constraint)
    conn.execute(
        """
        INSERT OR IGNORE INTO stat_candidates (
            candidate_id, detector, as_of, payload_kind,
            facts_json, provenance_json,
            coverage_window, claim_scope, novelty_score
        ) VALUES (?, 'on_this_day', '2024-06-09', 'table', '{}', '[]',
                  '1990-2024', 'since_1990', 0.5)
        """,
        [candidate_id],
    )
    conn.execute(
        """
        INSERT INTO tweet_drafts (draft_id, candidate_id, text, status)
        VALUES (?, ?, ?, ?)
        """,
        [draft_id, candidate_id, text, status],
    )


def test_pending_to_verified(padres_db: duckdb.DuckDBPyConnection) -> None:
    _insert_draft(padres_db, "d001", "c001", "pending")
    transition(padres_db, "d001", "verified")
    row = padres_db.execute("SELECT status FROM tweet_drafts WHERE draft_id='d001'").fetchone()
    assert row is not None and row[0] == "verified"


def test_verified_to_approved(padres_db: duckdb.DuckDBPyConnection) -> None:
    _insert_draft(padres_db, "d002", "c002", "verified")
    transition(padres_db, "d002", "approved")
    row = padres_db.execute("SELECT status FROM tweet_drafts WHERE draft_id='d002'").fetchone()
    assert row is not None and row[0] == "approved"


def test_approved_to_posted(padres_db: duckdb.DuckDBPyConnection) -> None:
    _insert_draft(padres_db, "d003", "c003", "approved")
    transition(padres_db, "d003", "posted")
    row = padres_db.execute("SELECT status FROM tweet_drafts WHERE draft_id='d003'").fetchone()
    assert row is not None and row[0] == "posted"


def test_posted_is_terminal(padres_db: duckdb.DuckDBPyConnection) -> None:
    _insert_draft(padres_db, "d004", "c004", "posted")
    with pytest.raises(StateTransitionError, match="terminal"):
        transition(padres_db, "d004", "rejected")


def test_reject_from_pending(padres_db: duckdb.DuckDBPyConnection) -> None:
    _insert_draft(padres_db, "d005", "c005", "pending")
    transition(padres_db, "d005", "rejected")
    row = padres_db.execute("SELECT status FROM tweet_drafts WHERE draft_id='d005'").fetchone()
    assert row is not None and row[0] == "rejected"


def test_illegal_transition(padres_db: duckdb.DuckDBPyConnection) -> None:
    """pending → posted is not a valid transition."""
    _insert_draft(padres_db, "d006", "c006", "pending")
    with pytest.raises(StateTransitionError):
        transition(padres_db, "d006", "posted")


def test_not_found_raises(padres_db: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(StateTransitionError, match="not found"):
        transition(padres_db, "ghost", "verified")
