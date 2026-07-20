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
