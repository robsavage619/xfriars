"""Tests for schema-driven metric discovery."""

from __future__ import annotations

import duckdb

from padres_analytics.detect.discovery import discover_metrics


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE statcast_batter_percentile_ranks (
            player_id INTEGER, year INTEGER,
            xwoba DOUBLE, hard_hit_percent DOUBLE, chase_percent DOUBLE, bat_speed DOUBLE
        )
    """)
    conn.execute("INSERT INTO statcast_batter_percentile_ranks VALUES (1, 2026, 91, 88, 30, 95)")
    conn.execute("""
        CREATE TABLE statcast_batting_expected (
            player_id INTEGER, year INTEGER, pa INTEGER,
            woba DOUBLE, est_woba DOUBLE, ba DOUBLE, est_ba DOUBLE
        )
    """)
    conn.execute(
        "INSERT INTO statcast_batting_expected VALUES (1, 2026, 200, 0.300, 0.360, 0.250, 0.280)"
    )
    return conn


def test_discovers_one_metric_per_percentile_column() -> None:
    specs = discover_metrics(_conn())
    ids = {s.id for s in specs}
    # Every numeric percentile column became a metric (year/player_id excluded)
    assert "pctl_B_xwoba" in ids
    assert "pctl_B_hard_hit_percent" in ids
    assert "pctl_B_chase_percent" in ids
    assert "pctl_B_bat_speed" in ids


def test_percentile_metrics_are_pre_oriented_and_use_percentile_lens() -> None:
    specs = {s.id: s for s in discover_metrics(_conn())}
    m = specs["pctl_B_hard_hit_percent"]
    assert m.direction == "higher"  # pre-oriented, no guessing
    assert "percentile_elite" in m.lenses
    assert m.label == "Hard-Hit %"


def test_expected_gap_metric_auto_paired() -> None:
    specs = {s.id: s for s in discover_metrics(_conn())}
    assert "gap_woba" in specs
    gap = specs["gap_woba"]
    assert gap.derived_expr == "est_woba - woba"
    assert gap.metric_type == "differential"


def test_id_and_meta_columns_never_become_metrics() -> None:
    ids = {s.id for s in discover_metrics(_conn())}
    assert "pctl_B_player_id" not in ids
    assert "pctl_B_year" not in ids


def test_unknown_column_gets_titlecased_label() -> None:
    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE statcast_batter_percentile_ranks "
        "(player_id INTEGER, year INTEGER, some_new_metric DOUBLE)"
    )
    conn.execute("INSERT INTO statcast_batter_percentile_ranks VALUES (1, 2026, 80)")
    specs = {s.id: s for s in discover_metrics(conn)}
    assert specs["pctl_B_some_new_metric"].label == "Some New Metric"


def test_no_tables_returns_empty() -> None:
    assert discover_metrics(duckdb.connect()) == []
