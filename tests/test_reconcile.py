"""Tests for source reconciliation + the hardened render audit."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import TYPE_CHECKING

from padres_analytics.detect.angles import Stat, StoryAngle, discover
from padres_analytics.detect.reconcile import ReconcileError, reconcile, verify_angle
from padres_analytics.render.story_infographic import audit_rendered, compose

if TYPE_CHECKING:
    import duckdb

_NOW = datetime(2026, 6, 20)


def _aux(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS team_rosters (player_id INTEGER, player_name VARCHAR)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS player_game_batting (player_id INTEGER, player_name VARCHAR, "
        "season INTEGER, game_date DATE, game_pk INTEGER, ab INTEGER, hits INTEGER, bb INTEGER, "
        "hbp INTEGER, source VARCHAR, ingested_at TIMESTAMP)"
    )


def _expected(
    c: duckdb.DuckDBPyConnection, pid: int, name: str, pa: int, w: float, x: float
) -> None:
    c.execute(
        "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa, bip, ba, est_ba, "
        "slg, est_slg, woba, est_woba, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [pid, name, 2026, pa, pa - 30, 0.25, 0.25, 0.40, 0.40, w, x, _NOW],
    )


def _pct(c: duckdb.DuckDBPyConnection, pid: int, name: str, **vals: float) -> None:
    cols = ["player_id", "player_name", "year", *vals.keys(), "ingested_at"]
    c.execute(
        f"INSERT INTO statcast_batter_percentile_ranks ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})",
        [pid, name, 2026, *vals.values(), _NOW],
    )


def _barrels(c: duckdb.DuckDBPyConnection, pid: int, name: str, attempts: int, brl: float) -> None:
    c.execute(
        "INSERT INTO statcast_batter_exitvelo_barrels (player_id, player_name, year, attempts, "
        "avg_hit_speed, max_hit_speed, barrels, brl_percent, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [pid, name, 2026, attempts, 90.0, 105.0, 20, brl, _NOW],
    )


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    _aux(conn)
    for i in range(8):
        _expected(conn, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
        _barrels(conn, 900 + i, f"League, G{i}", 200, 6.0)
    pad = [
        (1, "Machado, Manny", 296, 0.270, 0.330),
        (2, "Bogaerts, Xander", 250, 0.285, 0.318),
        (3, "Merrill, Jackson", 150, 0.300, 0.312),
    ]
    for pid, name, pa, w, x in pad:
        conn.execute("INSERT INTO team_rosters VALUES (?, ?)", [pid, name])
        _expected(conn, pid, name, pa, w, x)
        _barrels(conn, pid, name, 150, 9.0)
    _pct(
        conn,
        1,
        "Machado, Manny",
        xwoba=70,
        hard_hit_percent=80,
        brl_percent=75,
        chase_percent=45,
        k_percent=44,
    )
    _pct(
        conn,
        2,
        "Bogaerts, Xander",
        xwoba=55,
        hard_hit_percent=30,
        brl_percent=40,
        chase_percent=8,
        k_percent=68,
    )
    _pct(
        conn,
        3,
        "Merrill, Jackson",
        xwoba=60,
        hard_hit_percent=92,
        brl_percent=95,
        chase_percent=40,
        k_percent=32,
    )


def test_real_angles_reconcile_clean(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed(padres_db)
    for a in discover(padres_db, 2026, as_of=date(2026, 6, 20)):
        assert reconcile(padres_db, a) == [], f"{a.key}: {reconcile(padres_db, a)}"


def test_tampered_team_number_is_caught(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed(padres_db)
    team = next(
        a for a in discover(padres_db, 2026, as_of=date(2026, 6, 20)) if a.key == "team_luck"
    )
    bad_stats = [replace(s, value=0.999) if s.key == "team_xwoba" else s for s in team.stats]
    tampered = replace(team, stats=bad_stats)
    problems = reconcile(padres_db, tampered)
    assert any("team_xwoba" in p for p in problems)
    try:
        verify_angle(padres_db, tampered)
        raise AssertionError("verify_angle should have raised")
    except ReconcileError:
        pass


def test_wrong_player_percentile_is_caught(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed(padres_db)
    appr = next(
        a for a in discover(padres_db, 2026, as_of=date(2026, 6, 20)) if a.key == "approach_outlier"
    )
    bad = replace(
        appr, stats=[replace(s, value=99) if s.key == "chase_pct" else s for s in appr.stats]
    )
    assert any("chase_pct" in p for p in reconcile(padres_db, bad))


def test_hardened_audit_rejects_within_number_false_match() -> None:
    """A token must appear as a whole number in TEXT, not inside a coordinate/larger number."""
    angle = StoryAngle(
        key="x",
        subject="s",
        title="T",
        headline="h",
        thesis="t",
        direction="up",
        effect=1,
        reliability=0.5,
        interest=1,
        confidence="moderate",
        as_of=date(2026, 6, 20),
        stats=[Stat("v", 5, "count", "five", 0, shown=True)],
    )
    # "5" only appears inside "0.05" and a coordinate-like "256" — must NOT pass.
    fake = '<svg><text x="256.0" y="10">rate 0.05</text></svg>'
    assert audit_rendered(angle, fake)
    real = '<svg><text x="256.0" y="10">total 5 hits</text></svg>'
    assert audit_rendered(angle, real) == []


def test_compose_audits_clean_for_discovered_angles(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed(padres_db)
    for a in discover(padres_db, 2026, as_of=date(2026, 6, 20)):
        assert audit_rendered(a, compose(a)) == [], a.key
