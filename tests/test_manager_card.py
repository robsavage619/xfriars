"""Tests for the manager-case angle, its reconcile gate, and the verdict renderer."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from padres_analytics.detect.angles import detect_manager_case, discover
from padres_analytics.detect.reconcile import reconcile, verify_angle
from padres_analytics.render.manager_card import audit_manager, compose_manager, render_manager_card

if TYPE_CHECKING:
    from pathlib import Path

    import duckdb

_NOW = datetime(2026, 6, 20, 0, 0, 0)
_AS_OF = date(2026, 6, 20)
_PAD = 135


def _aux(c: duckdb.DuckDBPyConnection) -> None:
    """Create the ingest-owned tables the detector reads (not part of initialize)."""
    c.execute(
        "CREATE TABLE IF NOT EXISTS team_rosters (player_id INTEGER, player_name VARCHAR, "
        "status VARCHAR)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS player_season_pitching (player_id INTEGER, "
        "player_name VARCHAR, season INTEGER, team_id INTEGER, ip VARCHAR, era DOUBLE, "
        "saves INTEGER, source VARCHAR, ingested_at TIMESTAMP)"
    )


def _expected(
    c: duckdb.DuckDBPyConnection, pid: int, name: str, pa: int, w: float, x: float
) -> None:
    c.execute(
        "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa, bip, ba, est_ba, "
        "slg, est_slg, woba, est_woba, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [pid, name, 2026, pa, pa - 30, 0.25, 0.25, 0.40, 0.40, w, x, _NOW],
    )


def _game(c: duckdb.DuckDBPyConnection, pk: int, hs: int, as_: int) -> None:
    """A completed Padres home game with the given home/away scores."""
    c.execute(
        "INSERT INTO game_box (game_pk, game_date, home_team_id, away_team_id, home_score, "
        "away_score, innings, ingested_at) VALUES (?,?,?,?,?,?,?,?)",
        [pk, "2026-06-01", _PAD, 119, hs, as_, 9, _NOW],
    )


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    _aux(conn)
    # League anchor (non-Padres) so _context derives a league xwOBA.
    for i in range(6):
        _expected(conn, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    # Padres: a roster of regulars all under their expected wOBA (the drag).
    pad = [(1, "Machado, Manny"), (2, "Bogaerts, Xander"), (3, "Merrill, Jackson")]
    for pid, name in pad:
        conn.execute(
            "INSERT INTO team_rosters (player_id, player_name, status) VALUES (?,?,?)",
            [pid, name, "Active"],
        )
        _expected(conn, pid, name, 300, 0.285, 0.317)
    # Pitching staff (for the evidence line + a saves leader).
    for pid, name, ip, era, sv in (
        (10, "King, M", "85.0", 3.60, 0),
        (11, "Miller, M", "31.0", 0.87, 20),
    ):
        conn.execute(
            "INSERT INTO player_season_pitching (player_id, player_name, season, team_id, ip, era, "
            "saves, source, ingested_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [pid, name, 2026, _PAD, ip, era, sv, "t", _NOW],
        )
    # 24-game run ledger: 13-11, run diff negative (so actual tops Pythagorean),
    # with eight one-run wins and six one-run losses (the close-game record).
    pk = 0
    for _ in range(8):  # close wins
        _game(conn, pk := pk + 1, 4, 3)
    for _ in range(5):  # comfortable wins
        _game(conn, pk := pk + 1, 6, 2)
    for _ in range(6):  # close losses
        _game(conn, pk := pk + 1, 3, 4)
    for _ in range(5):  # blowout losses (inflate runs allowed)
        _game(conn, pk := pk + 1, 1, 7)


def test_detect_manager_case_fires_and_reconciles(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed(padres_db)
    angle = detect_manager_case(_ctx(padres_db))
    assert angle is not None
    assert angle.key == "manager_case"
    sm = {s.key: s.value for s in angle.stats}
    assert (sm["mgr_wins"], sm["mgr_losses"]) == (13, 11)
    assert sm["mgr_close_w"] == 8 and sm["mgr_close_l"] == 6
    assert sm["mgr_wins"] > sm["mgr_pyth"]  # outperforming the run margin
    assert sm["owed"] >= 8  # the bats are the drag
    # Reconcile against an independent re-derivation from source.
    assert reconcile(padres_db, angle) == []
    verify_angle(padres_db, angle)  # must not raise


def test_manager_case_in_discovery(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed(padres_db)
    keys = {a.key for a in discover(padres_db, 2026, as_of=_AS_OF)}
    assert "manager_case" in keys


def test_manager_case_renders_and_audits(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _seed(padres_db)
    angle = detect_manager_case(_ctx(padres_db))
    assert angle is not None
    svg = compose_manager(angle)
    assert audit_manager(angle, svg) == []  # every marquee number is drawn
    out = render_manager_card(angle, tmp_path, "mgr")
    assert out.exists() and out.stat().st_size > 0


def test_manager_case_silent_without_games(padres_db: duckdb.DuckDBPyConnection) -> None:
    # Bats underwater but no game ledger -> no Pythagorean -> no exoneration story.
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    padres_db.execute(
        "INSERT INTO team_rosters (player_id, player_name, status) "
        "VALUES (1,'Machado, Manny','Active')"
    )
    _expected(padres_db, 1, "Machado, Manny", 300, 0.285, 0.317)
    assert detect_manager_case(_ctx(padres_db)) is None


def _ctx(conn: duckdb.DuckDBPyConnection):
    from padres_analytics.detect.angles import _context

    ctx = _context(conn, 2026, _AS_OF)
    assert ctx is not None
    return ctx
