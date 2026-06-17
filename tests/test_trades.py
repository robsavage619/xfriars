"""Tests for the deadline-history detector."""

from __future__ import annotations

from datetime import date

import duckdb

from padres_analytics.detect.trades import DeadlineHistoryDetector


def _conn(legs: list[tuple[int, str, str]]) -> duckdb.DuckDBPyConnection:
    """legs = (season, iso_date, player_name) acquired by SDP."""
    conn = duckdb.connect()
    conn.execute("CREATE SCHEMA hist")
    conn.execute("""
        CREATE TABLE hist.trade_player_unified (
            trade_season INTEGER, date DATE, to_team_bref VARCHAR, player_name VARCHAR
        )
    """)
    for season, d, name in legs:
        conn.execute(
            "INSERT INTO hist.trade_player_unified VALUES (?, ?, 'SDP', ?)", [season, d, name]
        )
    return conn


def test_deadline_history_counts_by_year_and_names_latest() -> None:
    legs = [
        (2023, "2023-07-31", "Player A"),
        (2024, "2024-07-30", "Tanner Scott"),
        (2024, "2024-07-30", "Jason Adam"),
        (2025, "2025-07-31", "Nestor Cortes"),
        (2025, "2025-07-31", "Ramón Laureano"),
        (2025, "2025-07-31", "Freddy Fermin"),
        (2024, "2024-03-13", "Dylan Cease"),  # March — not a deadline add, excluded
    ]
    cands = DeadlineHistoryDetector().run(_conn(legs), date(2026, 6, 16))
    assert len(cands) == 1
    f = cands[0].facts_json
    assert f["facts"]["latest_year"] == 2025
    assert f["facts"]["latest_additions"] == 3  # only July 2025 legs
    assert "added 3 players at the 2025 deadline" in f["headline"]
    assert "Nestor Cortes" in f["headline"]
    assert "2026 deadline" in f["headline"]
    # March acquisition (Cease) excluded from deadline counts
    rows = {yr: n for yr, n in f["rows"]}
    assert rows["2024"] == 2


def test_deadline_history_excludes_current_year() -> None:
    legs = [(y, f"{y}-07-31", f"P{y}") for y in (2022, 2023, 2024, 2025)]
    legs.append((2026, "2026-07-31", "Should Not Count"))  # current year excluded
    cands = DeadlineHistoryDetector().run(_conn(legs), date(2026, 6, 16))
    years = {yr for yr, _ in cands[0].facts_json["rows"]}
    assert "2026" not in years


def test_deadline_history_empty_without_data() -> None:
    assert DeadlineHistoryDetector().run(_conn([]), date(2026, 6, 16)) == []
