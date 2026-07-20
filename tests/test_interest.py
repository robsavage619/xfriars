"""Tests for editorial interest scoring."""

from __future__ import annotations

from padres_analytics.detect.interest import (
    Interest,
    claim_key,
    rank_board,
    score_candidate,
)


def _payload(
    headline: str = "",
    hint: str | None = None,
    detector: str = "scan",
    **facts: object,
) -> dict:
    return {
        "headline": headline,
        "card_hint": hint,
        "facts": dict(facts),
        "_detector": detector,
    }


# ── Conjunctions ──────────────────────────────────────────────────────────────


def test_crowded_conjunction_is_boring() -> None:
    # "one of 7 out of 182" reads rare but 3.8% of the league clears the bar,
    # and 36 family pairs were available to find it.
    got = score_candidate(
        "scan",
        _payload(
            "X is one of 7 players out of 182 qualified in the top 10% in both A and B",
            hint="conjunction",
            n_metrics=2,
            players_meeting_all=7,
            population_size=182,
        ),
    )
    assert got.verdict == "boring"
    assert any("crowded" in f for f in got.flags)


def test_unique_conjunction_beats_crowded_one() -> None:
    unique = score_candidate(
        "scan",
        _payload(
            "X is the only player out of 210 qualified in the top 10% in both A and B",
            hint="conjunction",
            n_metrics=2,
            players_meeting_all=1,
            population_size=210,
        ),
    )
    crowded = score_candidate(
        "scan",
        _payload(
            "X is one of 7 players out of 182 in the top 10% in both A and B",
            hint="conjunction",
            n_metrics=2,
            players_meeting_all=7,
            population_size=182,
        ),
    )
    assert unique.score > crowded.score


def test_three_way_conjunction_pays_for_the_search() -> None:
    two = score_candidate(
        "scan",
        _payload(hint="conjunction", n_metrics=2, players_meeting_all=1, population_size=200),
    )
    three = score_candidate(
        "scan",
        _payload(hint="conjunction", n_metrics=3, players_meeting_all=1, population_size=200),
    )
    # Same empirical rarity, but 84 triples were available against 36 pairs.
    assert three.search_bits > two.search_bits
    assert three.score < two.score


def test_conjunction_without_denominator_earns_nothing() -> None:
    got = score_candidate("scan", _payload(hint="conjunction", n_metrics=2))
    assert got.components["surprise"] == 0.0
    assert any("no-denominator" in f for f in got.flags)


# ── Single-metric claims ──────────────────────────────────────────────────────


def test_shallow_tail_is_boring_and_deep_tail_is_not() -> None:
    shallow = score_candidate(
        "scan",
        _payload("X is in the top 10% of MLB in Y", padre_percentile=90, population_size=500),
    )
    deep = score_candidate(
        "scan",
        _payload("X is in the top 1% of MLB in Y", padre_percentile=99.8, population_size=500),
    )
    assert shallow.verdict == "boring"
    assert deep.score > shallow.score
    assert any("shallow tail" in f for f in shallow.flags)


def test_roster_max_is_boring() -> None:
    got = score_candidate(
        "scan", _payload("Samad Taylor leads the Padres in Sprint Speed", n_padres=26)
    )
    assert got.verdict == "boring"
    assert any("roster-max" in f for f in got.flags)


def test_contrast_uses_the_stated_percentile() -> None:
    wide = score_candidate(
        "scan",
        _payload(
            "X — whiff rate: a 43.8-point gap, wider than 98% of 350 MLB hitters",
            hint="contrast",
            gap=43.8,
            population_size=350,
            a_value=56.8,
            b_value=13.0,
        ),
    )
    narrow = score_candidate(
        "scan",
        _payload(
            "X — chase rate: a 12.4-point gap, wider than 86% of 349 MLB hitters",
            hint="contrast",
            gap=12.4,
            population_size=349,
            a_value=42.9,
            b_value=30.6,
        ),
    )
    # Before the headline was parsed both scored at the 1/population ceiling.
    assert wide.score > narrow.score


# ── Stakes, tension, legibility ───────────────────────────────────────────────


def test_stakes_rescue_a_low_surprise_countdown() -> None:
    # 0.1 WAR from third all-time is not statistically surprising; it is a
    # countdown, which is the most postable thing the engine makes.
    got = score_candidate(
        "milestone_watch",
        _payload("Tatis is 0.1 WAR from passing Machado for 3rd all-time", gap_war=0.1),
    )
    assert got.components["surprise"] == 0.0
    assert got.verdict == "strong"


def test_franchise_record_is_strong() -> None:
    got = score_candidate(
        "career_chase",
        _payload(
            "Machado is the Padres' all-time home run leader",
            franchise_rank=1,
            tier="franchise_record",
        ),
    )
    assert got.verdict == "strong"


def test_expected_vs_actual_gap_scores_on_tension() -> None:
    got = score_candidate("scan", _payload("X has a gap", gap_woba_value=0.037))
    assert got.components["tension"] > 0.8


def test_legibility_penalises_multi_clause_claims() -> None:
    simple = score_candidate(
        "scan", _payload("X is in the top 1% in Y", padre_percentile=99.8, population_size=500)
    )
    tangled = score_candidate(
        "scan",
        _payload(
            "X ranks in MLB's top 10% in all of Swing Rate in the zone "
            "and Sprint Speed and Arm Strength",
            padre_percentile=99.8,
            population_size=500,
            n_metrics=3,
        ),
    )
    assert tangled.components["legibility"] < simple.components["legibility"]


def test_short_first_since_gap_is_discounted() -> None:
    recent = score_candidate(
        "scan",
        _payload(
            "X is the first Padre since Y (2025) to achieve 94 in Z",
            padre_percentile=99.8,
            population_size=500,
            metric_year=2026,
        ),
    )
    distant = score_candidate(
        "scan",
        _payload(
            "X is the first Padre since Y (2004) to achieve 94 in Z",
            padre_percentile=99.8,
            population_size=500,
            metric_year=2026,
        ),
    )
    assert recent.score < distant.score
    assert any("recurring event" in f for f in recent.flags)


def test_unchanged_repeat_is_penalised() -> None:
    payload = _payload("X is the all-time leader", franchise_rank=1, tier="franchise_record")
    fresh = score_candidate("career_chase", payload)
    stale = score_candidate("career_chase", payload, unchanged=True)
    assert stale.score < fresh.score * 0.5
    assert "unchanged since last emission" in stale.flags


# ── Claim identity and board selection ────────────────────────────────────────


def test_claim_key_ignores_render_date() -> None:
    june = _payload("Tatis is 0.1 WAR from 3rd", gap_war=0.1, player_name="Tatis", metric_year=2026)
    july = _payload("Tatis is 0.1 WAR from 3rd", gap_war=0.1, player_name="Tatis", metric_year=2026)
    june["as_of"] = "2026-06-14"
    july["as_of"] = "2026-07-19"
    june["subtitle"] = "through 2026-06-14"
    july["subtitle"] = "through 2026-07-19"
    assert claim_key("milestone_watch", june) == claim_key("milestone_watch", july)


def test_claim_key_separates_a_moved_measure() -> None:
    before = _payload(player_name="Tatis", gap_war=0.1)
    after = _payload(player_name="Tatis", gap_war=0.4)
    assert claim_key("milestone_watch", before) != claim_key("milestone_watch", after)


def test_rank_board_caps_one_detector() -> None:
    rows = [
        (
            f"c{i}",
            _payload(
                f"Player {i} is 1 SB from 150 as a Padre",
                detector="milestone_club",
                player_name=f"Player {i}",
                club_size=2,
                gap=1,
            ),
        )
        for i in range(8)
    ]
    board = rank_board(rows, limit=12, max_per_detector=3)
    assert len(board) == 3


def test_rank_board_caps_one_subject() -> None:
    rows = [
        (
            f"c{i}",
            _payload(
                f"Tatis claim {i}",
                detector=f"det{i}",
                player_name="Tatis",
                club_size=2,
                gap=1,
            ),
        )
        for i in range(6)
    ]
    board = rank_board(rows, limit=12, max_per_subject=2)
    assert len(board) == 2


def test_rank_board_drops_boring() -> None:
    rows = [
        ("dull", _payload("X leads the Padres in Sprint Speed", detector="scan", n_padres=26)),
        (
            "good",
            _payload(
                "Machado is the all-time leader",
                detector="career_chase",
                player_name="Machado",
                tier="franchise_record",
                franchise_rank=1,
            ),
        ),
    ]
    board = rank_board(rows)
    assert [cid for cid, _ in board] == ["good"]


def test_verdict_bands() -> None:
    assert Interest(score=0.8, surprise_bits=0, search_bits=0).verdict == "strong"
    assert Interest(score=0.55, surprise_bits=0, search_bits=0).verdict == "ok"
    assert Interest(score=0.4, surprise_bits=0, search_bits=0).verdict == "thin"
    assert Interest(score=0.1, surprise_bits=0, search_bits=0).verdict == "boring"


# ── Candidate identity ────────────────────────────────────────────────────────


def test_candidate_id_ignores_the_render_date() -> None:
    """The same claim restated on a later day must collide, not mint a new id."""
    from padres_analytics.detect.candidates import make_candidate_id

    june = {
        "as_of": "2026-06-14",
        "subtitle": "Career WAR as a Padre · through 2026-06-14",
        "facts": {"career_sdp_war": 26.9, "gap_war": 0.1},
    }
    july = {
        "as_of": "2026-07-19",
        "subtitle": "Career WAR as a Padre · through 2026-07-19",
        "facts": {"career_sdp_war": 26.9, "gap_war": 0.1},
    }
    subject = "SDP|milestone_watch|665487|592518"
    assert make_candidate_id("milestone_watch", subject, june) == make_candidate_id(
        "milestone_watch", subject, july
    )


def test_candidate_id_separates_a_moved_measure() -> None:
    """When the underlying number moves, it is a new claim."""
    from padres_analytics.detect.candidates import make_candidate_id

    before = {"as_of": "2026-06-14", "facts": {"career_sdp_war": 26.9, "gap_war": 0.1}}
    after = {"as_of": "2026-07-19", "facts": {"career_sdp_war": 27.2, "gap_war": 0.0}}
    subject = "SDP|milestone_watch|665487|592518"
    assert make_candidate_id("milestone_watch", subject, before) != make_candidate_id(
        "milestone_watch", subject, after
    )


def test_candidate_id_separates_seasons() -> None:
    """metric_year stays in the hash: a 2025 mark and a 2026 mark differ."""
    from padres_analytics.detect.candidates import make_candidate_id

    y25 = {"as_of": "2026-01-01", "facts": {"padre_value": 90.0, "metric_year": 2025}}
    y26 = {"as_of": "2026-01-01", "facts": {"padre_value": 90.0, "metric_year": 2026}}
    assert make_candidate_id("scan", "s", y25) != make_candidate_id("scan", "s", y26)


# ── Evidence contract ─────────────────────────────────────────────────────────


def test_evidence_is_preferred_over_shape_sniffing() -> None:
    """A supplied count wins over whatever the facts dict looks like."""
    from padres_analytics.detect.candidates import RarityEvidence

    payload = _payload(
        "X is one of 7 of 182",
        hint="conjunction",
        players_meeting_all=7,
        population_size=182,
        n_metrics=2,
    )
    sniffed = score_candidate("scan", payload)
    evidenced = score_candidate(
        "scan",
        payload,
        evidence=RarityEvidence(kind="conjunction", qualifying=1, population=500, search_space=36),
    )
    assert evidenced.score > sniffed.score


def test_evidence_kind_none_is_not_a_failure() -> None:
    """A countdown genuinely has no tail; saying so must not read as unscored."""
    from padres_analytics.detect.candidates import RarityEvidence

    got = score_candidate(
        "milestone_watch",
        _payload("Tatis is 0.1 WAR from 3rd", gap_war=0.1),
        evidence=RarityEvidence(kind="none"),
    )
    assert got.scored
    assert got.verdict == "strong"


def test_claim_with_nothing_measurable_is_unscored() -> None:
    """An unscored zero means 'could not judge', which callers must not filter on."""
    got = score_candidate("trade_war_balance", _payload("Some context card", n_eras=3))
    assert not got.scored
    assert got.score == 0.0


def test_rarity_evidence_tail_from_counts() -> None:
    from padres_analytics.detect.candidates import RarityEvidence

    assert RarityEvidence(kind="rank", qualifying=5, population=200).tail() == 0.025
    assert RarityEvidence(kind="extremeness", tail_p=0.01).tail() == 0.01
    assert RarityEvidence(kind="none").tail() is None


# ── Stakes and occasion ───────────────────────────────────────────────────────


def test_franchise_rank_decays_instead_of_falling_off_a_cliff() -> None:
    """Rewarding only rank 1 silently killed every '2nd all-time' card."""
    first = score_candidate("career_chase", _payload("1st", franchise_rank=1))
    second = score_candidate("career_chase", _payload("2nd", franchise_rank=2))
    fourth = score_candidate("career_chase", _payload("4th", franchise_rank=4))
    twentieth = score_candidate("career_chase", _payload("20th", franchise_rank=20))

    assert first.score > second.score > fourth.score
    assert fourth.verdict != "boring"
    assert twentieth.score == 0.0  # outside the top 10 is not a standing


def test_occasion_keeps_calendar_cards_alive() -> None:
    """The old model's hardcoded timeliness was a floor these cards rode on."""
    almanac = score_candidate("on_this_day", _payload("Padres are 14-14 on Jul 19"))
    assert almanac.scored
    assert almanac.verdict != "boring"


def test_longer_streaks_outrank_shorter_ones() -> None:
    short = score_candidate("cold_streak", _payload("0-for-10", skid_ab=10))
    long = score_candidate("cold_streak", _payload("0-for-25", skid_ab=25))
    assert long.score > short.score


def test_a_race_scores_on_how_close_it_is() -> None:
    close = score_candidate("nl_west_race", _payload("2 back", games_back=2.0))
    over = score_candidate("nl_west_race", _payload("14 back", games_back=14.0))
    assert close.verdict == "strong"
    assert over.verdict == "boring"  # 14 games out is not a race


# ── Learned priors (ranking only) ─────────────────────────────────────────────


def _board_rows() -> list[tuple[str, dict]]:
    return [
        ("a", _payload("A leads all-time", detector="det_a", player_name="A", franchise_rank=2)),
        ("b", _payload("B leads all-time", detector="det_b", player_name="B", franchise_rank=2)),
        ("c", _payload("C leads all-time", detector="det_c", player_name="C", franchise_rank=2)),
    ]


def test_prior_reorders_the_board() -> None:
    rows = _board_rows()
    neutral = [cid for cid, _ in rank_board(rows, limit=3, exploration_slots=0)]
    favoured = [
        cid
        for cid, _ in rank_board(
            rows,
            limit=3,
            exploration_slots=0,
            prior=lambda d, p: 1.4 if d == "det_c" else 0.8,
        )
    ]
    assert set(neutral) == set(favoured)
    assert favoured[0] == "c"  # the prior moved it to the front


def test_prior_never_changes_the_score_the_gates_read() -> None:
    """A prior that could move Interest.score could relax a gate."""
    rows = _board_rows()
    plain = {cid: i.score for cid, i in rank_board(rows, limit=3, exploration_slots=0)}
    primed = {
        cid: i.score
        for cid, i in rank_board(rows, limit=3, exploration_slots=0, prior=lambda d, p: 1.4)
    }
    assert plain == primed


def test_prior_cannot_resurrect_a_boring_candidate() -> None:
    rows = [
        ("dull", _payload("X leads the Padres in Sprint Speed", detector="scan", n_padres=26)),
        (
            "good",
            _payload(
                "Y is 2nd all-time", detector="career_chase", player_name="Y", franchise_rank=2
            ),
        ),
    ]
    board = rank_board(rows, limit=5, prior=lambda d, p: 1.4)
    assert [cid for cid, _ in board] == ["good"]


def test_exploration_slots_survive_a_hostile_prior() -> None:
    """Ranking purely on what was approved before converges on a house style."""
    rows = _board_rows()
    board = rank_board(
        rows,
        limit=2,
        exploration_slots=1,
        max_per_detector=1,
        # Punish whatever would otherwise rank first.
        prior=lambda d, p: 0.1 if d == "det_a" else 1.4,
    )
    picked = [cid for cid, _ in board]
    assert "a" in picked  # reserved slot is blind to the prior
