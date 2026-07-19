"""Referee tests: adjudication rule, the never-compute contract, and staleness."""

from __future__ import annotations

from datetime import date

import pytest

from padres_analytics.review.gate import (
    NotClearedError,
    RefereeContractError,
    adjudicate,
    assert_caption_digits_unchanged,
    assert_no_fact_mutation,
    require_clearance,
)
from padres_analytics.review.models import ReviewPacket, ReviewVerdict


def _packet(**over) -> ReviewPacket:
    base = {
        "target_kind": "draft",
        "target_id": "d1",
        "as_of": date(2026, 7, 18),
        "claim": "Machado is one of 5 players in MLB in the top 12% in both OAA and xwOBA-wOBA gap",
        "caption": "Machado has been unlucky.",
        "facts": {"padre_value": 0.037, "population_size": 517},
    }
    base.update(over)
    return ReviewPacket(**base)


def _v(lens: str, verdict: str, mode: str | None = None, confidence: float = 1.0) -> ReviewVerdict:
    return ReviewVerdict(
        lens=lens,  # type: ignore[arg-type]
        verdict=verdict,  # type: ignore[arg-type]
        failure_mode=mode,  # type: ignore[arg-type]
        evidence="because",
        confidence=confidence,
    )


# ── decision rule ───────────────────────────────────────────────────────────


def test_all_pass_clears() -> None:
    verdicts = [_v(x, "PASS") for x in ("statistician", "causal", "coverage", "editor", "voice")]
    assert adjudicate(_packet(), verdicts).outcome == "cleared"


def test_one_block_beats_four_passes() -> None:
    """Not majority vote — a single sound refutation is enough."""
    verdicts = [
        _v("statistician", "BLOCK", "arbitrary_endpoint"),
        _v("causal", "PASS"),
        _v("coverage", "PASS"),
        _v("editor", "PASS"),
        _v("voice", "PASS"),
    ]
    result = adjudicate(_packet(), verdicts)
    assert result.outcome == "blocked"
    assert result.blocking_lenses() == ["statistician"]
    assert "arbitrary_endpoint" in result.failure_modes


def test_two_revises_means_revise() -> None:
    verdicts = [
        _v("voice", "REVISE", "voice_tell"),
        _v("editor", "REVISE", "trivial"),
        _v("causal", "PASS"),
    ]
    assert adjudicate(_packet(), verdicts).outcome == "revise"


def test_lone_revise_is_still_a_revise_but_noted_as_prose() -> None:
    verdicts = [_v("voice", "REVISE", "voice_tell"), _v("causal", "PASS")]
    result = adjudicate(_packet(), verdicts)
    assert result.outcome == "revise"
    assert "prose note" in result.rationale


# ── asymmetric uncertainty ──────────────────────────────────────────────────


def test_unsure_causal_pass_becomes_a_block() -> None:
    """An unearned causal story is the costliest thing to publish."""
    verdicts = [_v("causal", "PASS", confidence=0.4), _v("editor", "PASS")]
    assert adjudicate(_packet(), verdicts).outcome == "blocked"


def test_unsure_coverage_pass_becomes_a_block() -> None:
    verdicts = [_v("coverage", "PASS", confidence=0.3)]
    assert adjudicate(_packet(), verdicts).outcome == "blocked"


def test_unsure_editor_pass_does_not_block() -> None:
    """A hesitant 'so what' is not grounds to kill a card."""
    verdicts = [_v("editor", "PASS", confidence=0.3), _v("causal", "PASS")]
    assert adjudicate(_packet(), verdicts).outcome == "cleared"


# ── contract enforcement ────────────────────────────────────────────────────


def test_rejection_must_name_a_failure_mode() -> None:
    with pytest.raises(RefereeContractError, match="without a failure_mode"):
        adjudicate(_packet(), [_v("editor", "BLOCK")])


def test_lens_cannot_reach_outside_its_remit() -> None:
    with pytest.raises(RefereeContractError, match="outside its remit"):
        adjudicate(_packet(), [_v("voice", "BLOCK", "sample_too_small")])


def test_empty_panel_is_not_a_clearance() -> None:
    with pytest.raises(RefereeContractError, match="empty panel"):
        adjudicate(_packet(), [])


def test_duplicate_lens_verdicts_are_rejected() -> None:
    with pytest.raises(RefereeContractError, match="Duplicate verdict"):
        adjudicate(_packet(), [_v("editor", "PASS"), _v("editor", "BLOCK", "trivial")])


# ── the never-compute invariant ─────────────────────────────────────────────


def test_referee_may_not_mutate_facts() -> None:
    original = {"padre_value": 0.037}
    with pytest.raises(RefereeContractError, match="modified facts_json"):
        assert_no_fact_mutation(original, {"padre_value": 0.041})


def test_unchanged_facts_pass() -> None:
    original = {"padre_value": 0.037}
    assert_no_fact_mutation(original, dict(original))
    assert_no_fact_mutation(original, None)


def test_suggested_caption_may_not_introduce_a_number() -> None:
    facts = {"padre_value": 0.037}
    with pytest.raises(RefereeContractError, match="introduces unaudited numbers"):
        assert_caption_digits_unchanged(
            "Machado has been unlucky.",
            "Machado has been unlucky — 47 points of it.",
            facts,
        )


def test_suggested_caption_may_rephrase_freely() -> None:
    facts = {"padre_value": 0.037, "n": 5}
    assert_caption_digits_unchanged(
        "Machado has been unlucky.",
        "The bounces have not gone Machado's way.",
        facts,
    )


# ── clearance + staleness ───────────────────────────────────────────────────


def test_no_adjudication_blocks_advancement() -> None:
    with pytest.raises(NotClearedError, match="no referee adjudication"):
        require_clearance(None, _packet())


def test_blocked_adjudication_blocks_advancement() -> None:
    packet = _packet()
    adj = adjudicate(packet, [_v("editor", "BLOCK", "trivial")])
    with pytest.raises(NotClearedError, match="was blocked"):
        require_clearance(adj, packet)


def test_clearance_travels_with_the_content_it_saw() -> None:
    packet = _packet()
    adj = adjudicate(packet, [_v("editor", "PASS")])
    require_clearance(adj, packet)  # same content: fine

    recaptioned = _packet(caption="Totally different caption with new framing.")
    with pytest.raises(NotClearedError, match="stale"):
        require_clearance(adj, recaptioned)


def test_packet_hash_ignores_incidental_fields() -> None:
    """Re-running review on the same content must not churn the hash."""
    a = _packet(detector="scan", rarity=0.91)
    b = _packet(detector="scan", rarity=0.91, battery_size=18)
    assert a.packet_hash() == b.packet_hash()


def test_packet_hash_tracks_the_facts() -> None:
    a = _packet()
    b = _packet(facts={"padre_value": 0.041, "population_size": 517})
    assert a.packet_hash() != b.packet_hash()
