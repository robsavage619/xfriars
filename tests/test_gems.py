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


# ── MilestoneClubDetector ─────────────────────────────────────────────────────


def test_milestone_club_exclusive_fires() -> None:
    from padres_analytics.detect.gems import MilestoneClubDetector

    # Active star at 195 HR (5 from the 200 club); only 1 retired legend already at 200.
    rows = [(900, "Legend", 1990, 250), (1, "Active Star", 2026, 195)]
    cands = MilestoneClubDetector().run(_conn(rows), date(2026, 6, 16))
    hr = next(c for c in cands if c.facts_json["facts"]["stat"] == "HR")
    assert hr.facts_json["facts"]["milestone"] == 200
    assert hr.facts_json["facts"]["gap"] == 5
    assert hr.facts_json["facts"]["would_be_nth"] == 2  # 2nd ever
    assert "2nd Padre ever to reach 200" in hr.facts_json["headline"]


def test_milestone_club_non_exclusive_suppressed() -> None:
    from padres_analytics.detect.gems import MilestoneClubDetector

    # 20 legends already past 100 HR; an active player 5 away is the 21st — not a gem.
    rows = [(900 + i, f"Legend {i}", 1990, 120) for i in range(20)]
    rows.append((1, "Active Star", 2026, 95))
    cands = MilestoneClubDetector().run(_conn(rows), date(2026, 6, 16))
    assert not any(c.facts_json["facts"]["stat"] == "HR" for c in cands)


# ── HitStreakDetector ─────────────────────────────────────────────────────────


def _game_conn(games: list[tuple[int, str, int, int]]) -> duckdb.DuckDBPyConnection:
    """games = (day_offset, game_date, ab, hits) for one player (id 1)."""
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE player_game_batting (
            player_id INTEGER, player_name VARCHAR, season INTEGER,
            game_date DATE, game_pk INTEGER, ab INTEGER, hits INTEGER, bb INTEGER, hbp INTEGER
        )
    """)
    for i, (_d, gdate, ab, hits) in enumerate(games):
        conn.execute(
            "INSERT INTO player_game_batting VALUES (1, 'Streak Guy', 2026, ?, ?, ?, ?, 0, 0)",
            [gdate, 1000 + i, ab, hits],
        )
    return conn


def test_hit_streak_fires_and_counts() -> None:
    from padres_analytics.detect.gems import HitStreakDetector

    # 9 straight games with a hit (chronological); streak = 9
    games = [(i, f"2026-06-{1 + i:02d}", 4, 1) for i in range(9)]
    cands = HitStreakDetector().run(_game_conn(games), date(2026, 6, 16))
    assert len(cands) == 1
    assert cands[0].facts_json["facts"]["streak_games"] == 9
    assert "9 straight games" in cands[0].facts_json["headline"]


def test_hit_streak_no_ab_game_does_not_break() -> None:
    from padres_analytics.detect.gems import HitStreakDetector

    # 8 hits, then a walk-only game (0 AB) most recent — streak stays 8, not broken
    games = [(i, f"2026-06-{1 + i:02d}", 4, 1) for i in range(8)]
    games.append((8, "2026-06-09", 0, 0))  # walk-only, most recent
    cands = HitStreakDetector().run(_game_conn(games), date(2026, 6, 16))
    assert cands and cands[0].facts_json["facts"]["streak_games"] == 8


def test_hit_streak_below_threshold_suppressed() -> None:
    from padres_analytics.detect.gems import HitStreakDetector

    games = [(i, f"2026-06-{1 + i:02d}", 4, 1) for i in range(5)]  # only 5
    assert HitStreakDetector().run(_game_conn(games), date(2026, 6, 16)) == []


# ── CareerConjunctionDetector ─────────────────────────────────────────────────


def _conn_hr_sb(rows: list[tuple[int, str, int, int, int]]) -> duckdb.DuckDBPyConnection:
    """rows = (player_id, name, season, hr, sb)."""
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE player_season_batting (
            player_id INTEGER, player_name VARCHAR, season INTEGER, team_id INTEGER,
            games INTEGER, pa INTEGER, ab INTEGER, runs INTEGER, hits INTEGER,
            doubles INTEGER, triples INTEGER, hr INTEGER, rbi INTEGER, sb INTEGER,
            bb INTEGER, so INTEGER, avg VARCHAR, obp VARCHAR, slg VARCHAR, ops VARCHAR
        )
    """)
    for pid, name, season, hr, sb in rows:
        conn.execute(
            "INSERT INTO player_season_batting (player_id, player_name, season, team_id, hr, sb, "
            "hits) VALUES (?, ?, ?, 135, ?, ?, 0)",
            [pid, name, season, hr, sb],
        )
    return conn


def test_conjunction_only_padre_ever() -> None:
    from padres_analytics.detect.gems import CareerConjunctionDetector

    # Active star uniquely has 200+ HR and 50+ SB; legends miss one leg.
    rows = [
        (900, "Power Only", 1990, 300, 10),  # HR yes, SB no
        (901, "Speed Only", 1990, 50, 300),  # SB yes, HR no
        (1, "Active Star", 2026, 206, 61),  # both
    ]
    cands = CareerConjunctionDetector().run(_conn_hr_sb(rows), date(2026, 6, 16))
    hr_sb = next(c for c in cands if c.facts_json["facts"]["club_size"] == 1)
    assert "ONLY Padre ever with 200+ HR and 50+ SB" in hr_sb.facts_json["headline"]
    assert hr_sb.facts_json["facts"]["padre_player_id"] == 1


def test_conjunction_non_exclusive_club_suppressed() -> None:
    from padres_analytics.detect.gems import CareerConjunctionDetector

    # 10 players all in the 100-HR/50-SB club → not exclusive (> _MAX_CONJ_CLUB).
    rows = [(900 + i, f"P{i}", 1990, 120, 80) for i in range(10)]
    rows.append((1, "Active Star", 2026, 130, 90))
    cands = CareerConjunctionDetector().run(_conn_hr_sb(rows), date(2026, 6, 16))
    assert not any(c.facts_json["facts"].get("club_size", 99) <= 6 for c in cands)


# ── Team scoping ──────────────────────────────────────────────────────────────
# Every fixture above inserts team_id=135 unconditionally, so a missing team
# filter was invisible to them: the franchise leaderboards silently ranked all
# 30 clubs and produced "Aaron Judge is the Padres' all-time home run leader
# (302 HR)". These fixtures put non-Padres in the table on purpose.


def _conn_two_teams(rows: list[tuple[int, str, int, int, int]]) -> duckdb.DuckDBPyConnection:
    """rows = (player_id, name, season, hr, team_id)."""
    conn = _conn([])
    for pid, name, season, hr, team_id in rows:
        conn.execute(
            "INSERT INTO player_season_batting (player_id, player_name, season, team_id, hr, "
            "hits, rbi, sb, doubles) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0)",
            [pid, name, season, team_id, hr],
        )
    return conn


def test_career_leaderboard_excludes_other_teams() -> None:
    # A Yankee with more career HR than any Padre must not top the Padres board.
    rows = [(900 + i, f"Padre {i}", 1990, 200 - i * 10, 135) for i in range(10)]
    rows.append((1, "Active Padre", 2026, 210, 135))
    rows.append((2, "Yankee Slugger", 2026, 302, 147))
    cands = CareerChaseDetector().run(_conn_two_teams(rows), date(2026, 6, 16))

    hr = next(c for c in cands if c.facts_json["facts"]["stat"] == "HR")
    assert hr.facts_json["facts"]["player_name"] == "Active Padre"
    assert "Yankee Slugger" not in hr.facts_json["headline"]
    assert all(m["label"] != "Yankee Slugger" for m in hr.facts_json["highlight"])


def test_career_total_counts_only_padres_seasons() -> None:
    # A player's other-team seasons must not inflate his franchise total.
    rows = [(900 + i, f"Padre {i}", 1990, 100 - i * 5, 135) for i in range(10)]
    rows.append((1, "Traded Star", 2024, 40, 147))  # 40 HR as a Yankee
    rows.append((1, "Traded Star", 2026, 120, 135))  # 120 HR as a Padre
    cands = CareerChaseDetector().run(_conn_two_teams(rows), date(2026, 6, 16))

    hr = next(c for c in cands if c.facts_json["facts"]["stat"] == "HR")
    assert hr.facts_json["facts"]["career_total"] == 120


def test_active_set_excludes_other_teams() -> None:
    # An out-of-town player active this season is not eligible for a Padres chase.
    rows = [(900 + i, f"Padre {i}", 1990, 300 - i * 20, 135) for i in range(10)]
    rows.append((2, "Yankee Slugger", 2026, 295, 147))
    cands = CareerChaseDetector().run(_conn_two_teams(rows), date(2026, 6, 16))

    assert all("Yankee Slugger" not in (c.facts_json.get("headline") or "") for c in cands)
