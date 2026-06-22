"""Phase 2 tests: leaderboard detector, Path A verification, ammo, ingest_runs."""

from __future__ import annotations

import json
from datetime import date

import duckdb
import pytest

from padres_analytics.ingest.runs import last_complete_run, record_run
from padres_analytics.tweets.ammo import search_ammo
from padres_analytics.tweets.verify import VerificationError, verify_path_a

# ── ingest_runs ───────────────────────────────────────────────────────────────


def test_record_run_complete(padres_db: duckdb.DuckDBPyConnection) -> None:
    with record_run(padres_db, "test-source", note="unit test") as run:
        run["rows_written"] = 42

    row = padres_db.execute(
        "SELECT complete, rows_written, note FROM ingest_runs WHERE source = ?",
        ["test-source"],
    ).fetchone()
    assert row is not None
    assert row[0] is True
    assert row[1] == 42
    assert row[2] == "unit test"


def test_record_run_failure_marks_incomplete(padres_db: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError), record_run(padres_db, "failing-source"):
        raise ValueError("simulated failure")

    row = padres_db.execute(
        "SELECT complete FROM ingest_runs WHERE source = ?",
        ["failing-source"],
    ).fetchone()
    assert row is not None
    assert row[0] is False


def test_last_complete_run_none_when_empty(padres_db: duckdb.DuckDBPyConnection) -> None:
    result = last_complete_run(padres_db, "nonexistent-source")
    assert result is None


def test_last_complete_run_returns_timestamp(padres_db: duckdb.DuckDBPyConnection) -> None:
    with record_run(padres_db, "ts-source"):
        pass
    result = last_complete_run(padres_db, "ts-source")
    assert result is not None


# ── Path A verification ───────────────────────────────────────────────────────


def _seed_mlb_leaders(
    conn: duckdb.DuckDBPyConnection,
    stat_type: str = "homeRuns",
    season: int = 2026,
    padre_rank: int = 5,
    padre_value: str = "15",
) -> None:
    """Seed mlb_leaders with a Padre at the given rank."""
    # Insert 10 rows; make rank padre_rank belong to player_id=660271 (Tatis)
    for rank in range(1, 11):
        is_padre = rank == padre_rank
        conn.execute(
            """
            INSERT INTO mlb_leaders
                (season, stat_group, stat_type, rank, player_id, player_name,
                 team_id, team_abbr, value)
            VALUES (?, 'hitting', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                season,
                stat_type,
                rank,
                660271 if is_padre else rank * 100,
                "Fernando Tatis Jr." if is_padre else f"Player {rank}",
                135 if is_padre else rank * 10,
                "SD" if is_padre else f"T{rank}",
                padre_value if is_padre else str(rank * 3),
            ],
        )


def test_path_a_passes_exact_match(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed_mlb_leaders(padres_db, padre_rank=5, padre_value="15")
    facts = {
        "stat_type": "homeRuns",
        "season": 2026,
        "padre_rank": 5,
        "padre_value_raw": "15",
    }
    result = verify_path_a(padres_db, "test-cid", facts)
    assert result["passed"] is True
    assert result["path"] == "A"
    assert result["single_source"] is False


def test_path_a_mismatch_raises(padres_db: duckdb.DuckDBPyConnection) -> None:
    # facts_json says 15, mlb_leaders says 20 — counting stat, tolerance=0
    _seed_mlb_leaders(padres_db, padre_rank=5, padre_value="20")
    facts = {
        "stat_type": "homeRuns",
        "season": 2026,
        "padre_rank": 5,
        "padre_value_raw": "15",  # deliberately wrong
    }
    with pytest.raises(VerificationError, match="MISMATCH"):
        verify_path_a(padres_db, "test-cid", facts)


def test_path_a_rate_stat_within_tolerance(padres_db: duckdb.DuckDBPyConnection) -> None:
    # AVG: tolerance = 0.001; diff 0.0005 should pass
    _seed_mlb_leaders(padres_db, stat_type="battingAverage", padre_value="0.3005")
    facts = {
        "stat_type": "battingAverage",
        "season": 2026,
        "padre_rank": 5,
        "padre_value_raw": "0.3010",  # diff = 0.0005 < 0.001
    }
    result = verify_path_a(padres_db, "test-cid", facts)
    assert result["passed"] is True


def test_path_a_rate_stat_exceeds_tolerance(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed_mlb_leaders(padres_db, stat_type="battingAverage", padre_value="0.300")
    facts = {
        "stat_type": "battingAverage",
        "season": 2026,
        "padre_rank": 5,
        "padre_value_raw": "0.320",  # diff = 0.020 >> 0.001
    }
    with pytest.raises(VerificationError, match="MISMATCH"):
        verify_path_a(padres_db, "test-cid", facts)


def test_path_a_falls_back_for_non_leaderboard(padres_db: duckdb.DuckDBPyConnection) -> None:
    facts: dict = {"wins": 14, "losses": 12}  # on_this_day candidate
    result = verify_path_a(padres_db, "test-cid", facts)
    assert result["path"] == "B"
    assert result["single_source"] is True


# ── ammo ──────────────────────────────────────────────────────────────────────


def _seed_candidates(conn: duckdb.DuckDBPyConnection) -> None:
    """Seed stat_candidates with known fixture rows."""
    rows = [
        (
            "cid001",
            "on_this_day",
            "SDP|Jun 9",
            date(2026, 6, 9),
            0.85,
            json.dumps({"headline": "Padres are 14-12 on Jun 9 since 1990"}),
        ),
        (
            "cid002",
            "leaderboard",
            "SDP|2026|stolenBases",
            date(2026, 6, 8),
            0.72,
            json.dumps({"headline": "Tatis ranks #8 in MLB SB (15) in 2026"}),
        ),
        (
            "cid003",
            "on_this_day",
            "SDP|Jun 1",
            date(2026, 6, 1),
            0.60,
            json.dumps({"headline": "Padres are 9-11 on Jun 1 since 1990"}),
        ),
    ]
    for cid, detector, subject, as_of, score, facts in rows:
        conn.execute(
            """
            INSERT INTO stat_candidates
                (candidate_id, detector, subject, as_of, category,
                 payload_kind, facts_json, provenance_json,
                 coverage_window, claim_scope, novelty_score)
            VALUES (?, ?, ?, ?, 'historical', 'table', ?, '[]',
                    '1990-2024', 'since_1990', ?)
            ON CONFLICT DO NOTHING
            """,
            [cid, detector, subject, as_of, facts, score],
        )


def test_ammo_returns_results(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed_candidates(padres_db)
    results = search_ammo(padres_db, "tatis", as_of=date(2026, 6, 9))
    assert len(results) >= 1
    assert any("Tatis" in r["headline"] for r in results)


def test_ammo_no_results_for_unknown(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed_candidates(padres_db)
    results = search_ammo(padres_db, "xyzzy_not_a_player")
    assert results == []


def test_ammo_respects_limit(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed_candidates(padres_db)
    results = search_ammo(padres_db, "padres", limit=2)
    assert len(results) <= 2


def test_ammo_sorted_by_score(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed_candidates(padres_db)
    results = search_ammo(padres_db, "padres", as_of=date(2026, 6, 9))
    scores = [r["ammo_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


# ── leaderboard detector (offline — queries mlb_leaders in padres_db) ─────────


def _seed_leaderboard_with_padre(
    conn: duckdb.DuckDBPyConnection,
    stat_type: str = "stolenBases",
    season: int = 2026,
    padre_rank: int = 8,
    padre_value: str = "15",
    padre_name: str = "Fernando Tatis Jr.",
) -> None:
    # Mark the ingest run as complete so the stale guard passes
    with record_run(conn, f"mlb-stats-api/leaders/{season}"):
        pass

    for rank in range(1, 11):
        is_padre = rank == padre_rank
        conn.execute(
            """
            INSERT INTO mlb_leaders
                (season, stat_group, stat_type, rank, player_id, player_name,
                 team_id, team_abbr, value)
            VALUES (?, 'hitting', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                season,
                stat_type,
                rank,
                660271 if is_padre else rank * 100,
                padre_name if is_padre else f"Player {rank}",
                135 if is_padre else rank * 10,
                "SD" if is_padre else f"T{rank}",
                padre_value if is_padre else str(rank * 2),
            ],
        )


def test_leaderboard_candidate_built(padres_db: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.leaderboards import _build_leaderboard_candidate

    _seed_leaderboard_with_padre(padres_db)
    cand = _build_leaderboard_candidate(padres_db, "stolenBases", 2026, date(2026, 6, 9))
    assert cand is not None
    assert cand.detector == "leaderboard"
    assert cand.payload_kind == "dataset"
    f = cand.facts_json["facts"]
    assert f["padre_rank"] == 8
    assert f["padre_value"] == "15"
    assert f["padre_name"] == "Fernando Tatis Jr."
    # Padre row should be highlighted on the bar card
    assert any(m["label"] == "Padres" for m in cand.facts_json["highlight"])


def test_leaderboard_no_padre_returns_none(padres_db: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.leaderboards import _build_leaderboard_candidate

    # Seed with no Padre (all team_id != 135)
    with record_run(padres_db, "mlb-stats-api/leaders/2026"):
        pass
    for rank in range(1, 6):
        padres_db.execute(
            """
            INSERT INTO mlb_leaders
                (season, stat_group, stat_type, rank, player_id, player_name,
                 team_id, team_abbr, value)
            VALUES (2026, 'hitting', 'homeRuns', ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [rank, rank * 100, f"Player {rank}", rank * 10, f"T{rank}", str(rank * 5)],
        )
    cand = _build_leaderboard_candidate(padres_db, "homeRuns", 2026, date(2026, 6, 9))
    assert cand is None


def test_leaderboard_stale_guard(padres_db: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.leaderboards import LeaderboardDetector

    # No ingest run recorded → stale → emits 0 candidates
    detector = LeaderboardDetector()
    candidates = detector.run(padres_db, date(2026, 6, 9))
    assert candidates == []


# ── golden: known leaderboard value ──────────────────────────────────────────


def test_leaderboard_golden_tatis_sb(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Golden: Tatis Jr. with 15 SB ranks #8. Facts must reflect that exactly."""
    from padres_analytics.detect.leaderboards import _build_leaderboard_candidate

    _seed_leaderboard_with_padre(
        padres_db,
        stat_type="stolenBases",
        season=2026,
        padre_rank=8,
        padre_value="15",
        padre_name="Fernando Tatis Jr.",
    )
    cand = _build_leaderboard_candidate(padres_db, "stolenBases", 2026, date(2026, 6, 9))
    assert cand is not None
    assert cand.facts_json["facts"]["padre_rank"] == 8
    assert cand.facts_json["facts"]["padre_value"] == "15"
    assert "Tatis" in cand.facts_json["headline"]
    # Provenance must be present
    assert len(cand.provenance_json) == 1
    assert "mlb_leaders" in cand.provenance_json[0]["source_table"]
