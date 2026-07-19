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


# ── composition ─────────────────────────────────────────────────────────────


def _fired_dossier() -> StudyDossier:
    return StudyDossier(
        study_id="s2",
        subject_id=592518,
        subject_name="Test Padre",
        tree="gap_woba",
        as_of=date(2026, 7, 18),
        headline="Test Padre's wOBA sits 0.037 behind expected.",
        nodes=[
            _node(
                "gap",
                "fired",
                finding="wOBA sits 0.037 behind expected.",
                facts={"gap": 0.037, "gap_percentile": 82},
            ),
            _node(
                "components",
                "fired",
                finding="Carried by average and power.",
                facts={"ba_gap": 0.051, "slg_gap": 0.038},
            ),
            _node(
                "contact",
                "fired",
                finding="89.9 mph, 64th percentile.",
                facts={"avg_exit_velocity": 89.9, "exit_velocity_percentile": 64},
            ),
            _node("comps", "insufficient", reason="Expected stats cover 1 season."),
        ],
    )


def test_panels_are_selected_by_what_fired() -> None:
    """Composition is selection, not a fixed roster of panels."""
    from padres_analytics.study.compose import story_card_from_dossier

    card = story_card_from_dossier(_fired_dossier())
    assert card is not None
    assert len(card.blocks) == 3  # the insufficient node contributes no panel


def test_a_study_that_barely_fired_composes_nothing() -> None:
    """One answered question is a fact, not a story."""
    from padres_analytics.study.compose import story_card_from_dossier

    thin = StudyDossier(
        study_id="s3",
        subject_id=1,
        subject_name="P",
        tree="gap_woba",
        as_of=date(2026, 7, 18),
        nodes=[_node("gap", "fired", facts={"gap": 0.037, "gap_percentile": 82})],
    )
    assert story_card_from_dossier(thin) is None


def test_the_closing_line_names_what_is_still_open() -> None:
    """A deep dive that hides its open questions sells a conclusion it didn't reach."""
    from padres_analytics.study.compose import story_card_from_dossier

    card = story_card_from_dossier(_fired_dossier())
    assert card is not None
    assert card.narrative.startswith("Still open:")
    # And it must not simply restate the finding the hero already carries.
    assert "0.037" not in card.narrative


def test_every_composed_number_traces_to_a_node_fact() -> None:
    """The dossier stays the audit corpus; composition may not invent a value."""
    from padres_analytics.study.compose import story_card_from_dossier

    dossier = _fired_dossier()
    card = story_card_from_dossier(dossier)
    assert card is not None

    node_facts = {str(v) for n in dossier.nodes for v in n.facts.values()}
    for block in card.blocks:
        stripped = block.value.lstrip("0") or "0"
        assert any(stripped in fact or block.value in fact for fact in node_facts), block.value


def test_composed_candidate_carries_the_dossier_digest() -> None:
    """Provenance must tie the card back to the exact frozen dossier."""
    from padres_analytics.study.compose import candidate_from_dossier

    dossier = _fired_dossier()
    candidate = candidate_from_dossier(dossier)
    assert candidate is not None
    assert candidate.payload_kind == "story"
    assert candidate.provenance_json[0]["dossier_digest"] == dossier.digest()


# ── the gradable registry ───────────────────────────────────────────────────


def test_gradable_claims_are_registered_not_hardcoded() -> None:
    from padres_analytics.predict import gradable_keys, gradable_spec

    assert "pitcher_luck" in gradable_keys()
    assert gradable_spec("pitcher_luck") is not None
    assert gradable_spec("not_a_claim") is None


def test_registering_a_new_falsifiable_claim_works() -> None:
    from padres_analytics.predict import GradableSpec, gradable_spec, register_gradable

    spec = GradableSpec(
        baseline_key="b",
        target_key="t",
        metric="xwOBA",
        epsilon=0.005,
        table="statcast_batting_expected",
        column="woba",
        id_col="player_id",
        season_col="year",
    )
    register_gradable("test_claim_shape", spec)
    assert gradable_spec("test_claim_shape") == spec


def test_rebinding_a_claim_shape_is_refused() -> None:
    """Changing how a key grades would rewrite predictions already logged under it."""
    from padres_analytics.predict import GradableSpec, register_gradable

    first = GradableSpec(
        baseline_key="b",
        target_key="t",
        metric="ERA",
        epsilon=0.10,
        table="player_season_pitching",
        column="era",
        id_col="player_id",
        season_col="season",
    )
    register_gradable("rebind_test", first)
    conflicting = GradableSpec(
        baseline_key="b",
        target_key="t",
        metric="ERA",
        epsilon=0.99,  # different threshold — different meaning
        table="player_season_pitching",
        column="era",
        id_col="player_id",
        season_col="season",
    )
    with pytest.raises(ValueError, match="already registered"):
        register_gradable("rebind_test", conflicting)


# ── league backfill ─────────────────────────────────────────────────────────


def test_qualified_population_comes_from_season_stats_not_the_event_table(padres_db) -> None:
    """Sourcing from the table being filled would only return who's already there."""
    from padres_analytics.ingest.statcast_events import qualified_batter_ids

    padres_db.execute(
        "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa) "
        "VALUES (1, 'Qualified', 2026, 400), (2, 'Thin', 2026, 40)"
    )
    ids = qualified_batter_ids(padres_db, 2026, min_pa=100)
    assert [pid for pid, _ in ids] == [1]


def test_backfill_skips_players_already_current(padres_db, monkeypatch) -> None:
    """Resumability: an interrupted run must pick up, not restart."""
    from datetime import date as _date

    from padres_analytics.ingest import statcast_events as ev

    padres_db.execute(
        "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa) "
        "VALUES (1, 'Fresh', 2026, 400), (2, 'Stale', 2026, 400)"
    )
    padres_db.execute(
        "INSERT INTO statcast_batter_pitches "
        "(batter_id, batter_name, season, game_date, game_pk, at_bat_number, pitch_number) "
        "VALUES (1, 'Fresh', 2026, DATE '2026-07-17', 1, 1, 1)"
    )

    fetched: list[int] = []

    def _fake_ingest(conn, season, batter_id, *a, **kw):
        fetched.append(batter_id)
        return 10

    monkeypatch.setattr(ev, "ingest_batter_pitches", _fake_ingest)
    monkeypatch.setattr(ev.time, "sleep", lambda _s: None)

    tally = ev.ingest_league_events(
        padres_db, 2026, "batter_pitches", delay_seconds=0, fresh_through=_date(2026, 7, 15)
    )
    assert fetched == [2]  # the current player was not refetched
    assert tally["skipped"] == 1


def test_one_failed_player_does_not_end_the_run(padres_db, monkeypatch) -> None:
    """A few hundred players must not be lost to a single unavailable one."""
    from padres_analytics.ingest import statcast_events as ev

    padres_db.execute(
        "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa) "
        "VALUES (1, 'A', 2026, 400), (2, 'B', 2026, 400), (3, 'C', 2026, 400)"
    )

    def _flaky(conn, season, batter_id, *a, **kw):
        if batter_id == 2:
            raise RuntimeError("savant unavailable")
        return 5

    monkeypatch.setattr(ev, "ingest_batter_pitches", _flaky)
    monkeypatch.setattr(ev.time, "sleep", lambda _s: None)

    tally = ev.ingest_league_events(padres_db, 2026, "batter_pitches", delay_seconds=0)
    assert tally["fetched"] == 2
    assert tally["failed"] == 1
    assert tally["rows"] == 10


# ── the comps control (mean regression vs owed luck) ────────────────────────


def _seed_comp_history(conn, *, gap_rebound: float, control_rebound: float) -> None:
    """Two cohorts: gap hitters and same-wOBA no-gap hitters, with next seasons."""
    conn.execute("DELETE FROM statcast_batting_expected")
    rows = []
    pid = 1
    for _ in range(40):  # gap cohort: woba .270, gap +.037
        rows.append((pid, f"G{pid}", 2020, 400, 300, 0.24, 0.28, 0.40, 0.45, 0.270, 0.307))
        rows.append(
            (pid, f"G{pid}", 2021, 400, 300, 0.25, 0.26, 0.42, 0.43, 0.270 + gap_rebound, 0.300)
        )
        pid += 1
    for _ in range(40):  # control: woba .270, no gap
        rows.append((pid, f"C{pid}", 2020, 400, 300, 0.24, 0.24, 0.40, 0.40, 0.270, 0.272))
        rows.append(
            (pid, f"C{pid}", 2021, 400, 300, 0.25, 0.25, 0.42, 0.42, 0.270 + control_rebound, 0.272)
        )
        pid += 1
    conn.executemany(
        "INSERT INTO statcast_batting_expected "
        "(player_id, player_name, year, pa, bip, ba, est_ba, slg, est_slg, woba, est_woba) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


def test_a_rebound_matched_by_the_control_is_not_owed_luck(padres_db) -> None:
    """The trap: gap hitters just had a bad year, and bad years rebound anyway."""
    from padres_analytics.study.trees import _node_comps

    _seed_comp_history(padres_db, gap_rebound=0.030, control_rebound=0.029)
    node, _ = _node_comps(padres_db, player_id=9999, year=2026, gap=0.037, woba=0.270)
    assert node.verdict == "quiet"
    assert "ordinary regression" in node.finding
    assert abs(float(node.facts["net_effect"])) < 0.010


def test_a_rebound_the_control_does_not_match_is_a_real_effect(padres_db) -> None:
    from padres_analytics.study.trees import _node_comps

    _seed_comp_history(padres_db, gap_rebound=0.060, control_rebound=0.010)
    node, _ = _node_comps(padres_db, player_id=9999, year=2026, gap=0.037, woba=0.270)
    assert node.verdict == "fired"
    assert float(node.facts["net_effect"]) >= 0.010
    assert "the gap itself is worth" in node.finding


def test_no_control_cohort_means_no_claim(padres_db) -> None:
    """Without a control the rebound can't be separated from mean regression."""
    from padres_analytics.study.trees import _node_comps

    _seed_comp_history(padres_db, gap_rebound=0.030, control_rebound=0.029)
    # Ask about a wOBA far from the seeded control band.
    node, _ = _node_comps(padres_db, player_id=9999, year=2026, gap=0.037, woba=0.400)
    assert node.verdict == "insufficient"
    assert "control" in node.reason
