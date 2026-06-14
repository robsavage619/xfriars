"""Unit tests for detect/conjunction.py and the P3 scope / conjunction layer."""

from __future__ import annotations

from typing import Literal

import duckdb
import pytest

from padres_analytics.detect.conjunction import (
    ScopeResult,
    evaluate_franchise_scope,
    find_conjunctions,
)
from padres_analytics.detect.lenses import LensResult, milestone_proximity_lens
from padres_analytics.detect.registry import MetricSpec
from padres_analytics.tweets.verify import check_scope_upgrade

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_metric(
    table: str = "statcast_batter_exitvelo_barrels",
    value_col: str = "brl_percent",
    filter_sql: str = "attempts >= 10",
    direction: Literal["higher", "lower"] = "higher",
    milestones: list[float] | None = None,
) -> MetricSpec:
    return MetricSpec(
        id="barrel_rate",
        label="Barrel %",
        table=table,
        value_col=value_col,
        filter_sql=filter_sql,
        direction=direction,
        value_format=".1f",
        unit="%",
        population="p",
        coverage="since_2015",
        milestones=milestones or [],
    )


def _conn_with_tables() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with synthetic Statcast + bwar tables."""
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE statcast_batter_exitvelo_barrels (
            player_id INTEGER, player_name TEXT, year INTEGER,
            attempts INTEGER, brl_percent DOUBLE
        )
    """)
    conn.execute("""
        CREATE SCHEMA hist;
        CREATE TABLE hist.bwar_player_seasons (
            mlb_id INTEGER, year_id INTEGER, team_id TEXT
        )
    """)
    return conn


def _seed_sdp(conn: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
    """Insert (player_id, player_name, year, brl_percent) + bwar SDP entry."""
    for pid, pname, yr, brl in rows:
        conn.execute(
            "INSERT INTO statcast_batter_exitvelo_barrels VALUES (?, ?, ?, 200, ?)",
            [pid, pname, yr, brl],
        )
        conn.execute(
            "INSERT INTO hist.bwar_player_seasons VALUES (?, ?, 'SDP')",
            [pid, yr],
        )


# ── evaluate_franchise_scope ──────────────────────────────────────────────────


def test_scope_franchise_record_no_prior_data() -> None:
    conn = _conn_with_tables()
    # Current player only — no prior SDP seasons
    _seed_sdp(conn, [(1001, "Fernando Tatis Jr.", 2024, 22.5)])

    metric = _make_metric()
    result = evaluate_franchise_scope(conn, metric, 1001, "Fernando Tatis Jr.", 22.5, 2024, "base")
    assert isinstance(result, ScopeResult)
    # No prior player → franchise record
    assert result.tier == "franchise_record"
    assert "Tatis Jr." in result.framing
    assert "22.5" in result.framing


def test_scope_franchise_record_beats_prior() -> None:
    conn = _conn_with_tables()
    # Tatis (2024) beats previous best by Hosmer (2019)
    _seed_sdp(
        conn,
        [
            (9999, "Eric Hosmer", 2019, 14.0),
            (1001, "Fernando Tatis Jr.", 2024, 22.5),
        ],
    )

    metric = _make_metric()
    result = evaluate_franchise_scope(conn, metric, 1001, "Fernando Tatis Jr.", 22.5, 2024, "base")
    assert result.tier == "franchise_record"
    assert "best Padre" in result.framing


def test_scope_first_since() -> None:
    conn = _conn_with_tables()
    # Tatis (2024) at 22.5, but Gonzalez (2010) had 24.0 — not a record
    _seed_sdp(
        conn,
        [
            (9888, "Adrian Gonzalez", 2010, 24.0),
            (1001, "Fernando Tatis Jr.", 2024, 22.5),
        ],
    )

    metric = _make_metric()
    result = evaluate_franchise_scope(conn, metric, 1001, "Fernando Tatis Jr.", 22.5, 2024, "base")
    assert result.tier == "first_since"
    assert "Gonzalez" in result.framing
    assert "2010" in result.framing


def test_scope_graceful_fallback_on_error() -> None:
    """When bwar table is missing, returns season_best without raising."""
    conn = duckdb.connect()
    # No tables at all — should not raise
    metric = _make_metric()
    result = evaluate_franchise_scope(conn, metric, 1001, "Player", 18.0, 2024, "base fallback")
    assert result.tier == "season_best"
    assert result.framing == "base fallback"


# ── find_conjunctions ─────────────────────────────────────────────────────────


def _make_hit(player_id: int, player_name: str, metric_id: str, rarity: float):
    """Build a minimal _Hit-like object for conjunction tests."""
    from padres_analytics.detect.registry import MetricSpec
    from padres_analytics.detect.scanner import _Hit

    metric = MetricSpec(
        id=metric_id,
        label=metric_id.replace("_", " ").title(),
        table="t",
        value_col="v",
        population="p",
        coverage="since_2015",
    )
    lr = LensResult(
        rarity=rarity, framing=f"{player_name} lens", claim_scope="since_2015", lens="rank"
    )
    return _Hit(
        lens_result=lr,
        metric=metric,
        player_id=player_id,
        player_name=player_name,
        focal_value=1.0,
        rank=1,
        population_size=100,
        leaderboard=[],
        resolved_table="t",
        metric_year=2024,
    )


def test_find_conjunctions_two_metrics_same_player() -> None:
    hits = [
        _make_hit(1001, "Tatis Jr.", "barrel_rate", 0.90),
        _make_hit(1001, "Tatis Jr.", "sprint_speed", 0.85),
    ]
    groups = find_conjunctions(hits)
    assert len(groups) == 1
    g = groups[0]
    assert g.player_id == 1001
    assert len(g.metric_ids) == 2
    assert g.combined_rarity == pytest.approx((0.90 * 0.85) ** 0.5, rel=1e-3)


def test_find_conjunctions_different_players_not_grouped() -> None:
    hits = [
        _make_hit(1001, "Tatis Jr.", "barrel_rate", 0.90),
        _make_hit(2002, "Machado", "sprint_speed", 0.85),
    ]
    groups = find_conjunctions(hits)
    assert groups == []


def test_find_conjunctions_single_metric_not_grouped() -> None:
    hits = [
        _make_hit(1001, "Tatis Jr.", "barrel_rate", 0.90),
        _make_hit(1001, "Tatis Jr.", "barrel_rate", 0.88),  # same metric, different lens
    ]
    groups = find_conjunctions(hits)
    assert groups == []


def test_find_conjunctions_sorted_by_combined_rarity() -> None:
    hits = [
        _make_hit(1001, "Player A", "barrel_rate", 0.95),
        _make_hit(1001, "Player A", "sprint_speed", 0.90),
        _make_hit(2002, "Player B", "barrel_rate", 0.82),
        _make_hit(2002, "Player B", "sprint_speed", 0.80),
    ]
    groups = find_conjunctions(hits)
    assert len(groups) == 2
    assert groups[0].player_id == 1001  # higher combined rarity first


# ── milestone_proximity_lens ──────────────────────────────────────────────────


def test_milestone_proximity_fires_within_threshold() -> None:
    lr = milestone_proximity_lens(
        focal_value=18.5,
        milestone=20.0,
        metric_label="Barrel %",
        player_name="Tatis Jr.",
        value_format=".1f",
        unit="%",
        claim_scope="since_2015",
    )
    assert lr is not None
    assert lr.lens == "milestone_proximity"
    assert "18.5" in lr.framing
    assert "20.0" in lr.framing
    assert 0.80 <= lr.rarity <= 0.95


def test_milestone_proximity_suppressed_beyond_threshold() -> None:
    lr = milestone_proximity_lens(
        focal_value=10.0,  # 50% away from 20.0 — way outside 10%
        milestone=20.0,
        metric_label="Barrel %",
        player_name="Player",
        value_format=".1f",
        unit="%",
        claim_scope="since_2015",
    )
    assert lr is None


def test_milestone_proximity_suppressed_past_milestone() -> None:
    lr = milestone_proximity_lens(
        focal_value=22.0,  # already past 20.0
        milestone=20.0,
        metric_label="Barrel %",
        player_name="Player",
        value_format=".1f",
        unit="%",
        claim_scope="since_2015",
    )
    assert lr is None


# ── check_scope_upgrade ───────────────────────────────────────────────────────


def test_scope_upgrade_statcast_to_alltime_flagged() -> None:
    violations = check_scope_upgrade(
        framing="Fernando Tatis Jr. is the best Padre in the Statcast era in Barrel %",
        caption="Tatis has the best barrel rate ever in Padres history",
    )
    assert len(violations) > 0
    assert any("ever" in v.lower() or "all-time" in v.lower() for v in violations)


def test_scope_upgrade_no_violation() -> None:
    violations = check_scope_upgrade(
        framing="Fernando Tatis Jr. is the best Padre in the Statcast era in Barrel %",
        caption="Tatis leads the Padres in barrel rate this season — top of the Statcast era",
    )
    assert violations == []


def test_scope_upgrade_season_to_statcast_era_flagged() -> None:
    violations = check_scope_upgrade(
        framing="Player A leads MLB this season",
        caption="Nobody has done this in the Statcast era",
    )
    assert len(violations) > 0


def test_scope_upgrade_empty_framing_no_violations() -> None:
    violations = check_scope_upgrade(framing="", caption="Best ever in franchise history")
    assert violations == []
