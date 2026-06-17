"""Tests for the career-chase gem engine."""

from __future__ import annotations

from datetime import date

import duckdb

from padres_analytics.detect.gems import CareerChaseDetector


def _conn(rows: list[tuple[int, str, int, int]]) -> duckdb.DuckDBPyConnection:
    """rows = (player_id, name, season, hr)."""
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE player_season_batting (
            player_id INTEGER, player_name VARCHAR, season INTEGER, team_id INTEGER,
            games INTEGER, pa INTEGER, ab INTEGER, runs INTEGER, hits INTEGER,
            doubles INTEGER, triples INTEGER, hr INTEGER, rbi INTEGER, sb INTEGER,
            bb INTEGER, so INTEGER, avg VARCHAR, obp VARCHAR, slg VARCHAR, ops VARCHAR
        )
    """)
    for pid, name, season, hr in rows:
        conn.execute(
            "INSERT INTO player_season_batting (player_id, player_name, season, team_id, hr, "
            "hits, rbi, sb, doubles) VALUES (?, ?, ?, 135, ?, 0, 0, 0, 0)",
            [pid, name, season, hr],
        )
    return conn


def _legends_plus(active_hr: int, active_season: int = 2026) -> list[tuple[int, str, int, int]]:
    """A 10-deep HR board of retired legends + one active player at active_hr."""
    rows = [(900 + i, f"Legend {i}", 1990, 300 - i * 20) for i in range(10)]  # 300,280,...120
    rows.append((1, "Active Star", active_season, active_hr))
    return rows


def test_franchise_leader_gem() -> None:
    # Active star with 320 HR tops everyone → franchise record gem
    conn = _conn(_legends_plus(320))
    cands = CareerChaseDetector().run(conn, date(2026, 6, 16))
    hr = next(c for c in cands if c.facts_json["facts"]["stat"] == "HR")
    assert hr.facts_json["facts"]["tier"] == "franchise_record"
    assert "all-time home run leader" in hr.facts_json["headline"]
    assert hr.facts_json["facts"]["franchise_rank"] == 1


def test_chase_gem_names_the_legend() -> None:
    # Active star at 277 HR, just behind "Legend 1" (280) → chase gem with anchor
    conn = _conn(_legends_plus(277))
    cands = CareerChaseDetector().run(conn, date(2026, 6, 16))
    hr = next(c for c in cands if c.facts_json["facts"]["stat"] == "HR")
    assert hr.facts_json["facts"]["tier"] == "chase"
    head = hr.facts_json["headline"]
    assert "needs 3 to pass Legend 1" in head
    assert "all-time home run list" in head


def test_active_player_highlighted_on_card() -> None:
    conn = _conn(_legends_plus(320))
    hr = next(
        c
        for c in CareerChaseDetector().run(conn, date(2026, 6, 16))
        if c.facts_json["facts"]["stat"] == "HR"
    )
    assert any(m["label"] == "Active Star" for m in hr.facts_json["highlight"])
    assert hr.facts_json["card_hint"] == "bar"


def test_no_active_player_no_gem() -> None:
    # All players retired (no 2026 season) → no gems
    rows = [(900 + i, f"Legend {i}", 1990, 300 - i * 20) for i in range(10)]
    cands = CareerChaseDetector().run(_conn(rows), date(2026, 6, 16))
    assert cands == []


def test_no_data_returns_empty() -> None:
    assert CareerChaseDetector().run(duckdb.connect(), date(2026, 6, 16)) == []
