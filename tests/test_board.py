"""Tests for the board store (where generated cards + leads land)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.board import (
    add_card,
    add_leads,
    list_cards,
    list_leads,
    set_card_status,
    set_lead_status,
)
from padres_analytics.detect.angles import Stat, StoryAngle
from padres_analytics.detect.leads import Lead

if TYPE_CHECKING:
    import duckdb


def _angle(subject="Manny Machado", title="BETTER THAN THE LINE") -> StoryAngle:
    return StoryAngle(
        key="player_luck",
        subject=subject,
        title=title,
        headline="h",
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


def test_add_and_list_card(padres_db: duckdb.DuckDBPyConnection) -> None:
    add_card(padres_db, _angle(), "/tmp/x.png", kind="season_story", reconciled=True)
    cards = list_cards(padres_db)
    assert len(cards) == 1
    c = cards[0]
    assert c["kind"] == "season_story" and c["subject"] == "Manny Machado"
    assert c["reconciled"] is True and c["status"] == "new"
    assert c["rank_note"].startswith("OPS 216")
    assert c["caption"] == "h"  # defaults to headline


def test_card_is_idempotent_on_identity(padres_db: duckdb.DuckDBPyConnection) -> None:
    add_card(padres_db, _angle(), "/tmp/a.png", kind="season_story", reconciled=True)
    add_card(padres_db, _angle(), "/tmp/b.png", kind="season_story", reconciled=True)
    cards = list_cards(padres_db)
    assert len(cards) == 1 and cards[0]["image_path"] == "/tmp/b.png"  # refreshed


def test_card_status_flip(padres_db: duckdb.DuckDBPyConnection) -> None:
    add_card(padres_db, _angle(), "/tmp/x.png", kind="season_story", reconciled=True)
    cid = list_cards(padres_db)[0]["card_id"]
    assert set_card_status(padres_db, cid, "queued")
    assert list_cards(padres_db, status="queued")[0]["card_id"] == cid
    assert not set_card_status(padres_db, cid, "bogus")


def test_leads_refresh_keeps_dismissed(padres_db: duckdb.DuckDBPyConnection) -> None:
    leads = [
        Lead("Machado", "down_year", "Machado: .609 OPS, 216 below career", "dig in", 64),
        Lead("Tatis", "luck", "Tatis: -37 pts vs expected", "dig in", 21),
    ]
    add_leads(padres_db, leads)
    assert len(list_leads(padres_db)) == 2
    # ranked by interest desc
    assert list_leads(padres_db)[0]["subject"] == "Machado"
    # dismiss Tatis, re-scout -> Tatis stays gone, Machado refreshes
    tatis_id = next(x["lead_id"] for x in list_leads(padres_db) if x["subject"] == "Tatis")
    assert set_lead_status(padres_db, tatis_id, "dismissed")
    add_leads(padres_db, leads)
    subjects = {x["subject"] for x in list_leads(padres_db, status="new")}
    assert "Machado" in subjects and "Tatis" not in subjects
