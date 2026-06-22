"""Engagement-aware captions — written for how X ranks in 2026, not for tricks.

Grounded in current (2025-2026) multi-source reporting on the X "For You" ranking,
not the stale 2023 open-source weights. The durable, brand-safe takeaways:

* **Replies and reposts are weighted far above likes** (sources disagree on the
  exact multiple — replies ~13.5x to ~27x a like — but agree on the ordering);
  **bookmarks** are a strong "save-worthy" signal. Optimize for replies and saves,
  not likes.
* **Early-engagement velocity (first 15-30 min) is decisive.** Post into a live
  window and reply to early engagers fast — author replies are heavily weighted.
* **X demotes external links in the post (50-90% reach cut), 2+ hashtags, and
  engagement-bait.** So the link and the longer explainer go in the author's
  *first reply*, never the main post.
* **A strong line-1 hook + an attached visual** (the story card qualifies) carry
  reach; keep the post tight (< 280).

So a post is two pieces: a tight, debate-driving main caption, and an author
first-reply that carries the plain-language gloss and any link. This module builds
both in that shape and audits a caption for what X demotes — enforced by
construction, never by faking or begging for engagement.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from padres_analytics.detect.angles import lead_gloss

if TYPE_CHECKING:
    from padres_analytics.detect.angles import StoryAngle

MAX_LEN = 270  # headroom under X's 280 so nothing truncates and reach isn't clipped
MAX_HASHTAGS = 1  # 2026: 2+ hashtags trip spam filters and cut reach; 0-1 only

# Behavioral levers the caption can't control but the poster should — surfaced with
# every briefing so the highest-weighted signals (velocity, author replies) get used.
POSTING_TIPS = (
    "Post into a live window (weekday midday/early-evening ET) — the first 15-30 min "
    "of engagement is the single biggest reach signal.",
    "Reply to your earliest engagers within minutes — author replies are heavily "
    "weighted and deepen the conversation the algorithm rewards.",
    "Put any link (methodology, source) in your FIRST REPLY, never the post — links "
    "in-post can cut reach 50-90%.",
    "The story card counts as the visual; a post with media out-reaches text-only.",
)

# Phrases X demotes as engagement-bait — never beg for the interaction.
_BAIT = (
    "like if",
    "retweet if",
    "rt if",
    "follow for",
    "like and retweet",
    "smash that",
    "tag a friend",
    "drop a follow",
    "comment below if",
)
# Tells that read as AI-written — off-brand for a human analyst voice.
_AI_TELLS = (
    "delve",
    "in the realm of",
    "it's worth noting",
    "a testament to",
    "tapestry",
    "navigating the",
    "in today's fast-paced",
)
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)


def reply_hook(angle: StoryAngle) -> str:
    """A genuine debate question grounded in the story — the top engagement signal.

    Not bait: an honest either/or the audience can actually argue, matched to the
    angle's claim and direction. Replies are weighted far above likes, so the post
    earns its reach by starting a real conversation.
    """
    up = angle.direction == "up"
    hooks = {
        "pitcher_luck": "Regression coming, or does he keep getting away with it?",
        "player_luck": "Due for the bounce, or is this just who he is now?"
        if up
        else "Riding his luck, or has he genuinely leveled up?",
        "team_luck": "Better days coming, or is this the team's real level?",
        "change": "Real leap, or a hot streak that cools?"
        if up
        else "Just a slump, or has the league adjusted to him?",
        "contact_change": "Found something at the plate, or small-sample noise?"
        if up
        else "Lost the barrel, or noise that evens out?",
        "league_control": "Him, or is the whole league hot right now? You buying it?",
    }
    return hooks.get(angle.key, "What do you see here?")


def build_caption(angle: StoryAngle) -> str:
    """The main post: a line-1 verdict hook + a reply-driving question, kept tight.

    Uses only the angle's own (audited) numbers, no link, no jargon left unglossed-
    elsewhere — the explainer and any link ride in :func:`first_reply`. A draft for
    the caption skill to sharpen; this guarantees the engagement shape.
    """
    return f"{angle.headline} {reply_hook(angle)} ({angle.confidence} confidence)"


def first_reply(angle: StoryAngle) -> str | None:
    """The author's first reply: the plain-language gloss (and where a link belongs).

    Carries the accessibility + provenance that would cost the main post reach if
    inlined, while giving the author a reason to self-reply (a weighted signal).
    Returns ``None`` when the card has no jargon to translate.
    """
    gloss = lead_gloss(angle)
    if gloss is None:
        return None
    return f"In plain terms: {gloss} (Full methodology + glossary linked here.)"


def caption_audit(text: str) -> list[str]:
    """Flag the patterns X demotes (and the brand bans). Empty means it's clean.

    A lint for the main post: it does not rewrite, it surfaces what would cost reach
    or read as off-brand so a human can fix it before posting.
    """
    out: list[str] = []
    low = text.lower()
    if "?" not in text:
        out.append("no reply hook — end on a real question (replies far outweigh likes)")
    if _URL.search(text):
        out.append("link in the post — move it to your first reply (in-post links cut reach)")
    if text.count("#") > MAX_HASHTAGS:
        out.append(f"more than {MAX_HASHTAGS} hashtag — 2+ trips spam filters and cuts reach")
    for phrase in _BAIT:
        if phrase in low:
            out.append(f"engagement-bait '{phrase}' — X demotes begging for interactions")
            break
    for tell in _AI_TELLS:
        if tell in low:
            out.append(f"AI-tell '{tell}' — off-brand for a human analyst voice")
            break
    if len(text) > MAX_LEN:
        out.append(f"{len(text)} chars — over {MAX_LEN}; tighten so reach isn't clipped")
    return out
