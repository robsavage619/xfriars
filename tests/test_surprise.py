"""Tests for the surprise + novelty ranking layer."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from padres_analytics.detect.angles import Stat, StoryAngle
from padres_analytics.detect.surprise import novelty, subject_surprise

if TYPE_CHECKING:
    import duckdb

_NOW = datetime(2026, 6, 20)


def _psb(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS player_season_batting (player_id INTEGER, player_name VARCHAR, "
        "season INTEGER, team_id INTEGER, games INTEGER, pa INTEGER, ab INTEGER, runs INTEGER, "
        "hits INTEGER, doubles INTEGER, triples INTEGER, hr INTEGER, rbi INTEGER, sb INTEGER, "
        "bb INTEGER, so INTEGER, avg VARCHAR, obp VARCHAR, slg VARCHAR, ops VARCHAR, "
        "source VARCHAR, ingested_at TIMESTAMP)"
    )


def _season(conn: duckdb.DuckDBPyConnection, pid: int, season: int, pa: int, ops: str) -> None:
    conn.execute(
        "INSERT INTO player_season_batting (player_id, season, pa, ops, ingested_at) "
        "VALUES (?,?,?,?,?)",
        [pid, season, pa, ops, _NOW],
    )


def _angle(
    key: str, *, subject: str = "X", subject_id: int | None = None, stats: list[Stat] | None = None
) -> StoryAngle:
    return StoryAngle(
        key=key,
        subject=subject,
        title="T",
        headline="h",
        thesis="t",
        direction="up",
        effect=1,
        reliability=0.5,
        interest=10.0,
        confidence="moderate",
        as_of=date(2026, 6, 20),
        subject_id=subject_id,
        stats=stats or [],
    )


def test_down_year_is_boosted_over_career_norm(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A player far off his career OPS is more surprising than one at his norm."""
    _psb(padres_db)
    # Machado: ~.810 career, .609 now (a real collapse)
    for s, pa, ops in [(2023, 600, ".800"), (2024, 640, ".810"), (2025, 678, ".820")]:
        _season(padres_db, 1, s, pa, ops)
    _season(padres_db, 1, 2026, 291, ".609")
    # A steady player: career .750, now .748
    for s, pa, ops in [(2023, 600, ".750"), (2024, 640, ".752"), (2025, 600, ".748")]:
        _season(padres_db, 2, s, pa, ops)
    _season(padres_db, 2, 2026, 290, ".748")

    down = subject_surprise(padres_db, _angle("player_luck", subject_id=1), 2026)
    steady = subject_surprise(padres_db, _angle("player_luck", subject_id=2), 2026)
    assert down.multiplier > steady.multiplier
    assert down.multiplier >= 1.4  # ~200 OPS pts off -> strong boost
    assert "below his" in down.note
    assert "career norm" in steady.note


def test_neutral_without_history(padres_db: duckdb.DuckDBPyConnection) -> None:
    """No player_season_batting rows → neutral multiplier, never a crash."""
    _psb(padres_db)
    s = subject_surprise(padres_db, _angle("player_luck", subject_id=999), 2026)
    assert s.multiplier == 1.0 and s.note == "no baseline"


def test_team_surprise_standardizes_vs_league(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Team luck gap is scored against the league distribution of gaps."""
    for i in range(20):
        padres_db.execute(
            "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa, bip, ba, "
            "est_ba, slg, est_slg, woba, est_woba, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [i, f"P{i}", 2026, 300, 270, 0.25, 0.25, 0.4, 0.4, 0.320, 0.322, _NOW],
        )
    team = _angle(
        "team_luck",
        stats=[
            Stat("team_woba", 0.294, "woba", "t", 0),
            Stat("team_xwoba", 0.314, "woba", "t", 0),
        ],
    )
    s = subject_surprise(padres_db, team, 2026)
    assert s.multiplier > 1.0  # a -20 gap is an outlier vs a league clustered near 0
    assert "SD" in s.note


def test_novelty_downweights_recent_subject(padres_db: duckdb.DuckDBPyConnection) -> None:
    padres_db.execute(
        "INSERT INTO stat_candidates (candidate_id, detector, subject, as_of, payload_kind, "
        "facts_json, provenance_json, coverage_window, claim_scope, novelty_score) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ["c1", "d", "Manny Machado", date(2026, 6, 18), "dataset", "{}", "[]", "2026", "2026", 1.0],
    )
    mult, note = novelty(
        padres_db, _angle("player_luck", subject="Manny Machado"), date(2026, 6, 20)
    )
    assert mult == 0.7 and note == "recently featured"
    mult2, _ = novelty(padres_db, _angle("player_luck", subject="Nobody Here"), date(2026, 6, 20))
    assert mult2 == 1.0
