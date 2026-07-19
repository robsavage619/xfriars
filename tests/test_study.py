"""Study dossiers: the audit corpus, verdict honesty, and rate construction."""

from __future__ import annotations

from datetime import date

import pytest

from padres_analytics.detect.aggregates import BATTER_AGGS, fetch_agg_rows, rate_is_bounded
from padres_analytics.study.dossier import StudyDossier, StudyNode


def _node(node_id: str, verdict: str, **kw) -> StudyNode:
    return StudyNode(node_id=node_id, question="q?", verdict=verdict, **kw)  # type: ignore[arg-type]


def _dossier(*nodes: StudyNode) -> StudyDossier:
    return StudyDossier(
        study_id="s1",
        subject_id=1,
        subject_name="Test Padre",
        tree="gap_woba",
        as_of=date(2026, 7, 18),
        nodes=list(nodes),
    )


# ── the audit corpus ────────────────────────────────────────────────────────


def test_every_node_fact_reaches_the_audit_corpus() -> None:
    """Narrative is checked against this dump; a fact outside it can't be cited."""
    d = _dossier(_node("gap", "fired", facts={"woba": 0.270, "gap": 0.037}))
    corpus = d.audit_corpus()
    assert corpus["nodes"][0]["facts"]["woba"] == 0.270
    assert corpus["nodes"][0]["facts"]["gap"] == 0.037


def test_digest_changes_when_a_number_changes() -> None:
    a = _dossier(_node("gap", "fired", facts={"gap": 0.037}))
    b = _dossier(_node("gap", "fired", facts={"gap": 0.041}))
    assert a.digest() != b.digest()


def test_digest_is_stable_for_identical_content() -> None:
    assert (
        _dossier(_node("g", "fired", facts={"x": 1})).digest()
        == _dossier(_node("g", "fired", facts={"x": 1})).digest()
    )


# ── verdict honesty ─────────────────────────────────────────────────────────


def test_unanswerable_steps_are_reported_not_dropped() -> None:
    """A study must state the shape of its own ignorance."""
    d = _dossier(
        _node("gap", "fired", facts={"gap": 0.037}),
        _node("comps", "insufficient", reason="Expected stats cover 1 season."),
    )
    assert len(d.insufficient()) == 1
    assert "1 season" in d.insufficient()[0].reason
    assert "could not be answered" in d.summary()


def test_only_fired_nodes_count_as_evidence() -> None:
    d = _dossier(
        _node("a", "fired"),
        _node("b", "quiet"),
        _node("c", "insufficient", reason="no data"),
    )
    assert [n.node_id for n in d.fired()] == ["a"]
    assert d.nodes[1].is_evidence() is False


def test_a_study_that_answered_nothing_is_still_a_valid_outcome() -> None:
    d = _dossier(_node("gap", "insufficient", reason="Subject below the PA minimum."))
    assert d.fired() == []
    assert d.audit_corpus()["nodes"][0]["reason"]


# ── rate construction (the bug a study surfaced) ────────────────────────────


def test_no_rate_can_exceed_one_hundred_percent(padres_db) -> None:
    """A chase rate of 99.8% is a broken fraction, not a hitter with no discipline.

    Checked empirically over data containing every description value, because
    the containment is semantic — a whiff is a swing without textually naming
    every swing type — so string inspection would give a false answer.
    """
    padres_db.execute("DROP TABLE IF EXISTS statcast_batter_pitches")
    padres_db.execute(
        """
        CREATE TABLE statcast_batter_pitches (
            batter_id INTEGER, batter_name VARCHAR, season INTEGER, game_date DATE,
            pitch_type VARCHAR, zone INTEGER, description VARCHAR, p_throws VARCHAR
        )
        """
    )
    descriptions = [
        "ball",
        "foul",
        "hit_into_play",
        "called_strike",
        "swinging_strike",
        "blocked_ball",
        "foul_tip",
        "swinging_strike_blocked",
        "hit_by_pitch",
        "foul_bunt",
        "missed_bunt",
    ]
    rows = []
    for zone in (1, 5, 9, 11, 13):
        for desc in descriptions:
            for _ in range(20):
                rows.append((1, "P", 2026, "2026-05-01", "FF", zone, desc, "R"))
    padres_db.executemany("INSERT INTO statcast_batter_pitches VALUES (?,?,?,?,?,?,?,?)", rows)
    for metric in BATTER_AGGS:
        assert rate_is_bounded(padres_db, metric, 2026), f"{metric.id} exceeded 100%"


def _seed(conn) -> None:
    conn.execute(
        """
        CREATE TABLE statcast_batter_pitches (
            batter_id INTEGER, batter_name VARCHAR, season INTEGER, game_date DATE,
            pitch_type VARCHAR, zone INTEGER, description VARCHAR, p_throws VARCHAR
        )
        """
    )
    rows = []
    # Swings at everything in the zone, takes everything outside it.
    for _ in range(200):
        rows.append((1, "P", 2026, "2026-05-01", "FF", 5, "hit_into_play", "R"))
    for _ in range(200):
        rows.append((1, "P", 2026, "2026-05-01", "FF", 11, "ball", "R"))
    conn.executemany("INSERT INTO statcast_batter_pitches VALUES (?,?,?,?,?,?,?,?)", rows)


def test_chase_rate_counts_only_out_of_zone_swings(padres_db) -> None:
    """The hitter swings at every strike and no ball: chase must be 0, not 50."""
    padres_db.execute("DROP TABLE IF EXISTS statcast_batter_pitches")
    _seed(padres_db)
    chase = next(m for m in BATTER_AGGS if m.id == "chase_rate")
    rows, sizes, _ = fetch_agg_rows(padres_db, chase, 2026)
    assert rows[0][2] == pytest.approx(0.0)
    assert sizes[rows[0][0]] == 200  # denominator is out-of-zone pitches only
