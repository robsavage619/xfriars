"""Tests for the desk's read endpoints — the context a human decides on."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from padres_analytics.app import api
from padres_analytics.storage.schemas import initialize

_FACTS = {"kind": "table", "framing": "this season", "rows": [{"value": 56.3}]}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient over a temp db seeded with one candidate and one draft."""
    db_path = tmp_path / "padres.db"
    conn = duckdb.connect(str(db_path))
    initialize(conn)
    conn.execute(
        """
        INSERT INTO stat_candidates (
            candidate_id, detector, subject, as_of, payload_kind,
            facts_json, provenance_json, coverage_window, claim_scope, novelty_score
        ) VALUES ('c1', 'statcast', 'Fernando Tatis Jr.', '2026-06-20', 'table',
                  ?, '[]', '2015-2026', 'since_2015', 0.9)
        """,
        [json.dumps(_FACTS)],
    )
    conn.execute(
        "INSERT INTO tweet_drafts (draft_id, candidate_id, text, status) VALUES (?,?,?,?)",
        ["d1", "c1", "Tatis leads at 56.3.", "verified"],
    )
    conn.close()
    monkeypatch.setattr(api, "DUCKDB_PATH", db_path)
    return TestClient(api.app)


def test_stats_counts_every_lane(client: TestClient) -> None:
    """Queued cards used to vanish with nowhere to see them; every lane gets a number."""
    body = client.get("/api/stats").json()
    assert body["new_candidates"] == 1
    assert body["queue_size"] == 1
    for lane in ("open_leads", "board_new", "board_queued", "posted_count"):
        assert lane in body


def test_coverage_reports_freshness_per_domain(client: TestClient) -> None:
    """Stale data has produced silently empty runs; the desk shows it before that."""
    body = client.get("/api/coverage").json()
    assert isinstance(body, list) and body
    entry = body[0]
    assert {"domain", "status", "latest_date", "blocks", "reason"} <= set(entry)


def test_predictions_returns_the_scorecard_and_recent_calls(client: TestClient) -> None:
    body = client.get("/api/predictions").json()
    assert "scorecard" in body and isinstance(body["recent"], list)


def test_posted_history_is_empty_but_shaped_before_anything_ships(client: TestClient) -> None:
    assert client.get("/api/posted").json() == []


def test_a_draft_with_no_review_reports_no_referee(client: TestClient) -> None:
    """Never-reviewed is a distinct state from cleared, and must not look like one."""
    draft = client.get("/api/drafts").json()[0]
    assert draft["referee"] is None


def test_referee_verdicts_surface_on_the_draft(client: TestClient) -> None:
    from padres_analytics.review.gate import adjudicate
    from padres_analytics.review.models import ReviewVerdict
    from padres_analytics.review.packet import build_packet
    from padres_analytics.review.store import record

    conn = duckdb.connect(str(api.DUCKDB_PATH))
    try:
        packet = build_packet(conn, draft_id="d1")
        verdicts = [
            ReviewVerdict(lens=lens, verdict="PASS", evidence="ok", confidence=0.9)
            for lens in ("statistician", "causal", "coverage", "editor", "voice")
        ]
        record(conn, "draft", "d1", adjudicate(packet, verdicts))
    finally:
        conn.close()

    referee = client.get("/api/drafts").json()[0]["referee"]
    assert referee is not None
    assert referee["outcome"] == "cleared"
    assert referee["stale"] is False
    assert len(referee["lenses"]) == 5


def test_editing_the_caption_marks_the_clearance_stale(client: TestClient) -> None:
    """A clearance must never ride along on content the panel never saw."""
    from padres_analytics.review.gate import adjudicate
    from padres_analytics.review.models import ReviewVerdict
    from padres_analytics.review.packet import build_packet
    from padres_analytics.review.store import record

    conn = duckdb.connect(str(api.DUCKDB_PATH))
    try:
        packet = build_packet(conn, draft_id="d1")
        verdicts = [
            ReviewVerdict(lens=lens, verdict="PASS", evidence="ok", confidence=0.9)
            for lens in ("statistician", "causal", "coverage", "editor", "voice")
        ]
        record(conn, "draft", "d1", adjudicate(packet, verdicts))
    finally:
        conn.close()

    assert client.get("/api/drafts").json()[0]["referee"]["stale"] is False

    edited = client.patch("/api/drafts/d1", json={"text": "Tatis is at 56.3 and climbing."})
    assert edited.status_code == 200

    referee = client.get("/api/drafts").json()[0]["referee"]
    assert referee["stale"] is True, "the panel judged different words than the draft now holds"


def test_a_caption_edit_with_an_invented_number_is_refused(client: TestClient) -> None:
    """The digit audit guards the edit box exactly as it guards ingest."""
    resp = client.patch("/api/drafts/d1", json={"text": "Tatis is hitting .412 now."})
    assert resp.status_code == 422

    unchanged = client.get("/api/drafts").json()[0]
    assert unchanged["text"] == "Tatis leads at 56.3.", "a refused edit must not partially apply"
