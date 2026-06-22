"""Tests for the data-coverage preflight."""

from __future__ import annotations

import duckdb

from padres_analytics.storage.coverage import (
    APPROACH_TREND,
    CONTRACT,
    SEASON_LUCK,
    SWING_PATH_CHANGE,
    CoverageReport,
    DomainSpec,
    _classify,
    audit,
    can_support,
)


def _spec(**kw: object) -> DomainSpec:
    base: dict[str, object] = {
        "domain": "D",
        "table": "t",
        "granularity": "season-agg",
        "needs_current": True,
        "needs_baseline": False,
        "min_players": 100,
        "supports": (SEASON_LUCK,),
    }
    base.update(kw)
    return DomainSpec(**base)  # type: ignore[arg-type]


def test_classify_empty() -> None:
    status, _ = _classify(_spec(), 2026, rows=0, seasons=(), n_players=0)
    assert status == "EMPTY"


def test_classify_stale_when_current_season_missing() -> None:
    status, reason = _classify(_spec(), 2026, rows=10, seasons=(2024,), n_players=200)
    assert status == "STALE"
    assert "2024" in reason and "2026" in reason


def test_classify_partial_when_too_few_players() -> None:
    status, reason = _classify(_spec(), 2026, rows=10, seasons=(2026,), n_players=1)
    assert status == "PARTIAL"
    assert "1 players" in reason


def test_classify_ok_state_only_flags_missing_baseline() -> None:
    spec = _spec(needs_baseline=True)
    status, reason = _classify(spec, 2026, rows=500, seasons=(2026,), n_players=500)
    assert status == "OK"
    assert "no prior-year baseline" in reason


def test_audit_runs_against_real_schema(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Empty (freshly initialized) DB: one report per domain, nothing supported."""
    reports = audit(padres_db)
    assert len(reports) == len(CONTRACT)
    # Every domain is empty, so no capability is backed.
    assert all(not r.supports for r in reports)
    ok, _ = can_support(reports, SEASON_LUCK)
    assert ok is False


def test_can_support_gates_change_claims() -> None:
    """A change claim with no prior-season baseline is blocked, even if OK."""
    reports = [
        CoverageReport(
            domain="Pitch-level",
            table="statcast_batter_pitches",
            granularity="pitch",
            rows=2000,
            seasons=(2026,),  # current only — no baseline
            latest_date=None,
            n_players=200,
            status="OK",
            supports=(),
            blocks=(APPROACH_TREND,),
            reason="current only",
        )
    ]
    ok, why = can_support(reports, APPROACH_TREND)
    assert ok is False
    assert "blocked" in why


def test_can_support_unknown_capability() -> None:
    ok, why = can_support([], SWING_PATH_CHANGE)
    assert ok is False
    assert "no domain" in why
