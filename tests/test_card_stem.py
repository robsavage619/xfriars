"""Tests for story-card filename stems.

The lens key alone is not unique: two subjects surfaced by the same lens in one
season used to overwrite each other's PNG, so the older board card rendered the
newer subject's image.
"""

from __future__ import annotations

from datetime import date

from padres_analytics.detect.angles import StoryAngle
from padres_analytics.render.story_infographic import card_stem


def _angle(subject: str, key: str = "change") -> StoryAngle:
    return StoryAngle(
        key=key,
        subject=subject,
        title="FLIPPED A SWITCH",
        headline=f"{subject} has flipped a switch.",
        thesis="thesis",
        direction="up",
        effect=1.0,
        reliability=0.9,
        interest=1.0,
        confidence="high",
        as_of=date(2026, 6, 20),
    )


def test_same_lens_different_subjects_get_distinct_stems() -> None:
    france = card_stem("story", _angle("Ty France"), 2026)
    sheets = card_stem("story", _angle("Gavin Sheets"), 2026)

    assert france != sheets
    assert france == "story_change_ty_france_2026"
    assert sheets == "story_change_gavin_sheets_2026"


def test_stem_separates_lens_season_and_prefix() -> None:
    angle = _angle("Manny Machado")

    assert card_stem("daily", angle, 2026) != card_stem("story", angle, 2026)
    assert card_stem("story", angle, 2025) != card_stem("story", angle, 2026)
    assert card_stem("story", _angle("Manny Machado", key="player_luck"), 2026) != card_stem(
        "story", angle, 2026
    )


def test_punctuation_and_case_collapse_to_a_filesystem_safe_slug() -> None:
    assert card_stem("story", _angle("Fernando Tatis Jr."), 2026) == (
        "story_change_fernando_tatis_jr_2026"
    )
    assert card_stem("story", _angle("SDP|CONJUNCTION|665487"), 2026) == (
        "story_change_sdp_conjunction_665487_2026"
    )


def test_subject_with_no_alphanumerics_still_yields_a_stem() -> None:
    assert card_stem("story", _angle("—"), 2026) == "story_change_subject_2026"
