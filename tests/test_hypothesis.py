"""Spec identity, queue/ledger persistence, and the end-to-end detector loop."""

from __future__ import annotations

from datetime import date, timedelta

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


def test_injured_padre_is_filtered_at_detection(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    _seed(padres_db)
    # The only Padre with a qualifying row is on the IL — must not surface.
    padres_db.execute(
        "UPDATE team_rosters SET status = '60-Day Injured List' WHERE player_id = 665487"
    )
    store.enqueue(padres_db, [_spec()], AS_OF)

    candidates = HypothesisScanDetector().run(padres_db, AS_OF)

    assert candidates == []
    assert store.explored(padres_db)[0].outcome == "no_data"


def _pin_season(conn: duckdb.DuckDBPyConnection, year: int = 2026) -> None:
    """Pin coverage's notion of 'current season' so it doesn't depend on wall clock."""
    conn.execute(
        "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa) "
        "VALUES (1, 'Seed', ?, 100)",
        [year],
    )


def _seed_roster(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE team_rosters (
            player_id INTEGER, team_id INTEGER, season INTEGER,
            roster_type VARCHAR, status VARCHAR
        )
        """
    )
    conn.execute("INSERT INTO team_rosters VALUES (665487, 135, 2026, '40Man', 'Active')")


def _seed_window(conn: duckdb.DuckDBPyConnection, n_league: int = 30) -> None:
    """Game-grain batted balls: one hot Padre + ``n_league`` league bats over a week."""
    cols = (
        "(player_id, player_name, season, game_pk, at_bat_number, "
        "pitch_number, game_date, estimated_woba)"
    )
    gp = 0
    for day in range(8):  # 8 days, all inside a 15-day window ending AS_OF
        d = AS_OF - timedelta(days=day)
        conn.execute(
            f"INSERT INTO statcast_batted_balls {cols} VALUES (?, ?, 2026, ?, 1, 1, ?, 0.620)",
            [665487, "Tatis Jr., F", gp := gp + 1, d],
        )
        for p in range(n_league):
            conn.execute(
                f"INSERT INTO statcast_batted_balls {cols} VALUES (?, ?, 2026, ?, 1, 1, ?, ?)",
                [9000 + p, f"Player, {p}", gp := gp + 1, d, 0.230 + p * 0.002],
            )
    _pin_season(conn)
    _seed_roster(conn)


def test_windowed_spec_on_game_grain_emits(padres_db: duckdb.DuckDBPyConnection) -> None:
    _seed_window(padres_db)
    spec = _spec(
        id="xwoba_l15",
        label="xwOBA (last 15d)",
        table="statcast_batted_balls",
        value_col="estimated_woba",
        filter_sql="",
        window={"days": 15},
    )
    store.enqueue(padres_db, [spec], AS_OF)

    candidates = HypothesisScanDetector().run(padres_db, AS_OF)

    assert len(candidates) == 1
    assert candidates[0].detector == "hypothesis"
    # claim scope is the window, not the season — honesty gate
    assert "last 15 days" in candidates[0].claim_scope
    assert store.explored(padres_db)[0].outcome == "emitted"


def test_thin_coverage_blocks_before_scan(padres_db: duckdb.DuckDBPyConnection) -> None:
    # statcast_batted_balls is contracted (CONTACT_TREND); 4 league bats is PARTIAL.
    _seed_window(padres_db, n_league=4)
    spec = _spec(
        id="xwoba_l15",
        label="xwOBA (last 15d)",
        table="statcast_batted_balls",
        value_col="estimated_woba",
        filter_sql="",
        window={"days": 15},
    )
    store.enqueue(padres_db, [spec], AS_OF)

    candidates = HypothesisScanDetector().run(padres_db, AS_OF)

    assert candidates == []
    assert store.explored(padres_db)[0].outcome == "coverage_blocked"
