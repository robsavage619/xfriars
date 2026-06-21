"""Tests for the daily briefing — the engine's orchestration heartbeat."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from padres_analytics.board import list_cards
from padres_analytics.daily import build_caption, run_briefing
from padres_analytics.detect.angles import Stat, StoryAngle

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


def test_build_caption_carries_verdict_gloss_and_confidence() -> None:
    """The draft caption leads with the headline, glosses the jargon, states confidence."""
    angle = StoryAngle(
        key="change",
        subject="Gavin Sheets",
        title="HIT A WALL",
        headline="Gavin Sheets has cooled hard — 176 points of on-base off his prior form.",
        thesis="t",
        direction="down",
        effect=176,
        reliability=0.96,
        interest=1.0,
        confidence="high",
        as_of=date(2026, 6, 20),
        stats=[Stat("chg_recent", 0.267, "woba", "recent OBP", 60, shown=False)],
        caveats=["15-game windows, 121 PA — a results split, not a talent verdict"],
    )
    cap = build_caption(angle)
    assert cap.startswith("Gavin Sheets has cooled hard")
    assert "In plain terms: on-base percentage is" in cap  # the glossary gloss
    assert "High confidence" in cap and "results split" in cap


def test_run_briefing_queues_a_verified_story(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A clear change surfaces, verifies, gets a caption, and lands on the Board."""
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
    assert b.caption and "In plain terms" in b.caption
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
