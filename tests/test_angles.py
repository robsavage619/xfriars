"""Tests for the story-discovery engine + audited infographic renderer."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from padres_analytics.detect.angles import (
    REGRESSION_PA_PRIOR,
    audit_angle,
    confidence_tier,
    discover,
    regress,
    reliability,
)
from padres_analytics.render.story_infographic import audit_rendered, compose

if TYPE_CHECKING:
    import duckdb

_NOW = datetime(2026, 6, 20, 0, 0, 0)


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


def _ev(c: duckdb.DuckDBPyConnection, pid: int, name: str, att: int, ev: float) -> None:
    c.execute(
        "INSERT INTO statcast_batter_exitvelo_barrels (player_id, player_name, year, attempts, "
        "avg_hit_speed, max_hit_speed, barrels, brl_percent, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [pid, name, 2026, att, ev, ev + 15, 12, 9.0, _NOW],
    )


def _pct(c: duckdb.DuckDBPyConnection, pid: int, name: str, **vals: float) -> None:
    cols = ["player_id", "player_name", "year", *vals.keys(), "ingested_at"]
    c.execute(
        f"INSERT INTO statcast_batter_percentile_ranks ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})",
        [pid, name, 2026, *vals.values(), _NOW],
    )


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    _aux(conn)
    for i in range(6):
        _expected(conn, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    # Padres: an unlucky core + a free-swinger + an elite-barrel bat.
    pad = [
        (1, "Machado, Manny", 296, 0.270, 0.330),  # big individual under-performer
        (2, "Bogaerts, Xander", 250, 0.285, 0.318),
        (3, "Merrill, Jackson", 150, 0.300, 0.312),
    ]
    for pid, name, pa, w, x in pad:
        conn.execute("INSERT INTO team_rosters VALUES (?, ?)", [pid, name])
        _expected(conn, pid, name, pa, w, x)
        _ev(conn, pid, name, 150, 90.0)
        for gd, (ab, h) in zip(("2026-06-15", "2026-06-17"), ((10, 1), (12, 5)), strict=True):
            conn.execute(
                "INSERT INTO player_game_batting (player_id, player_name, season, game_date, "
                "game_pk, ab, hits, bb, hbp, source, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [pid, name, 2026, gd, 1, ab, h, 0, 0, "t", _NOW],
            )
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
    )  # 8th pct chase -> approach outlier
    _pct(
        conn,
        3,
        "Merrill, Jackson",
        xwoba=60,
        hard_hit_percent=92,
        brl_percent=95,
        chase_percent=40,
        k_percent=32,
    )  # 95th pct barrels -> power outlier


def test_reliability_and_regression() -> None:
    """The 220-PA prior weights observation and prior equally at the break-even."""
    assert reliability(REGRESSION_PA_PRIOR) == 0.5
    assert regress(0.400, REGRESSION_PA_PRIOR, 0.320) == (0.400 + 0.320) / 2
    assert confidence_tier(0.92) == "high"
    assert confidence_tier(0.50) == "moderate"
    assert confidence_tier(0.20) == "low"


def test_discover_surfaces_ranked_angles(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Multiple lenses fire, are ranked by interest, and carry direction + confidence."""
    _seed(padres_db)
    angles = discover(padres_db, 2026, as_of=date(2026, 6, 20))
    keys = {a.key for a in angles}
    assert "team_luck" in keys
    assert "player_luck" in keys  # Machado under-performing
    assert "approach_outlier" in keys  # Bogaerts 8th-pct chase
    assert "power_outlier" in keys  # Merrill 95th-pct barrels
    # sorted by interest descending
    assert [a.interest for a in angles] == sorted((a.interest for a in angles), reverse=True)
    # direction-aware: the under-performer is "owed up"
    pl = next(a for a in angles if a.key == "player_luck")
    assert pl.direction == "up"
    assert pl.title == "BETTER THAN THE LINE"


def test_no_story_below_threshold(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A lineup performing at its expected level yields no luck story."""
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    # Padres hitting exactly their expected line — nothing owed.
    for pid in (1, 2, 3):
        padres_db.execute("INSERT INTO team_rosters VALUES (?, ?)", [pid, f"P, {pid}"])
        _expected(padres_db, pid, f"P, {pid}", 300, 0.321, 0.321)
    angles = discover(padres_db, 2026, as_of=date(2026, 6, 20))
    assert not any(a.key in ("team_luck", "player_luck") for a in angles)


def test_render_audit_passes_for_real_angle(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Every shown stat and headline number lands on the rendered card."""
    _seed(padres_db)
    angles = discover(padres_db, 2026, as_of=date(2026, 6, 20))
    for a in angles:
        assert not audit_angle(a), f"{a.key} self-audit: {audit_angle(a)}"
        svg = compose(a)
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        problems = audit_rendered(a, svg)
        assert not problems, f"{a.key} render audit: {problems}"


def test_audit_catches_unbacked_headline_number(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A headline number not backed by any stat is flagged (the credibility guard)."""
    _seed(padres_db)
    angle = next(
        a for a in discover(padres_db, 2026, as_of=date(2026, 6, 20)) if a.key == "team_luck"
    )
    tampered = type(angle)(**{**angle.__dict__, "headline": "The bats are 999 points unlucky"})
    assert any("999" in v for v in audit_angle(tampered))


def test_render_audit_catches_dropped_stat(padres_db: duckdb.DuckDBPyConnection) -> None:
    """If a shown stat never reaches the SVG, the render audit flags it."""
    _seed(padres_db)
    angle = next(
        a for a in discover(padres_db, 2026, as_of=date(2026, 6, 20)) if a.key == "team_luck"
    )
    broken = type(angle)(**{**angle.__dict__, "panels": []})  # nothing drawn
    assert audit_rendered(broken, compose(broken))
