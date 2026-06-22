"""Tests for the rarity-driven detectors: milestone_watch + first_since."""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

# ── fixture hist ──────────────────────────────────────────────────────────────


def _build_hist(hist: duckdb.DuckDBPyConnection) -> None:
    hist.execute("""
        CREATE TABLE bwar_player_seasons (
            mlb_id      INTEGER NOT NULL,
            name_common VARCHAR NOT NULL,
            year_id     INTEGER NOT NULL,
            team_id     VARCHAR NOT NULL,
            war         DOUBLE NOT NULL
        )
    """)

    rows = [
        # Gwynn — franchise leader, single legacy row (not active)
        (100001, "Tony Gwynn", 1982, "SDP", 69.2),
        # Winfield — 2nd all-time (not active)
        (100002, "Dave Winfield", 1977, "SDP", 32.0),
        # Machado — 3rd all-time at 27.0, NOT active in 2026; all seasons sub-6
        (592518, "Manny Machado", 2019, "SDP", 5.0),
        (592518, "Manny Machado", 2021, "SDP", 5.5),
        (592518, "Manny Machado", 2022, "SDP", 5.4),
        (592518, "Manny Machado", 2023, "SDP", 5.6),
        (592518, "Manny Machado", 2024, "SDP", 5.5),
        # Tatis — 4th all-time at 26.9, ACTIVE (2026 row), 0.1 behind Machado
        (665487, "Fernando Tatis Jr.", 2019, "SDP", 5.9),
        (665487, "Fernando Tatis Jr.", 2021, "SDP", 5.0),
        (665487, "Fernando Tatis Jr.", 2022, "SDP", 5.5),
        (665487, "Fernando Tatis Jr.", 2023, "SDP", 5.0),
        (665487, "Fernando Tatis Jr.", 2024, "SDP", 4.5),
        (665487, "Fernando Tatis Jr.", 2026, "SDP", 1.0),
        # King — 6.2-WAR 2026 season, ACTIVE; gap to the rank above exceeds 2.5
        (650001, "Michael King", 2026, "SDP", 6.2),
        # Peavy — the most recent prior 6-WAR season (2007)
        (200001, "Jake Peavy", 2007, "SDP", 9.5),
        # Routine 3-WAR seasons, most recent 2024 → 3-WAR feats stay silent
        (300001, "Role Player A", 2024, "SDP", 3.5),
        (300002, "Role Player B", 2023, "SDP", 3.4),
        (300003, "Role Player C", 2021, "SDP", 4.2),
        # Current-season routine performer — must NOT fire first_since
        (300004, "Recent Guy", 2026, "SDP", 3.2),
    ]
    hist.executemany("INSERT INTO bwar_player_seasons VALUES (?, ?, ?, ?, ?)", rows)


@pytest.fixture()
def hist_conn(
    padres_db: duckdb.DuckDBPyConnection,
    tmp_path,
) -> duckdb.DuckDBPyConnection:
    """padres.db with a minimal hist database for brain detectors."""
    hist_path = tmp_path / "hist_brain.db"
    hist = duckdb.connect(str(hist_path))
    _build_hist(hist)
    hist.close()
    padres_db.execute(f"ATTACH '{hist_path}' AS hist (READ_ONLY)")
    return padres_db


# ── MilestoneWatchDetector ────────────────────────────────────────────────────


def test_milestone_watch_fires_close_chase(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.milestones import MilestoneWatchDetector

    det = MilestoneWatchDetector()
    candidates = det.run(hist_conn, date(2026, 6, 10))

    tatis = [c for c in candidates if "665487" in (c.subject or "")]
    assert len(tatis) == 1
    c = tatis[0]
    assert c.detector == "milestone_watch"
    assert c.payload_kind == "dataset"
    assert c.facts_json["facts"]["gap_war"] == pytest.approx(0.1)
    assert c.facts_json["facts"]["target_name"] == "Manny Machado"
    assert c.facts_json["facts"]["target_rank"] == 3
    assert "passing Manny Machado" in c.facts_json["headline"]
    assert "3rd" in c.facts_json["headline"]
    assert c.novelty_score >= 0.90  # gap <= 0.5 and target rank <= 5


def test_milestone_watch_silent_when_gap_large(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.milestones import MilestoneWatchDetector

    det = MilestoneWatchDetector()
    candidates = det.run(hist_conn, date(2026, 6, 10))

    subjects = [c.subject or "" for c in candidates]
    # King's gap to the rank above is enormous — silent
    assert not any("650001" in s for s in subjects)
    # Machado is not active in 2026 — silent even though his gap to Winfield is 5.0
    assert not any("|592518|" in s for s in subjects)


def test_milestone_watch_reemit_gate(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.base import emit
    from padres_analytics.detect.milestones import MilestoneWatchDetector

    det = MilestoneWatchDetector()
    first = det.run(hist_conn, date(2026, 6, 1))
    assert first
    emit(hist_conn, first)

    second = det.run(hist_conn, date(2026, 6, 10))
    assert second == []


def test_milestone_watch_hero_shows_chase(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.milestones import MilestoneWatchDetector

    det = MilestoneWatchDetector()
    c = next(c for c in det.run(hist_conn, date(2026, 6, 10)) if "665487" in (c.subject or ""))
    # Hero card: the gap is the one big number, the chase target is in the context line.
    hero = c.facts_json["hero"]
    assert hero["value"] == "0.1"
    assert "3rd" in hero["label"]
    assert "Manny Machado" in hero["context"]
    assert c.facts_json["facts"]["player_name"] == "Fernando Tatis Jr."


# ── WarSeasonFirstSinceDetector ───────────────────────────────────────────────


def test_first_since_fires_with_drought(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.first_since import WarSeasonFirstSinceDetector

    det = WarSeasonFirstSinceDetector()
    candidates = det.run(hist_conn, date(2026, 6, 10))

    king = [c for c in candidates if "650001" in (c.subject or "")]
    assert len(king) == 1
    c = king[0]
    assert c.detector == "first_since"
    assert c.facts_json["threshold"] == 6.0
    assert c.facts_json["season_war"] == pytest.approx(6.2)
    # Precedents: Winfield 1977 + Gwynn 1982 + Peavy 2007 (legacy single rows)
    assert c.facts_json["prior_occurrences"] == 3
    assert c.facts_json["years_since_last"] == 19
    assert "since Jake Peavy in 2007" in c.facts_json["headline"]
    assert "4th in franchise history" in c.facts_json["headline"]


def test_first_since_silent_for_routine_feat(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.first_since import WarSeasonFirstSinceDetector

    det = WarSeasonFirstSinceDetector()
    candidates = det.run(hist_conn, date(2026, 6, 10))

    # Recent Guy at 3.2: latest 3-WAR precedent is 2024 (2 yrs ago) and there
    # are more than _RARE_COUNT_MAX prior occurrences — correctly silent.
    subjects = [c.subject or "" for c in candidates]
    assert not any("300004" in s for s in subjects)


def test_first_since_current_season_on_top(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.first_since import WarSeasonFirstSinceDetector

    det = WarSeasonFirstSinceDetector()
    c = next(c for c in det.run(hist_conn, date(2026, 6, 10)) if "650001" in (c.subject or ""))
    rows = c.facts_json["rows"]
    assert c.facts_json["highlight_row"] == 0
    assert rows[0] == ["2026", "Michael King", "6.2"]
    # Precedents follow, most recent first
    assert rows[1][1] == "Jake Peavy"


def test_first_since_reemit_gate(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.base import emit
    from padres_analytics.detect.first_since import WarSeasonFirstSinceDetector

    det = WarSeasonFirstSinceDetector()
    first = det.run(hist_conn, date(2026, 6, 1))
    assert first
    emit(hist_conn, first)

    second = det.run(hist_conn, date(2026, 6, 10))
    assert second == []


def test_first_since_candidate_id_stable(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.first_since import WarSeasonFirstSinceDetector

    det = WarSeasonFirstSinceDetector()
    c1 = det.run(hist_conn, date(2026, 6, 10))
    c2 = det.run(hist_conn, date(2026, 6, 10))
    assert [c.candidate_id for c in c1] == [c.candidate_id for c in c2]
