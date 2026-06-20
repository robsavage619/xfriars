"""Live moment discovery — pick the card worth showing *right now*.

Brings the season engine's discipline to the live side: instead of always
rendering the starter, each moment detector is **gated** (it returns ``None``
until the moment is genuinely card-worthy) and carries an *interest* score, so
``discover_live`` ranks them and the strongest moment wins. The live card is now
one of several archetypes (a dominant starter, a Padre's big night at the plate),
selected — not hardcoded.

Everything stays unofficial/preliminary; numbers come from the live feed and are
audited like any other card.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from padres_analytics.config import PADRES_TEAM_ID
from padres_analytics.detect.angles import PanelSpec, Stat, StoryAngle
from padres_analytics.live import parse_feed
from padres_analytics.live_card import (
    _i,
    _opponent_abbr,
    _padres_side,
    headshot_data_uri,
    live_angle,
)
from padres_analytics.render.story_infographic import render_angle

# Gates — a moment must clear these to be worth a card.
_PITCHER_MIN_PITCHES = 25
_PITCHER_DOMINANT_CSW = 30  # % CSW that says "dealing"
_PITCHER_DOMINANT_K = 7

ResolverT = Any  # callable pid -> data: URI | None


def pitcher_moment(
    feed: dict[str, Any], *, team_id: int = PADRES_TEAM_ID, photo_resolver: ResolverT = None
) -> StoryAngle | None:
    """The Padres starter — but only once the outing is genuinely dominant."""
    angle = live_angle(feed, team_id=team_id, photo_resolver=photo_resolver)
    if angle is None:
        return None
    s = {st.key: int(st.value) for st in angle.stats}
    pitches, csw, k = s.get("pitches", 0), s.get("csw", 0), s.get("k", 0)
    if pitches < _PITCHER_MIN_PITCHES:
        return None  # too early to editorialize
    if csw < _PITCHER_DOMINANT_CSW and k < _PITCHER_DOMINANT_K:
        return None  # solid != card-worthy
    return replace(angle, interest=float(csw + 4 * k))


def _hardest_hit(feed: dict[str, Any], batter_id: int) -> float | None:
    """The batter's top exit velocity tonight (mph), or None."""
    plays = ((feed.get("liveData", {}) or {}).get("plays", {}) or {}).get("allPlays", []) or []
    best: float | None = None
    for play in plays:
        if ((play.get("matchup", {}) or {}).get("batter", {}) or {}).get("id") != batter_id:
            continue
        for event in play.get("playEvents", []) or []:
            speed = (event.get("hitData", {}) or {}).get("launchSpeed")
            if speed and (best is None or speed > best):
                best = float(speed)
    return best


def _best_padres_hitter(
    feed: dict[str, Any], side: str
) -> tuple[float, int, str, dict[str, Any]] | None:
    """The Padre with the best night at the plate, gated to a real game."""
    box = (((feed.get("liveData", {}) or {}).get("boxscore", {}) or {}).get("teams", {}) or {}).get(
        side, {}
    ) or {}
    best: tuple[float, int, str, dict[str, Any]] | None = None
    for player in (box.get("players", {}) or {}).values():
        batting = (player.get("stats", {}) or {}).get("batting", {}) or {}
        if not batting:
            continue
        h, hr, rbi = _i(batting, "hits"), _i(batting, "homeRuns"), _i(batting, "rbi")
        if not (hr >= 1 or h >= 2 or rbi >= 3):
            continue  # gate: nothing card-worthy yet
        score = hr * 50 + h * 12 + rbi * 8  # a homer is the headline event
        if best is None or score > best[0]:
            pid = (player.get("person", {}) or {}).get("id")
            name = (player.get("person", {}) or {}).get("fullName", "the hitter")
            if pid is not None:
                best = (float(score), int(pid), name, batting)
    return best


def hitter_moment(
    feed: dict[str, Any], *, team_id: int = PADRES_TEAM_ID, photo_resolver: ResolverT = None
) -> StoryAngle | None:
    """A Padre's standout night at the plate (multi-hit, homer, or big RBI)."""
    side = _padres_side(feed, team_id)
    if side is None:
        return None
    best = _best_padres_hitter(feed, side)
    if best is None:
        return None
    score, pid, name, b = best
    ab, h, hr = _i(b, "atBats"), _i(b, "hits"), _i(b, "homeRuns")
    rbi, bb, k = _i(b, "rbi"), _i(b, "baseOnBalls"), _i(b, "strikeOuts")

    snap = parse_feed(feed)
    opp = _opponent_abbr(feed, side)
    situation = f" · {snap.half} {snap.inning}" if (snap.inning and snap.half) else ""
    ev = _hardest_hit(feed, pid)

    if hr >= 2:
        title = "MULTI-HOMER"
    elif hr == 1:
        title = "WENT DEEP"
    elif h >= 3:
        title = "ON A TEAR"
    else:
        title = "LOCKED IN"

    extras = ""
    if hr:
        extras += f", {hr} HR"
    if rbi:
        extras += f", {rbi} RBI"
    headline = f"vs {opp}{situation} — {h}-for-{ab}{extras}."

    if hr:
        hero_value, hero_label = str(hr), "HOME RUN" if hr == 1 else "HOME RUNS"
    else:
        hero_value, hero_label = str(h), "HITS"
    hero_ctx = f"hardest hit {ev:.0f} mph tonight" if ev else f"{rbi} RBI · {ab} at-bats"

    return StoryAngle(
        key="live_hitter",
        subject=name,
        title=title,
        headline=headline,
        thesis="A live look at a Padre having a night at the plate.",
        direction="up",
        effect=score,
        reliability=0.5,
        interest=score,
        confidence="moderate",
        as_of=date.today(),
        panels=[
            PanelSpec(
                "hero",
                {
                    "value": hero_value,
                    "label": hero_label,
                    "context": hero_ctx,
                    "accent": "#C99A2E",
                },
            ),
            PanelSpec(
                "statline",
                {
                    "title": "TONIGHT'S LINE",
                    "blocks": [
                        ("AB", str(ab)),
                        ("H", str(h)),
                        ("HR", str(hr)),
                        ("RBI", str(rbi)),
                        ("BB", str(bb)),
                        ("K", str(k)),
                    ],
                },
            ),
        ],
        stats=[
            Stat("hits", h, "count", "hits", ab, shown=True),
            Stat("hr", hr, "count", "home runs", ab, shown=True),
            Stat("rbi", rbi, "count", "RBI", ab, shown=True),
            Stat("ab", ab, "count", "at-bats", ab, shown=True),
        ],
        caveats=["live · unofficial — preliminary, revised after the game"],
        source="MLB GUMBO feed (live)",
        headshot=photo_resolver(pid) if photo_resolver else None,
    )


_MOMENTS = (pitcher_moment, hitter_moment)


def discover_live(
    feed: dict[str, Any], *, team_id: int = PADRES_TEAM_ID, photo_resolver: ResolverT = None
) -> list[StoryAngle]:
    """Run every live-moment detector and return survivors, strongest first."""
    found = [m(feed, team_id=team_id, photo_resolver=photo_resolver) for m in _MOMENTS]
    return sorted((a for a in found if a is not None), key=lambda a: a.interest, reverse=True)


def render_live_moment(
    feed: dict[str, Any], out_dir: Path, stem: str, *, team_id: int = PADRES_TEAM_ID
) -> Path | None:
    """Render the strongest card-worthy live moment, or None if nothing qualifies yet."""
    angles = discover_live(feed, team_id=team_id, photo_resolver=headshot_data_uri)
    if not angles:
        return None
    return render_angle(angles[0], out_dir, stem)
