"""Spec identity, queue/ledger persistence, and the end-to-end detector loop."""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from padres_analytics.detect.hypothesis import store
from padres_analytics.detect.hypothesis.detector import HypothesisScanDetector
from padres_analytics.detect.hypothesis.spec import HypothesisSpec

AS_OF = date(2026, 6, 23)


def _spec(**kw: object) -> HypothesisSpec:
    base = {
        "id": "brl",
        "label": "Barrel %",
        "rationale": "barrel rate looks elite",
        "table": "statcast_batter_exitvelo_barrels",
        "value_col": "brl_percent",
        "filter_sql": "attempts >= 50",
        "lenses": ["rank", "extremeness"],
    }
    base.update(kw)
    return HypothesisSpec.model_validate(base)


# ── spec identity ────────────────────────────────────────────────────────────


def test_spec_hash_ignores_rationale() -> None:
    a = _spec(rationale="reason one")
    b = _spec(rationale="totally different wording")
    assert a.spec_hash() == b.spec_hash()


def test_spec_hash_changes_with_structure() -> None:
    assert (
        _spec(filter_sql="attempts >= 50").spec_hash()
        != _spec(filter_sql="attempts >= 100").spec_hash()
    )


def test_to_metric_spec_drops_bad_lenses() -> None:
    ms = _spec(lenses=["rank", "telepathy"]).to_metric_spec()
    assert ms.lenses == ["rank"]


# ── store roundtrip ──────────────────────────────────────────────────────────


def test_enqueue_dedups_pending(padres_db: duckdb.DuckDBPyConnection) -> None:
    assert store.enqueue(padres_db, [_spec()], AS_OF) == 1
    assert store.enqueue(padres_db, [_spec()], AS_OF) == 0
    assert len(store.pending(padres_db)) == 1


def test_mark_processed_clears_pending(padres_db: duckdb.DuckDBPyConnection) -> None:
    store.enqueue(padres_db, [_spec()], AS_OF)
    store.mark_processed(padres_db, _spec().spec_hash())
    assert store.pending(padres_db) == []


def test_log_outcome_is_explorable(padres_db: duckdb.DuckDBPyConnection) -> None:
    store.log_outcome(padres_db, _spec(), AS_OF, "below_gate", max_rarity=0.7, reason="meh")
    rows = store.explored(padres_db)
    assert len(rows) == 1
    assert rows[0].outcome == "below_gate"
    assert rows[0].max_rarity == pytest.approx(0.7)


# ── end-to-end detector ──────────────────────────────────────────────────────


_BARREL_COLS = "(player_id, player_name, year, attempts, avg_hit_speed, brl_percent)"


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    # statcast_batter_exitvelo_barrels already exists from the schema fixture.
    # One Padre with an extreme barrel rate; 30 leaguers clustered low.
    conn.execute(
        f"INSERT INTO statcast_batter_exitvelo_barrels {_BARREL_COLS} "
        "VALUES (?, ?, 2026, 200, 95.0, 28.0)",
        [665487, "Tatis Jr., F"],
    )
    for i in range(30):
        conn.execute(
            f"INSERT INTO statcast_batter_exitvelo_barrels {_BARREL_COLS} "
            "VALUES (?, ?, 2026, 180, 89.0, ?)",
            [9000 + i, f"Player, {i}", 4.0 + i * 0.1],
        )
    conn.execute(
        """
        CREATE TABLE team_rosters (
            player_id INTEGER, team_id INTEGER, season INTEGER,
            roster_type VARCHAR, status VARCHAR
        )
        """
    )
    conn.execute("INSERT INTO team_rosters VALUES (665487, 135, 2026, '40Man', 'Active')")


def test_valid_hypothesis_emits_candidate(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed(padres_db)
    store.enqueue(padres_db, [_spec()], AS_OF)

    candidates = HypothesisScanDetector().run(padres_db, AS_OF)

    assert len(candidates) == 1
    assert candidates[0].detector == "hypothesis"
    assert candidates[0].provenance_json[0]["origin"] == "llm"
    # ledger records the win, queue is drained
    assert store.explored(padres_db)[0].outcome == "emitted"
    assert store.pending(padres_db) == []


def test_injection_hypothesis_is_logged_invalid_not_run(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    _seed(padres_db)
    store.enqueue(padres_db, [_spec(filter_sql="attempts >= 50; DROP TABLE team_rosters")], AS_OF)

    candidates = HypothesisScanDetector().run(padres_db, AS_OF)

    assert candidates == []
    assert store.explored(padres_db)[0].outcome == "invalid"
    # the injection did not execute — table still present
    row = padres_db.execute("SELECT COUNT(*) FROM team_rosters").fetchone()
    assert row is not None and row[0] == 1


def test_windowed_spec_on_season_table_is_gated(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    _seed(padres_db)
    store.enqueue(padres_db, [_spec(window={"days": 15})], AS_OF)

    candidates = HypothesisScanDetector().run(padres_db, AS_OF)

    assert candidates == []
    assert store.explored(padres_db)[0].outcome == "unsupported_window"
