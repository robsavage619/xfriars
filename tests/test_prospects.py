"""Tests for the farm-performance detector."""

from __future__ import annotations

from datetime import date

import duckdb

from padres_analytics.detect.prospects import FarmPerformanceDetector


def _conn(rows: list[tuple[str, str, int, int, str, str]]) -> duckdb.DuckDBPyConnection:
    """rows = (player_name, level, pa, hr, ops, avg)."""
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE milb_batting (
            player_id INTEGER, player_name VARCHAR, season INTEGER, affiliate_id INTEGER,
            affiliate VARCHAR, level VARCHAR, games INTEGER, pa INTEGER, ab INTEGER,
            runs INTEGER, hits INTEGER, doubles INTEGER, triples INTEGER, hr INTEGER,
            rbi INTEGER, sb INTEGER, bb INTEGER, so INTEGER,
            avg VARCHAR, obp VARCHAR, slg VARCHAR, ops VARCHAR
        )
    """)
    for i, (name, level, pa, hr, ops, avg) in enumerate(rows):
        conn.execute(
            "INSERT INTO milb_batting (player_id, player_name, season, affiliate_id, level, "
            "pa, hr, ops, avg) VALUES (?, ?, 2026, ?, ?, ?, ?, ?, ?)",
            [i + 1, name, 900 + i, level, pa, hr, ops, avg],
        )
    return conn


def test_farm_performance_ranks_by_ops() -> None:
    rows = [
        ("Top Bat", "AA", 200, 15, "1.054", "0.301"),
        ("Second", "AAA", 220, 12, "0.962", "0.290"),
        ("Third", "High-A", 180, 10, "0.900", "0.275"),
        ("Fourth", "Single-A", 150, 8, "0.850", "0.260"),
    ]
    cands = FarmPerformanceDetector().run(_conn(rows), date(2026, 6, 16))
    assert len(cands) == 1
    f = cands[0].facts_json
    assert f["facts"]["leader"] == "Top Bat"
    assert f["facts"]["leader_ops"] == 1.054
    assert "raking at AA" in f["headline"]
    assert ".301 AVG" in f["headline"]
    assert cands[0].category == "prospects"
    # ranked by OPS desc
    assert [r[0].split(" (")[0] for r in f["rows"]] == ["Top Bat", "Second", "Third", "Fourth"]


def test_farm_performance_filters_small_samples() -> None:
    # All below the PA floor → no card
    rows = [(f"P{i}", "AA", 40, 5, "1.100", "0.350") for i in range(6)]
    assert FarmPerformanceDetector().run(_conn(rows), date(2026, 6, 16)) == []


def test_farm_performance_empty_without_data() -> None:
    assert FarmPerformanceDetector().run(_conn([]), date(2026, 6, 16)) == []
