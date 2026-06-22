"""Tests for the honest-negatives engine (cold streaks + weaknesses)."""

from __future__ import annotations

from datetime import date

import duckdb

from padres_analytics.detect.struggles import ColdStreakDetector, WeaknessDetector


def _game_conn(games: list[tuple[str, int, int]]) -> duckdb.DuckDBPyConnection:
    """games = (game_date, ab, hits) for one player (id 1), chronological."""
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE player_game_batting (
            player_id INTEGER, player_name VARCHAR, season INTEGER,
            game_date DATE, game_pk INTEGER, ab INTEGER, hits INTEGER, bb INTEGER, hbp INTEGER
        )
    """)
    for i, (gdate, ab, hits) in enumerate(games):
        conn.execute(
            "INSERT INTO player_game_batting VALUES (1, 'Slumper', 2026, ?, ?, ?, ?, 0, 0)",
            [gdate, 1000 + i, ab, hits],
        )
    return conn


def test_cold_streak_fires_with_skid() -> None:
    # 6 straight hitless games, 4 AB each = 0-for-24
    games = [(f"2026-06-{1 + i:02d}", 4, 0) for i in range(6)]
    cands = ColdStreakDetector().run(_game_conn(games), date(2026, 6, 16))
    assert len(cands) == 1
    f = cands[0].facts_json["facts"]
    assert f["skid_games"] == 6
    assert f["skid_ab"] == 24
    assert "0-for-his-last-24" in cands[0].facts_json["headline"]
    assert cands[0].category == "struggle"


def test_cold_streak_broken_by_recent_hit() -> None:
    # Most recent game has a hit → no active skid
    games = [(f"2026-06-{1 + i:02d}", 4, 0) for i in range(6)]
    games.append(("2026-06-09", 4, 2))  # snapped it
    assert ColdStreakDetector().run(_game_conn(games), date(2026, 6, 16)) == []


def test_cold_streak_too_short_suppressed() -> None:
    games = [(f"2026-06-{1 + i:02d}", 3, 0) for i in range(3)]  # only 3 games / 9 AB
    assert ColdStreakDetector().run(_game_conn(games), date(2026, 6, 16)) == []


# ── WeaknessDetector ──────────────────────────────────────────────────────────


def _weakness_conn(chase_pct: float, pa: int = 200) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE player_season_batting (
            player_id INTEGER, season INTEGER, team_id INTEGER, pa INTEGER
        )
    """)
    conn.execute("INSERT INTO player_season_batting VALUES (1, 2026, 135, ?)", [pa])
    conn.execute("""
        CREATE TABLE statcast_batter_percentile_ranks (
            player_id INTEGER, player_name VARCHAR, year INTEGER,
            xwoba DOUBLE, whiff_percent DOUBLE, chase_percent DOUBLE,
            k_percent DOUBLE, hard_hit_percent DOUBLE, exit_velocity DOUBLE
        )
    """)
    conn.execute(
        "INSERT INTO statcast_batter_percentile_ranks "
        "VALUES (1, 'Free Swinger', 2026, 60, 40, ?, 55, 70, 65)",
        [chase_pct],
    )
    return conn


def test_weakness_fires_for_bottom_percentile() -> None:
    cands = WeaknessDetector().run(_weakness_conn(5.0), date(2026, 6, 16))
    assert len(cands) == 1
    assert cands[0].facts_json["facts"]["metric"] == "Chase %"
    assert "5th percentile in Chase %" in cands[0].facts_json["headline"]
    assert "bottom 5% in MLB" in cands[0].facts_json["headline"]


def test_weakness_suppressed_when_not_poor() -> None:
    # All tools >= 40th percentile → no weakness fires
    assert WeaknessDetector().run(_weakness_conn(45.0), date(2026, 6, 16)) == []


def test_weakness_skips_non_regular() -> None:
    # pa < 100 → not a regular → no card
    assert WeaknessDetector().run(_weakness_conn(3.0, pa=40), date(2026, 6, 16)) == []
