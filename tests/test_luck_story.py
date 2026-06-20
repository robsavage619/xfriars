"""Tests for the luck-story composer + infographic SVG builder."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from padres_analytics.detect.luck_story import REGRESSION_PA_PRIOR, _regress, build_luck_story
from padres_analytics.render.story_infographic import build_svg

if TYPE_CHECKING:
    import duckdb

_NOW = datetime(2026, 6, 20, 0, 0, 0)


def _aux_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the tables the ingests (not the base schema) normally make."""
    conn.execute("CREATE TABLE IF NOT EXISTS team_rosters (player_id INTEGER, player_name VARCHAR)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS player_game_batting (player_id INTEGER, player_name VARCHAR, "
        "season INTEGER, game_date DATE, game_pk INTEGER, ab INTEGER, hits INTEGER, bb INTEGER, "
        "hbp INTEGER, source VARCHAR, ingested_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS standings (team_id INTEGER, team_abbr VARCHAR, "
        "team_name VARCHAR, division_id INTEGER, season INTEGER, wins INTEGER, losses INTEGER, "
        "win_pct DOUBLE, games_back VARCHAR, source VARCHAR, ingested_at TIMESTAMP)"
    )


def _roster(conn: duckdb.DuckDBPyConnection, pid: int, name: str) -> None:
    conn.execute("INSERT INTO team_rosters VALUES (?, ?)", [pid, name])


def _expected(
    conn: duckdb.DuckDBPyConnection, pid: int, name: str, pa: int, woba: float, xwoba: float
) -> None:
    conn.execute(
        "INSERT INTO statcast_batting_expected "
        "(player_id, player_name, year, pa, bip, ba, est_ba, slg, est_slg, woba, est_woba, "
        "ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [pid, name, 2026, pa, pa - 30, 0.250, 0.250, 0.400, 0.400, woba, xwoba, _NOW],
    )


def _ev(conn: duckdb.DuckDBPyConnection, pid: int, name: str, attempts: int, ev: float) -> None:
    conn.execute(
        "INSERT INTO statcast_batter_exitvelo_barrels "
        "(player_id, player_name, year, attempts, avg_hit_speed, max_hit_speed, barrels, "
        "brl_percent, ingested_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [pid, name, 2026, attempts, ev, ev + 15, 10, 8.0, _NOW],
    )


def _game(conn: duckdb.DuckDBPyConnection, pid: int, gd: str, ab: int, hits: int) -> None:
    conn.execute(
        "INSERT INTO player_game_batting "
        "(player_id, player_name, season, game_date, game_pk, ab, hits, bb, hbp, source, "
        "ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [pid, "x", 2026, gd, 1, ab, hits, 0, 0, "test", _NOW],
    )


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    _aux_tables(conn)
    # League anchor — non-Padres hitters near .322 xwOBA.
    for i in range(5):
        _expected(conn, 900 + i, f"League, Guy{i}", 300, 0.320, 0.322)
        _ev(conn, 900 + i, f"League, Guy{i}", 120, 88.0)
    # Padres regulars — all hitting under their expected line (unlucky).
    pad = [
        (1, "Tatis Jr., Fernando", 300, 0.290, 0.330),
        (2, "Bogaerts, Xander", 250, 0.280, 0.320),
        (3, "Merrill, Jackson", 150, 0.300, 0.310),
    ]
    for pid, name, pa, woba, xwoba in pad:
        _roster(conn, pid, name)
        _expected(conn, pid, name, pa, woba, xwoba)
        _ev(conn, pid, name, 150, 90.0 + pid * 0.4)
        for d, (ab, h) in zip(
            ("2026-06-15", "2026-06-17", "2026-06-19"), ((10, 1), (12, 5), (11, 3)), strict=True
        ):
            _game(conn, pid, d, ab, h)
    conn.execute(
        "INSERT INTO standings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [135, "SD", "Padres", 203, 2026, 38, 36, 38 / 74, "10.0", "test", _NOW],
    )
    conn.execute(
        "INSERT INTO standings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [119, "LAD", "Dodgers", 203, 2026, 49, 27, 49 / 76, "-", "test", _NOW],
    )


def test_regress_formula() -> None:
    """At the 220-PA break-even, observation and prior are weighted equally."""
    assert _regress(0.400, REGRESSION_PA_PRIOR, 0.320) == (0.400 + 0.320) / 2


def test_luck_story_composes(padres_db: duckdb.DuckDBPyConnection) -> None:
    """The story pulls the gauge, dumbbell, regression, and hook from live tables."""
    _seed(padres_db)
    story = build_luck_story(padres_db, 2026, as_of=date(2026, 6, 20))
    assert story is not None

    # Macro hook
    assert story.record == (38, 36)
    assert story.leader == "Dodgers"
    assert round(story.games_back) == 10

    # Luck gauge — the lineup is under its expected line.
    assert story.luck_gap_pts < 0
    assert story.team_woba < story.team_xwoba

    # Regression counterpoint — owed a bounce, true talent lands near league average.
    assert story.owed_pts > 0
    assert abs(story.true_talent - story.league_xwoba) < 0.015
    assert [p.label for p in story.ladder] == ["actual", "true talent", "league avg"]

    # Dumbbell sorted by expected wOBA descending.
    xwobas = [xw for _, _, xw in story.dumbbell]
    assert xwobas == sorted(xwobas, reverse=True)
    assert story.headline


def test_luck_story_svg_renders(padres_db: duckdb.DuckDBPyConnection) -> None:
    """build_svg emits a well-formed SVG carrying the headline numbers and panels."""
    _seed(padres_db)
    story = build_luck_story(padres_db, 2026, as_of=date(2026, 6, 20))
    assert story is not None
    svg = build_svg(story)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    for label in (
        "EVERY REGULAR IS OWED",
        "THE TEAM LUCK GAP",
        "THE CONTACT IS REAL",
        "BUT — REGRESSION TO WHAT?",
        "xFriars",
    ):
        assert label in svg


def test_luck_story_none_without_data(padres_db: duckdb.DuckDBPyConnection) -> None:
    """No roster / expected stats → no story (fail visibly, not a blank card)."""
    _aux_tables(padres_db)
    assert build_luck_story(padres_db, 2026) is None
