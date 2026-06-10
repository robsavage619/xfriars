"""Tests for the novelty scoring module."""

from __future__ import annotations

from padres_analytics.detect.scoring import min_novelty_threshold, novelty_score


def test_score_in_range() -> None:
    components = {
        "rarity": 0.8,
        "magnitude": 0.6,
        "timeliness": 0.5,
        "rootability": 0.7,
        "legibility": 0.9,
    }
    score, returned = novelty_score(components, "on_this_day")
    assert 0.0 <= score <= 1.0
    assert returned == components


def test_score_clamped_to_one() -> None:
    # All max components should not exceed 1.0 even with bonus
    components = dict.fromkeys(
        ["rarity", "magnitude", "timeliness", "rootability", "legibility"], 1.0
    )
    score, _ = novelty_score(components, "crossjoin")
    assert score <= 1.0


def test_zero_components() -> None:
    components = dict.fromkeys(
        ["rarity", "magnitude", "timeliness", "rootability", "legibility"], 0.0
    )
    score, _ = novelty_score(components, "on_this_day")
    # May be positive due to detector bonus, but must be >= 0
    assert score >= 0.0


def test_threshold_is_positive() -> None:
    t = min_novelty_threshold()
    assert t > 0.0
    assert t < 1.0
