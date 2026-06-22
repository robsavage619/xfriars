"""Tests for the nl_west_race detector and games-back math."""

from __future__ import annotations

from datetime import date

import duckdb

from padres_analytics.detect.standings import NlWestRaceDetector, _games_back


def _conn_with_standings(
    rows: list[tuple[int, int, int, float]], schema: str = "main"
) -> duckdb.DuckDBPyConnection:
    """In-memory DB with a standings table. rows = (team_id, wins, losses, win_pct)."""
    conn = duckdb.connect()
    table = "standings" if schema == "main" else "hist.standings"
    if schema != "main":
        conn.execute("CREATE SCHEMA hist")
    conn.execute(f"""
        CREATE TABLE {table} (
            team_id INTEGER, season INTEGER, wins INTEGER, losses INTEGER, win_pct DOUBLE
        )
    """)
    for tid, w, ls, p in rows:
        conn.execute(f"INSERT INTO {table} VALUES (?, 2026, ?, ?, ?)", [tid, w, ls, p])
    return conn


# Real 2026 NL West (matches the live MLB API): LAD 45-27, SD 37-33, AZ 36-35, SF, COL
_REAL = [
    (119, 45, 27, 0.625),
    (135, 37, 33, 0.529),
    (109, 36, 35, 0.507),
    (137, 29, 43, 0.403),
    (115, 27, 45, 0.375),
]


def test_games_back_math() -> None:
    assert _games_back((45, 27), (37, 33)) == 7.0
    assert _games_back((45, 27), (45, 27)) == 0.0


def test_nl_west_padres_behind() -> None:
    conn = _conn_with_standings(_REAL)
    c = NlWestRaceDetector().run(conn, date(2026, 6, 14))[0]
    assert c.facts_json["facts"]["games_back"] == 7.0
    assert c.facts_json["facts"]["padres_wins"] == 37
    assert "7.0 games back of LAD" in c.facts_json["headline"]
    # Padres row highlighted
    assert any(m["label"] == "Padres" for m in c.facts_json["highlight"])


def test_nl_west_padres_leading() -> None:
    rows = [(135, 45, 27, 0.625), (119, 40, 32, 0.556), (109, 36, 35, 0.507)]
    c = NlWestRaceDetector().run(_conn_with_standings(rows), date(2026, 6, 14))[0]
    assert "lead the NL West" in c.facts_json["headline"]
    assert c.facts_json["facts"]["games_back"] == 0.0


def test_prefers_fresh_main_over_hist() -> None:
    """main.standings (real) must win over hist.standings (simulated)."""
    conn = _conn_with_standings(_REAL, schema="main")
    conn.execute("CREATE SCHEMA hist")
    conn.execute(
        "CREATE TABLE hist.standings (team_id INTEGER, season INTEGER, "
        "wins INTEGER, losses INTEGER, win_pct DOUBLE)"
    )
    # Simulated: Padres only 1.5 back
    for tid, w, ls, p in [(119, 31, 19, 0.620), (135, 29, 20, 0.592)]:
        conn.execute("INSERT INTO hist.standings VALUES (?, 2026, ?, ?, ?)", [tid, w, ls, p])
    c = NlWestRaceDetector().run(conn, date(2026, 6, 14))[0]
    # Should report the real 7.0, not the simulated 1.5
    assert c.facts_json["facts"]["games_back"] == 7.0


def test_no_standings_returns_empty() -> None:
    conn = duckdb.connect()
    assert NlWestRaceDetector().run(conn, date(2026, 6, 14)) == []
