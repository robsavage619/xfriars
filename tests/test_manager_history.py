"""Tests for the manager-history angle, its reconcile gate, and the dumbbell renderer."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from padres_analytics.detect.angles import detect_manager_history, discover
from padres_analytics.detect.manager_history import COHORT
from padres_analytics.detect.reconcile import reconcile, verify_angle
from padres_analytics.render.manager_history_card import (
    audit_history,
    compose_history,
    render_manager_history_card,
)

if TYPE_CHECKING:
    from pathlib import Path

    import duckdb

_NOW = datetime(2026, 6, 20, 0, 0, 0)
_AS_OF = date(2026, 6, 20)
_PAD = 135


def _aux(c: duckdb.DuckDBPyConnection) -> None:
    c.execute(
        "CREATE TABLE IF NOT EXISTS team_rosters (player_id INTEGER, player_name VARCHAR, "
        "status VARCHAR)"
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
    c.execute(
        "INSERT INTO game_box (game_pk, game_date, home_team_id, away_team_id, home_score, "
        "away_score, innings, ingested_at) VALUES (?,?,?,?,?,?,?,?)",
        [pk, "2026-06-01", _PAD, 119, hs, as_, 9, _NOW],
    )


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    _aux(conn)
    for i in range(6):
        _expected(conn, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    conn.execute(
        "INSERT INTO team_rosters (player_id, player_name, status) "
        "VALUES (1,'Machado, Manny','Active')"
    )
    _expected(conn, 1, "Machado, Manny", 300, 0.285, 0.317)
    # 24-game ledger -> 13-11.
    pk = 0
    for _ in range(13):
        _game(conn, pk := pk + 1, 4, 3)
    for _ in range(11):
        _game(conn, pk := pk + 1, 2, 5)


def _ctx(conn: duckdb.DuckDBPyConnection):
    from padres_analytics.detect.angles import _context

    ctx = _context(conn, 2026, _AS_OF)
    assert ctx is not None
    return ctx


def test_detect_manager_history_fires_and_reconciles(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed(padres_db)
    angle = detect_manager_history(_ctx(padres_db))
    assert angle is not None
    assert angle.key == "manager_history"
    sm = {s.key: s.value for s in angle.stats}
    assert (sm["mgr_wins"], sm["mgr_losses"]) == (13, 11)
    data: dict = dict(angle.panels[0].data)
    rows = data["rows"]
    assert len(rows) == len(COHORT) + 1  # cohort + the Padres
    assert any(r["subject"] and r["wins"] == 13 for r in rows)
    assert reconcile(padres_db, angle) == []
    verify_angle(padres_db, angle)  # must not raise


def test_manager_history_reconcile_catches_cohort_drift(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    _seed(padres_db)
    angle = detect_manager_history(_ctx(padres_db))
    assert angle is not None
    # Tamper with a cited cohort row -> reconcile must flag the drift.
    data: dict = dict(angle.panels[0].data)
    rows = data["rows"]
    for r in rows:
        if not r["subject"]:
            r["wins"] = 999
            break
    assert any("cohort" in p for p in reconcile(padres_db, angle))


def test_manager_history_in_discovery(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed(padres_db)
    keys = {a.key for a in discover(padres_db, 2026, as_of=_AS_OF)}
    assert "manager_history" in keys


def test_manager_history_renders_and_audits(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _seed(padres_db)
    angle = detect_manager_history(_ctx(padres_db))
    assert angle is not None
    svg = compose_history(angle)
    assert audit_history(angle, svg) == []
    out = render_manager_history_card(angle, tmp_path, "mgr_hist")
    assert out.exists() and out.stat().st_size > 0
