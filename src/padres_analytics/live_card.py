"""Live (in-game) story card: the GUMBO feed as an editorial-light infographic.

Features the **Padres' own pitcher** — identified by team, not by who has thrown
the most pitches (that's usually the opponent while the Padres are batting). The
card shows tonight's line (IP/H/R/K/BB), a "stuff" stat strip, and the pitch mix
with per-pitch whiffs, framed against the live game situation.

Everything here is **unofficial and preliminary**: pitch types are
auto-classified and velocities are revised after the game, so the card always
carries a ``live · unofficial`` caveat and a live-feed source stamp.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from padres_analytics.config import PADRES_TEAM_ID
from padres_analytics.detect.angles import PanelSpec, Stat, StoryAngle
from padres_analytics.live import iter_pitches, parse_feed
from padres_analytics.render.story_infographic import render_angle
from padres_analytics.render.tokens import GOLD, INK


def _padres_side(feed: dict[str, Any], team_id: int) -> str | None:
    """Return 'home'/'away' for the Padres, or None if they aren't in this game."""
    teams = (feed.get("gameData", {}) or {}).get("teams", {}) or {}
    for side in ("home", "away"):
        if (teams.get(side, {}) or {}).get("id") == team_id:
            return side
    return None


def _opponent_abbr(feed: dict[str, Any], padres_side: str) -> str:
    other = "away" if padres_side == "home" else "home"
    teams = (feed.get("gameData", {}) or {}).get("teams", {}) or {}
    return (teams.get(other, {}) or {}).get("abbreviation", "OPP")


def _i(stats: dict[str, Any], key: str) -> int:
    try:
        return int(stats.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _padres_pitcher(
    feed: dict[str, Any], side: str, pitches: list
) -> tuple[int, str, dict[str, Any]] | None:
    """The Padres pitcher who has thrown the most tonight: (id, name, pitching line).

    Returns None if no Padres pitcher has appeared yet.
    """
    box = (((feed.get("liveData", {}) or {}).get("boxscore", {}) or {}).get("teams", {}) or {}).get(
        side, {}
    ) or {}
    pitcher_ids = box.get("pitchers", []) or []
    if not pitcher_ids:
        return None
    players = box.get("players", {}) or {}
    counts = defaultdict(int)
    for p in pitches:
        counts[p.pitcher_id] += 1
    best_id = max(pitcher_ids, key=lambda pid: counts.get(pid, 0))
    if counts.get(best_id, 0) == 0:
        return None
    player = players.get(f"ID{best_id}", {}) or {}
    name = (player.get("person", {}) or {}).get("fullName", "the starter")
    line = (player.get("stats", {}) or {}).get("pitching", {}) or {}
    return int(best_id), name, line


def live_angle(
    feed: dict[str, Any], *, team_id: int = PADRES_TEAM_ID, as_of: date | None = None
) -> StoryAngle | None:
    """Build a live story angle for the Padres' starter.

    Args:
        feed: A GUMBO ``feed/live`` payload.
        team_id: The Padres' MLB team id (overridable for tests).
        as_of: Card date; defaults to today.

    Returns:
        A :class:`StoryAngle`, or ``None`` if the Padres aren't in this game or
        their pitcher hasn't thrown yet.
    """
    side = _padres_side(feed, team_id)
    if side is None:
        return None
    pitches = iter_pitches(feed)
    found = _padres_pitcher(feed, side, pitches)
    if found is None:
        return None
    pid, name, line = found

    mine = [p for p in pitches if p.pitcher_id == pid]
    n = len(mine)
    whiffs = sum(1 for p in mine if p.is_whiff)
    called = sum(1 for p in mine if (p.result or "") == "Called Strike")
    csw = round(100 * (called + whiffs) / n) if n else 0  # called strikes + whiffs %

    counts: dict[str, int] = defaultdict(int)
    whiff_by: dict[str, int] = defaultdict(int)
    velos: dict[str, list[float]] = defaultdict(list)
    for p in mine:
        ptype = p.pitch_type or "Unknown"
        counts[ptype] += 1
        whiff_by[ptype] += int(p.is_whiff)
        if p.velo is not None:
            velos[ptype].append(p.velo)

    def _avg_velo(vs: list[float]) -> float:
        return sum(vs) / len(vs) if vs else 0.0

    # Pitch-mix rows: (label, count, "velo · Nw", swinging-strike rate) — color encodes whiff rate.
    mix = sorted(
        (
            (
                ptype,
                float(cnt),
                f"{_avg_velo(velos[ptype]):.0f} · {whiff_by[ptype]}w"
                if velos[ptype]
                else f"{whiff_by[ptype]}w",
                (whiff_by[ptype] / cnt if cnt else 0.0),
            )
            for ptype, cnt in counts.items()
        ),
        key=lambda row: row[1],
        reverse=True,
    )
    # Fastball velocity over the game (chronological) — is he holding or fading?
    fastballs = [
        p.velo for p in mine if p.velo and p.pitch_type in ("Four-Seam Fastball", "Sinker")
    ]

    snap = parse_feed(feed)
    opp = _opponent_abbr(feed, side)
    situation = f" · {snap.half} {snap.inning}" if (snap.inning and snap.half) else ""
    ip = str(line.get("inningsPitched", "0.0"))
    k, h, r, bb = (
        _i(line, "strikeOuts"),
        _i(line, "hits"),
        _i(line, "runs"),
        _i(line, "baseOnBalls"),
    )
    w_word = "whiff" if whiffs == 1 else "whiffs"

    return StoryAngle(
        key="live_pitcher",
        subject=f"{name} · vs {opp}{situation}",
        title="DEALING" if csw >= 30 else "ON THE BUMP",
        headline=f"{csw}% CSW on {n} pitches, {whiffs} {w_word} — {ip} IP, {k} K.",
        thesis="A live look at the Padres starter's stuff: CSW%, pitch mix, and velocity.",
        direction="up",
        effect=float(csw),
        reliability=0.5,
        interest=float(csw),
        confidence="moderate",
        as_of=as_of or date.today(),
        panels=[
            PanelSpec(
                "hero",
                {
                    "value": f"{csw}%",
                    "label": "CSW RATE",
                    "context": f"{n} pitches · {whiffs} {w_word} · {called} called",
                    "accent": GOLD if csw >= 30 else INK,
                },
            ),
            PanelSpec(
                "pitchmix",
                {
                    "rows": mix,
                    "title": "PITCH MIX",
                    "right": "usage · velo · whiffs (color = SwStr%)",
                },
            ),
            PanelSpec(
                "trend",
                {"values": fastballs, "title": "FASTBALL VELOCITY", "right": "mph, first → last"},
            ),
            PanelSpec(
                "statline",
                {
                    "title": "TONIGHT'S LINE",
                    "blocks": [
                        ("IP", ip),
                        ("H", str(h)),
                        ("R", str(r)),
                        ("K", str(k)),
                        ("BB", str(bb)),
                    ],
                },
            ),
        ],
        stats=[
            Stat("csw", csw, "pct", "CSW rate", n, shown=True),
            Stat("pitches", n, "count", "pitches", n, shown=True),
            Stat("whiffs", whiffs, "count", "whiffs", n, shown=True),
            Stat("k", k, "count", "strikeouts", n, shown=True),
        ],
        caveats=["live · unofficial — preliminary, revised after the game"],
        source="MLB GUMBO feed (live)",
    )


def render_live_card(
    feed: dict[str, Any], out_dir: Path, stem: str, *, team_id: int = PADRES_TEAM_ID
) -> Path | None:
    """Render the live story card to a PNG, or return None if there's nothing to show."""
    angle = live_angle(feed, team_id=team_id)
    if angle is None:
        return None
    return render_angle(angle, out_dir, stem)
