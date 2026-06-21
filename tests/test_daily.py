"""Tests for the daily briefing — the engine's orchestration heartbeat."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from padres_analytics.board import list_cards
from padres_analytics.daily import run_briefing
from padres_analytics.detect.angles import StoryAngle

if TYPE_CHECKING:
    import duckdb

_NOW = datetime(2026, 6, 20, 0, 0, 0)


def _stub_render(angle: StoryAngle, out_dir: Path, stem: str) -> Path:
    """A render that doesn't spin up a browser — just names the file."""
    return out_dir / f"{stem}.png"


def _league(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS team_rosters (player_id INTEGER, player_name VARCHAR)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS player_game_batting (player_id INTEGER, player_name VARCHAR, "
        "season INTEGER, game_date DATE, game_pk INTEGER, ab INTEGER, hits INTEGER, bb INTEGER, "
        "hbp INTEGER, source VARCHAR, ingested_at TIMESTAMP)"
    )
    for i in range(6):
        conn.execute(
            "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa, bip, ba, "
            "est_ba, slg, est_slg, woba, est_woba, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [900 + i, f"Lg {i}", 2026, 300, 270, 0.25, 0.25, 0.40, 0.40, 0.320, 0.322, _NOW],
        )


def test_run_briefing_queues_a_verified_story(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A clear change surfaces, verifies, gets an engagement-shaped post, lands on Board."""
    _league(padres_db)
    padres_db.execute("INSERT INTO team_rosters VALUES (1, 'Sheets, Gavin')")
    _game = (
        "INSERT INTO player_game_batting (player_id, player_name, season, game_date, game_pk, "
        "ab, hits, bb, hbp, source, ingested_at) "
        "VALUES (1,'Sheets, Gavin',2026,?,?,4,?,0,0,'t',?)"
    )
    for i in range(15):  # prior window: hot (.500), then recent window: cold (.000)
        padres_db.execute(_game, [f"2026-06-{i + 1:02d}", i, 2, _NOW])
    for i in range(15):
        padres_db.execute(_game, [f"2026-06-{i + 16:02d}", 100 + i, 0, _NOW])

    b = run_briefing(
        padres_db, 2026, as_of=date(2026, 7, 5), out_dir=Path("/tmp"), render_fn=_stub_render
    )
    assert b.story is not None and b.story.key == "change"
    assert b.caption and "?" in b.caption  # the post ends on a reply hook
    assert b.reply and "In plain terms" in b.reply  # the gloss rides in the first reply
    assert not b.warnings  # a clean, algorithm-aligned post
    assert b.image_path and b.image_path.endswith(".png")
    cards = list_cards(padres_db)
    assert any(c["angle_key"] == "change" for c in cards)  # queued for approval


def test_run_briefing_reports_a_quiet_day(padres_db: duckdb.DuckDBPyConnection) -> None:
    """No qualifying story → no card, but grading and the scorecard still run."""
    _league(padres_db)
    padres_db.execute("INSERT INTO team_rosters VALUES (1, 'Quiet, Quinn')")
    padres_db.execute(
        "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa, bip, ba, est_ba, "
        "slg, est_slg, woba, est_woba, ingested_at) VALUES (1,'Quiet, Quinn',2026,300,270,.25,.25,"
        ".40,.40,.321,.321,?)",
        [_NOW],
    )
    b = run_briefing(
        padres_db, 2026, as_of=date(2026, 7, 5), out_dir=Path("/tmp"), render_fn=_stub_render
    )
    assert b.story is None
    assert any("No story" in n for n in b.notes)
    assert b.scorecard["graded"] == 0  # grading ran, nothing to grade
