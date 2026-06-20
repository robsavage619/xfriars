"""Tests for the board API — what the app reads and the buttons it pushes."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from padres_analytics.app import api
from padres_analytics.board import add_card, add_leads
from padres_analytics.detect.angles import Stat, StoryAngle
from padres_analytics.detect.leads import Lead
from padres_analytics.storage.schemas import initialize


def _angle() -> StoryAngle:
    return StoryAngle(
        key="player_luck",
        subject="Manny Machado",
        title="BETTER THAN THE LINE",
        headline="43 points of wOBA separate his results from his contact.",
        thesis="t",
        direction="up",
        effect=1,
        reliability=0.5,
        interest=10,
        confidence="moderate",
        as_of=date(2026, 6, 20),
        rank_note="OPS 216 pts below his .825 career",
        stats=[Stat("p_owed", 43, "pts", "owed", 296)],
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient backed by a seeded temp padres.db (1 card + 2 leads)."""
    db_path = tmp_path / "padres.db"
    img = tmp_path / "card.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    conn = duckdb.connect(str(db_path))
    initialize(conn)
    add_card(conn, _angle(), str(img), kind="season_story", reconciled=True)
    add_leads(
        conn,
        [
            Lead("Machado", "down_year", "Machado: .609 OPS, 216 below career", "dig in", 64),
            Lead("Tatis", "luck", "Tatis: -37 pts vs expected", "dig in", 21),
        ],
    )
    conn.close()  # release the single writer before the API opens it
    monkeypatch.setattr(api, "DUCKDB_PATH", db_path)
    return TestClient(api.app)


def test_board_lists_cards_and_leads(client: TestClient) -> None:
    body = client.get("/api/board").json()
    assert len(body["cards"]) == 1 and len(body["leads"]) == 2
    card = body["cards"][0]
    assert card["subject"] == "Manny Machado" and card["reconciled"] is True
    assert card["has_image"] is True
    assert body["leads"][0]["subject"] == "Machado"  # ranked by interest desc


def test_card_image_served(client: TestClient) -> None:
    cid = client.get("/api/board").json()["cards"][0]["card_id"]
    resp = client.get(f"/api/board/cards/{cid}/image.png")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG")


def test_card_status_flip_and_validation(client: TestClient) -> None:
    cid = client.get("/api/board").json()["cards"][0]["card_id"]
    assert (
        client.post(f"/api/board/cards/{cid}/status", json={"status": "queued"}).status_code == 200
    )
    assert client.get("/api/board").json()["cards"][0]["status"] == "queued"
    assert client.post(f"/api/board/cards/{cid}/status", json={"status": "x"}).status_code == 422


def test_lead_status_flip(client: TestClient) -> None:
    lid = client.get("/api/board").json()["leads"][0]["lead_id"]
    assert (
        client.post(f"/api/board/leads/{lid}/status", json={"status": "exploring"}).status_code
        == 200
    )
    assert client.post(f"/api/board/leads/{lid}/status", json={"status": "x"}).status_code == 422


def test_missing_card_image_404(client: TestClient) -> None:
    assert client.get("/api/board/cards/deadbeef/image.png").status_code == 404


def test_sync_status_idle(client: TestClient) -> None:
    body = client.get("/api/actions/sync").json()
    assert "running" in body and "steps" in body
