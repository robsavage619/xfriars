"""Tests for the leads scout."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from padres_analytics.detect.leads import digest, scout

if TYPE_CHECKING:
    import duckdb

_NOW = datetime(2026, 6, 20)


def _setup(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS team_rosters "
        "(player_id INTEGER, player_name VARCHAR, status VARCHAR)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS player_season_batting (player_id INTEGER, season INTEGER, "
        "pa INTEGER, ops VARCHAR, ingested_at TIMESTAMP)"
    )
    # active down-year star + injured star (must be excluded)
    conn.execute("INSERT INTO team_rosters VALUES (1, 'Star, Down', 'Active')")
    conn.execute("INSERT INTO team_rosters VALUES (2, 'Hurt, Guy', 'Injured 60-Day')")
    for pid, nm, w, x in [(1, "Down, Star", 0.270, 0.330), (2, "Hurt, Guy", 0.300, 0.360)]:
        conn.execute(
            "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa, bip, ba, "
            "est_ba, slg, est_slg, woba, est_woba, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [pid, nm, 2026, 300, 270, 0.25, 0.25, 0.4, 0.4, w, x, _NOW],
        )
    for s, ops in [(2024, ".820"), (2025, ".810"), (2026, ".600")]:
        conn.execute("INSERT INTO player_season_batting VALUES (1, ?, 300, ?, ?)", [s, ops, _NOW])


def test_scout_surfaces_active_player_leads(padres_db) -> None:
    _setup(padres_db)
    leads = scout(padres_db, 2026, as_of=date(2026, 6, 20))
    subjects = {x.subject for x in leads}
    assert "Down" in subjects  # the active down-year star
    assert "Guy" not in subjects  # injured -> never a lead
    kinds = {x.kind for x in leads}
    assert "down_year" in kinds and "luck" in kinds
    # ranked by interest descending
    assert [x.interest for x in leads] == sorted((x.interest for x in leads), reverse=True)


def test_digest_renders_markdown(padres_db) -> None:
    _setup(padres_db)
    md = digest(scout(padres_db, 2026, as_of=date(2026, 6, 20)), date(2026, 6, 20))
    assert md.startswith("# xFriars leads")
    assert "Why is Down" in md or "luck story on Down" in md


def test_empty_when_no_roster(padres_db) -> None:
    assert scout(padres_db, 2026, as_of=date(2026, 6, 20)) == []
