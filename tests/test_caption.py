"""Tests for engagement-aware captions — built for how X ranks, audited for demotions."""

from __future__ import annotations

from datetime import date

from padres_analytics.caption import build_caption, caption_audit, first_reply, reply_hook
from padres_analytics.detect.angles import Stat, StoryAngle


def _angle(key: str, direction: str = "down", stats: list[Stat] | None = None) -> StoryAngle:
    return StoryAngle(
        key=key,
        subject="Gavin Sheets",
        title="HIT A WALL",
        headline="Gavin Sheets has cooled hard — 176 points of on-base off his prior form.",
        thesis="t",
        direction=direction,
        effect=176,
        reliability=0.9,
        interest=1.0,
        confidence="high",
        as_of=date(2026, 6, 20),
        stats=stats or [Stat("chg_recent", 0.267, "woba", "recent OBP", 60, shown=False)],
    )


def test_post_leads_with_hook_and_ends_on_a_question() -> None:
    """The main post opens with the verdict and ends on a reply-driving question."""
    cap = build_caption(_angle("change"))
    assert cap.startswith("Gavin Sheets has cooled hard")  # line-1 hook
    assert cap.rstrip().endswith("confidence)") and "?" in cap  # a real question, then the tag
    assert "http" not in cap  # no link in the post


def test_reply_hook_is_direction_aware() -> None:
    """A debate question matches the story's claim and direction."""
    assert "adjusted" in reply_hook(_angle("change", "down")).lower()
    assert "cools" in reply_hook(_angle("change", "up")).lower()
    assert reply_hook(_angle("pitcher_luck")).endswith("?")


def test_first_reply_carries_the_gloss_and_link_slot() -> None:
    """The gloss + link live in the author's first reply, not the post."""
    reply = first_reply(_angle("change"))
    assert reply is not None
    assert "on-base percentage is" in reply  # the casual gloss
    assert "methodology" in reply.lower()  # where the link goes
    # an angle with no glossable stat has no required reply
    assert first_reply(_angle("change", stats=[Stat("x", 1, "count", "x", 0)])) is None


def test_audit_flags_what_x_demotes() -> None:
    """The lint catches links, hashtag spam, engagement-bait, AI-tells, and length."""
    assert any("link" in w for w in caption_audit("Big news https://x.com/foo is it real?"))
    assert any("hashtag" in w for w in caption_audit("Hot take? #mlb #padres #sd"))
    assert any("bait" in w for w in caption_audit("He's cooling. Like if you agree?"))
    assert any("AI-tell" in w for w in caption_audit("Let's delve into it. Real or not?"))
    assert any("reply hook" in w for w in caption_audit("He has cooled hard, no question."))
    assert any("over" in w for w in caption_audit("x " * 200 + "?"))


def test_audit_passes_a_clean_post() -> None:
    """A tight, question-ending, link-free post has nothing to flag."""
    assert caption_audit(build_caption(_angle("change"))) == []
