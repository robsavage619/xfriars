"""Phase 3 tests: crossjoin + milestones detectors."""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


def _build_hist(hist: duckdb.DuckDBPyConnection) -> None:
    """Create and populate the hist tables needed for phase 3 detectors."""

    hist.execute("""
        CREATE TABLE spotrac_player_contracts (
            season     INTEGER NOT NULL,
            team_bref  VARCHAR NOT NULL,
            player_id  INTEGER,
            cap_hit    DOUBLE NOT NULL
        )
    """)

    # 5 teams; SDP = $200M with 47.1 WAR => $4.25M/WAR
    hist.executemany(
        "INSERT INTO spotrac_player_contracts VALUES (?, ?, ?, ?)",
        [
            (2025, "SDP", 1, 200_000_000.0),
            (2025, "NYM", 2, 320_000_000.0),
            (2025, "LAD", 3, 315_000_000.0),
            (2025, "MIA", 4, 80_000_000.0),
            (2025, "TBR", 5, 90_000_000.0),
        ],
    )

    hist.execute("""
        CREATE TABLE bwar_player_seasons (
            mlb_id      INTEGER NOT NULL,
            name_common VARCHAR NOT NULL,
            year_id     INTEGER NOT NULL,
            team_id     VARCHAR NOT NULL,
            war         DOUBLE NOT NULL
        )
    """)

    # WAR for the payroll teams (positive only)
    hist.executemany(
        "INSERT INTO bwar_player_seasons VALUES (?, ?, ?, ?, ?)",
        [
            # SDP 2025: 47.1 WAR total
            (1001, "Player A", 2025, "SDP", 5.0),
            (1002, "Player B", 2025, "SDP", 4.0),
            (1003, "Player C", 2025, "SDP", 38.1),
            # NYM 2025: very little WAR
            (2001, "NYM Player", 2025, "NYM", 48.4),
            # LAD 2025
            (3001, "LAD Player", 2025, "LAD", 51.0),
            # MIA 2025: efficient
            (4001, "MIA Player", 2025, "MIA", 70.0),
            # TBR 2025: efficient
            (5001, "TBR Player", 2025, "TBR", 50.0),
            # Franchise WAR for milestones — SDP all-time
            (592518, "Manny Machado", 2019, "SDP", 6.0),
            (592518, "Manny Machado", 2020, "SDP", 1.0),
            (592518, "Manny Machado", 2021, "SDP", 5.0),
            (592518, "Manny Machado", 2022, "SDP", 6.0),
            (592518, "Manny Machado", 2023, "SDP", 5.5),
            (592518, "Manny Machado", 2024, "SDP", 2.5),
            (592518, "Manny Machado", 2026, "SDP", 1.0),
            (665487, "Fernando Tatis Jr.", 2019, "SDP", 7.0),
            (665487, "Fernando Tatis Jr.", 2021, "SDP", 6.0),
            (665487, "Fernando Tatis Jr.", 2023, "SDP", 5.0),
            (665487, "Fernando Tatis Jr.", 2024, "SDP", 4.0),
            (665487, "Fernando Tatis Jr.", 2026, "SDP", 1.0),
            # Older franchise legends (not active 2026)
            (100001, "Tony Gwynn", 1982, "SDP", 69.2),
            (100002, "Dave Winfield", 1977, "SDP", 32.0),
        ],
    )

    hist.execute("""
        CREATE TABLE trade_movements (
            player_id    INTEGER NOT NULL,
            date         DATE NOT NULL,
            from_team_id INTEGER,
            to_team_id   INTEGER
        )
    """)

    # Preller era: acquired one good player, surrendered one
    hist.executemany(
        "INSERT INTO trade_movements VALUES (?, ?, ?, ?)",
        [
            (592518, "2019-02-19", None, 135),  # Machado acquired
            (1001, "2015-06-15", 135, 109),  # surrendered under Preller
        ],
    )

    hist.execute("""
        CREATE TABLE team_regime_assignments (
            bref_code VARCHAR NOT NULL,
            season    INTEGER NOT NULL,
            gm        VARCHAR NOT NULL
        )
    """)

    for yr in range(2014, 2027):
        hist.execute(
            "INSERT INTO team_regime_assignments VALUES (?, ?, ?)",
            ["SDP", yr, "A.J. Preller"],
        )


@pytest.fixture()
def hist_conn(
    padres_db: duckdb.DuckDBPyConnection,
    tmp_path,
) -> duckdb.DuckDBPyConnection:
    """padres.db with a minimal hist database for phase 3 detectors."""
    hist_path = tmp_path / "hist3.db"
    hist = duckdb.connect(str(hist_path))
    _build_hist(hist)
    hist.close()
    padres_db.execute(f"ATTACH '{hist_path}' AS hist (READ_ONLY)")
    return padres_db


# ── DollarPerWarDetector ──────────────────────────────────────────────────────


def test_dollar_per_war_emits_sdp(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.crossjoin import DollarPerWarDetector

    det = DollarPerWarDetector()
    # as_of 2026 → season 2025
    candidates = det.run(hist_conn, date(2026, 6, 9))

    assert len(candidates) == 1
    c = candidates[0]
    assert c.detector == "dollar_per_war"
    assert c.facts_json["season"] == 2025
    assert c.facts_json["sd_eff_rank"] >= 1
    assert c.facts_json["sd_payroll_m"] == pytest.approx(200.0, abs=1.0)
    assert c.facts_json["sd_war"] == pytest.approx(47.1, abs=0.5)
    assert c.subject is not None and "SDP" in c.subject
    assert c.novelty_score > 0.5


def test_dollar_per_war_no_data_returns_empty(
    padres_db: duckdb.DuckDBPyConnection, tmp_path
) -> None:
    from padres_analytics.detect.crossjoin import DollarPerWarDetector

    hist_path = tmp_path / "empty_hist.db"
    hist = duckdb.connect(str(hist_path))
    hist.execute("""
        CREATE TABLE spotrac_player_contracts
        (season INT, team_bref VARCHAR, player_id INT, cap_hit DOUBLE)
    """)
    hist.execute("""
        CREATE TABLE bwar_player_seasons
        (mlb_id INT, name_common VARCHAR, year_id INT, team_id VARCHAR, war DOUBLE)
    """)
    hist.close()
    padres_db.execute(f"ATTACH '{hist_path}' AS hist (READ_ONLY)")

    det = DollarPerWarDetector()
    result = det.run(padres_db, date(2026, 6, 9))
    assert result == []


# ── TradeWarDetector ──────────────────────────────────────────────────────────


def test_trade_war_emits_gm_era(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.crossjoin import TradeWarDetector

    det = TradeWarDetector()
    candidates = det.run(hist_conn, date(2026, 6, 9))

    assert len(candidates) == 1
    c = candidates[0]
    assert c.detector == "trade_war_balance"
    assert c.subject == "SDP|trade_war_balance"
    eras = c.facts_json["eras"]
    assert len(eras) >= 1
    assert eras[0]["gm"] == "A.J. Preller"
    # net_5yr is a float
    assert isinstance(eras[0]["net_5yr"], float)


def test_trade_war_candidate_id_stable(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.crossjoin import TradeWarDetector

    det = TradeWarDetector()
    c1 = det.run(hist_conn, date(2026, 6, 9))[0]
    c2 = det.run(hist_conn, date(2026, 6, 9))[0]
    assert c1.candidate_id == c2.candidate_id


# ── FranchiseWarRankDetector ──────────────────────────────────────────────────


def test_franchise_war_emits_active_padres(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.milestones import FranchiseWarRankDetector

    det = FranchiseWarRankDetector()
    candidates = det.run(hist_conn, date(2026, 6, 9))

    # Both Machado and Tatis are active in 2026 and in franchise top 10
    assert len(candidates) >= 1
    subjects = [c.subject or "" for c in candidates]
    assert any("592518" in s for s in subjects)  # Machado
    assert any("665487" in s for s in subjects)  # Tatis


def test_franchise_war_does_not_emit_inactive(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.milestones import FranchiseWarRankDetector

    det = FranchiseWarRankDetector()
    candidates = det.run(hist_conn, date(2026, 6, 9))

    # Gwynn has no 2026 row → not active → should not be emitted
    subjects = [c.subject or "" for c in candidates]
    assert not any("100001" in s for s in subjects)


def test_franchise_war_re_emit_gate(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.base import emit
    from padres_analytics.detect.milestones import FranchiseWarRankDetector

    det = FranchiseWarRankDetector()
    first_run = det.run(hist_conn, date(2026, 6, 1))
    emit(hist_conn, first_run)

    # Same as_of within 30 days — re_emit gate should fire
    second_run = det.run(hist_conn, date(2026, 6, 9))
    assert second_run == []


def test_franchise_war_facts_contain_headline(hist_conn: duckdb.DuckDBPyConnection) -> None:
    from padres_analytics.detect.milestones import FranchiseWarRankDetector

    det = FranchiseWarRankDetector()
    candidates = det.run(hist_conn, date(2026, 6, 9))
    for c in candidates:
        assert "headline" in c.facts_json
        headline = c.facts_json["headline"]
        assert "all-time" in headline.lower()
        assert "WAR" in headline


# ── ordinal helper ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "n,expected",
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (23, "23rd"),
        (29, "29th"),
    ],
)
def test_ordinal(n: int, expected: str) -> None:
    from padres_analytics.detect.crossjoin import _ordinal

    assert _ordinal(n) == expected
