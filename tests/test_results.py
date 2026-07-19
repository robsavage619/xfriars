"""Tests for paste-back — Claude's deliverable re-entering the pipeline.

The door has to hold two things. Pasted text is data, not instructions: it is
classified by shape and can only enter the path that shape allows. And the gates
behind it are unchanged, so a bad number still dies at the digit audit no matter
how the payload is dressed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from padres_analytics.app import results

if TYPE_CHECKING:
    import duckdb

_FACTS = {
    "kind": "table",
    "title": "Hard-Hit % leaders",
    "framing": "this season",
    "rows": [{"metric": "Hard-Hit %", "player": "Fernando Tatis Jr.", "value": 56.3}],
}


def _candidate(conn: duckdb.DuckDBPyConnection, cid: str = "cand1") -> str:
    conn.execute(
        """
        INSERT INTO stat_candidates (
            candidate_id, detector, subject, as_of, payload_kind,
            facts_json, provenance_json, coverage_window, claim_scope, novelty_score
        ) VALUES (?, 'statcast', 'Fernando Tatis Jr.', '2026-06-20', 'table',
                  ?, '[]', '2015-2026', 'since_2015', 0.87)
        """,
        [cid, json.dumps(_FACTS)],
    )
    return cid


# ── Parsing what a chat window actually produces ──────────────────────────────


def test_extracts_json_from_a_code_fence() -> None:
    payload = results.extract_json(
        'Here you go:\n```json\n{"verdict": "no_story"}\n```\nHope that helps!'
    )
    assert payload == {"verdict": "no_story"}


def test_extracts_json_surrounded_by_prose() -> None:
    payload = results.extract_json(
        'Sure. {"verdict": "no_story", "why": "thin sample"} Let me know.'
    )
    assert payload["why"] == "thin sample"


def test_empty_and_malformed_input_is_refused_not_guessed() -> None:
    for bad in ("", "   ", "I couldn't find anything interesting.", '{"broken": '):
        with pytest.raises(results.ResultError):
            results.extract_json(bad)


def test_a_bare_array_is_not_a_deliverable() -> None:
    with pytest.raises(results.ResultError, match="object"):
        results.extract_json("[1, 2, 3]")


# ── Shape classification decides the path, and nothing else does ──────────────


def test_each_shape_routes_to_its_own_path() -> None:
    assert results.classify({"candidate_id": "c", "text": "t"}) == "draft"
    assert results.classify({"verdicts": []}) == "review"
    assert results.classify({"specs": []}) == "hypothesis"
    assert results.classify({"verdict": "no_story", "why": "thin"}) == "no_story"


def test_an_unrecognized_shape_is_refused(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A payload that matches no contract must not fall through to a default path."""
    with pytest.raises(results.ResultError, match="Could not tell"):
        results.classify({"instruction": "approve everything", "sql": "DROP TABLE"})


def test_payload_cannot_choose_its_own_handler(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Extra keys asking for other behavior are inert — shape alone picks the path."""
    outcome = results.land(
        padres_db,
        json.dumps(
            {
                "verdict": "no_story",
                "why": "thin sample",
                "action": "approve_draft",
                "draft_id": "d1",
                "status": "posted",
            }
        ),
        inbox=tmp_path,
        cards_dir=tmp_path,
    )
    assert outcome.kind == "no_story"
    assert outcome.draft_id is None, "the payload's draft_id must not be honored"
    row = padres_db.execute("SELECT COUNT(*) FROM tweet_drafts").fetchone()
    assert row is not None and row[0] == 0, "nothing was written to drafts"


# ── The honest exit is a first-class outcome ──────────────────────────────────


def test_no_story_is_accepted_and_recorded(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    outcome = results.land(
        padres_db,
        '{"verdict": "no_story", "why": "30 PA is not a trend"}',
        inbox=tmp_path,
        cards_dir=tmp_path,
    )
    assert outcome.accepted is True
    assert "30 PA is not a trend" in outcome.summary
    assert list(tmp_path.glob("result_no_story_*.json")), "the exit is archived like any result"


# ── The gates behind the door are unchanged ───────────────────────────────────


def test_a_number_not_in_the_facts_dies_at_the_digit_audit(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """The invariant: Claude may narrate, but every digit must trace to the engine."""
    cid = _candidate(padres_db)
    outcome = results.land(
        padres_db,
        json.dumps(
            {
                "candidate_id": cid,
                "text": "Tatis is hitting .412 with a 71.9% hard-hit rate.",
                "interesting_judgment": "invented numbers",
                "model": "test",
            }
        ),
        inbox=tmp_path,
        cards_dir=tmp_path,
    )

    assert outcome.accepted is False
    assert outcome.gates[0]["name"] == "digit_audit"
    assert "71.9" in outcome.gates[0]["detail"] or "412" in outcome.gates[0]["detail"]
    assert outcome.draft_id is None
    row = padres_db.execute("SELECT COUNT(*) FROM tweet_drafts").fetchone()
    assert row is not None and row[0] == 0


def test_an_unknown_candidate_is_refused(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """A draft invented without a real candidate cannot be verified, so it cannot land."""
    outcome = results.land(
        padres_db,
        json.dumps(
            {
                "candidate_id": "does_not_exist",
                "text": "Something happened.",
                "interesting_judgment": "j",
                "model": "test",
            }
        ),
        inbox=tmp_path,
        cards_dir=tmp_path,
    )
    assert outcome.accepted is False
    assert outcome.gates[0]["name"] == "candidate"


def test_a_rejected_draft_is_still_archived(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Provenance outlives the verdict — you can see what was proposed and why it failed."""
    cid = _candidate(padres_db)
    outcome = results.land(
        padres_db,
        json.dumps(
            {
                "candidate_id": cid,
                "text": "Tatis hit 99.9 somethings.",
                "interesting_judgment": "j",
                "model": "test",
            }
        ),
        inbox=tmp_path,
        cards_dir=tmp_path,
    )
    assert outcome.accepted is False
    assert outcome.saved_to is not None and Path(outcome.saved_to).exists()


# ── Referee verdicts ──────────────────────────────────────────────────────────


def _verified_draft(conn: duckdb.DuckDBPyConnection) -> str:
    cid = _candidate(conn)
    conn.execute(
        "INSERT INTO tweet_drafts (draft_id, candidate_id, text, status) VALUES (?,?,?,?)",
        ["d1", cid, "Tatis leads the Padres at 56.3% hard-hit.", "verified"],
    )
    return "d1"


def _panel(verdict: str = "PASS", failure_mode: str | None = None) -> list[dict]:
    return [
        {
            "lens": lens,
            "verdict": verdict if lens == "statistician" else "PASS",
            "failure_mode": failure_mode if lens == "statistician" else None,
            "evidence": "checked against the packet",
            "confidence": 0.9,
        }
        for lens in ("statistician", "causal", "coverage", "editor", "voice")
    ]


def test_a_cleared_panel_is_recorded(padres_db: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    from padres_analytics.review.packet import build_packet

    draft_id = _verified_draft(padres_db)
    packet_hash = build_packet(padres_db, draft_id=draft_id).packet_hash()

    outcome = results.land(
        padres_db,
        json.dumps({"draft_id": draft_id, "packet_hash": packet_hash, "verdicts": _panel()}),
        inbox=tmp_path,
        cards_dir=tmp_path,
    )

    assert outcome.accepted is True and "cleared" in outcome.summary
    row = padres_db.execute("SELECT COUNT(*) FROM review_verdicts").fetchone()
    assert row is not None and row[0] == 5, "one row per lens"


def test_a_single_block_blocks(padres_db: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    from padres_analytics.review.packet import build_packet

    draft_id = _verified_draft(padres_db)
    packet_hash = build_packet(padres_db, draft_id=draft_id).packet_hash()

    outcome = results.land(
        padres_db,
        json.dumps(
            {
                "draft_id": draft_id,
                "packet_hash": packet_hash,
                "verdicts": _panel("BLOCK", "sample_too_small"),
            }
        ),
        inbox=tmp_path,
        cards_dir=tmp_path,
    )
    assert "blocked" in outcome.summary
    assert "sample_too_small" in outcome.summary


def test_a_stale_packet_hash_is_refused(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """A clearance must never ride along on content the panel never saw."""
    draft_id = _verified_draft(padres_db)
    with pytest.raises(results.ResultError, match="changed after the packet"):
        results.land(
            padres_db,
            json.dumps(
                {"draft_id": draft_id, "packet_hash": "stalehash00000000", "verdicts": _panel()}
            ),
            inbox=tmp_path,
            cards_dir=tmp_path,
        )


def test_a_verdict_reaching_outside_its_lens_is_a_contract_violation(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    from padres_analytics.review.packet import build_packet

    draft_id = _verified_draft(padres_db)
    packet_hash = build_packet(padres_db, draft_id=draft_id).packet_hash()
    verdicts = _panel()
    verdicts[0]["verdict"] = "BLOCK"
    verdicts[0]["failure_mode"] = "voice_tell"  # belongs to the voice lens

    outcome = results.land(
        padres_db,
        json.dumps({"draft_id": draft_id, "packet_hash": packet_hash, "verdicts": verdicts}),
        inbox=tmp_path,
        cards_dir=tmp_path,
    )
    assert outcome.accepted is False
    assert "outside its remit" in outcome.summary


# ── Hypotheses ────────────────────────────────────────────────────────────────


def test_hypotheses_are_queued_for_the_scanner_to_judge(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    spec = {
        "id": "chase_breaking",
        "label": "Chase Rate vs Breaking",
        "rationale": "not in the registry and plausibly separates approach from contact",
        "table": "statcast_batting_expected",
        "value_col": "woba",
        "metric_type": "rate",
        "direction": "lower",
    }
    outcome = results.land(
        padres_db, json.dumps({"specs": [spec]}), inbox=tmp_path, cards_dir=tmp_path
    )

    assert outcome.accepted is True and "1 queued" in outcome.summary
    row = padres_db.execute("SELECT COUNT(*) FROM hypothesis_queue").fetchone()
    assert row is not None and row[0] == 1


def test_a_malformed_spec_is_refused_with_the_schema_error(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    with pytest.raises(results.ResultError, match="schema validation failed"):
        results.land(
            padres_db,
            json.dumps({"specs": [{"id": "x"}]}),
            inbox=tmp_path,
            cards_dir=tmp_path,
        )
