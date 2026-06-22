"""Ask the engine about the live game in plain language.

Deterministic intent parsing (no LLM in the loop, so it's testable and free):
resolve a player named in the question against the players actually in tonight's
game, decide pitcher-vs-batter from context, and answer from the current feed.
Live numbers are computed straight from the feed, so this works whether or not
``pad live watch`` is running.

Every answer is stamped unofficial — pitch types/velo are preliminary.
"""

from __future__ import annotations

from typing import Any

from padres_analytics.config import PADRES_TEAM_ID
from padres_analytics.live import BatterLine, _batter_line, iter_pitches, parse_feed

_PITCHER_WORDS = ("pitch", "throw", "velo", "stuff", "arm", "mound", "fastball", "slider")
_BATTER_WORDS = ("hit", "plate", "bat", "line", "swing", "at-bat", "ab", "knock")
_MATCHUP_WORDS = ("against", "vs", "facing")
_RISP_WORDS = ("risp", "runners in scoring", "with runners")


def participants(feed: dict[str, Any]) -> list[tuple[int, str]]:
    """Every player in the game's boxscore as ``(id, full_name)``."""
    box = ((feed.get("liveData", {}) or {}).get("boxscore", {}) or {}).get("teams", {}) or {}
    out: list[tuple[int, str]] = []
    for side in ("home", "away"):
        players = (box.get(side, {}) or {}).get("players", {}) or {}
        for player in players.values():
            person = player.get("person", {}) or {}
            if person.get("id"):
                out.append((int(person["id"]), person.get("fullName", "")))
    return out


def match_player(question: str, people: list[tuple[int, str]]) -> int | None:
    """Resolve a player id from a question by matching a name token.

    Prefers a full-name hit; falls back to a unique last-name hit. Returns
    ``None`` if nothing matches or a last name is ambiguous.
    """
    q = question.lower()
    for pid, name in people:
        if name and name.lower() in q:
            return pid
    last_hits = [pid for pid, name in people if name and name.split()[-1].lower() in q.split()]
    return last_hits[0] if len(last_hits) == 1 else None


def _pitcher_summary(feed: dict[str, Any], pid: int, name: str) -> str | None:
    pitches = [p for p in iter_pitches(feed) if p.pitcher_id == pid]
    if not pitches:
        return None
    by_type: dict[str, list[float]] = {}
    whiffs = 0
    for p in pitches:
        by_type.setdefault(p.pitch_type or "?", []).append(p.velo if p.velo is not None else 0.0)
        whiffs += int(p.is_whiff)
    parts = []
    for ptype, velos in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        real = [v for v in velos if v]
        avg = f" ({sum(real) / len(real):.1f})" if real else ""
        parts.append(f"{ptype} {len(velos)}{avg}")
    return f"{name} tonight: {len(pitches)} pitches, {whiffs} whiff(s) — " + ", ".join(parts)


def _batter_summary(feed: dict[str, Any], pid: int, name: str) -> str | None:
    line = _line_for(feed, pid)
    seen = sum(1 for p in iter_pitches(feed) if p.batter_id == pid)
    if line is None and not seen:
        return None
    tail = f" on {seen} pitches" if seen else ""
    body = line.line() if line else "no plate appearance yet"
    return f"{name} tonight: {body}{tail}"


def _line_for(feed: dict[str, Any], pid: int) -> BatterLine | None:
    return _batter_line(feed.get("liveData", {}) or {}, {"batter": {"id": pid}})


def _name_of(people: list[tuple[int, str]], pid: int) -> str:
    return next((n for i, n in people if i == pid), "")


def answer_from_feed(question: str, feed: dict[str, Any]) -> str:
    """Answer a plain-language question from a GUMBO feed payload."""
    snap = parse_feed(feed)
    people = participants(feed)
    pid = match_player(question, people)
    stamp = "  ·  live · unofficial"

    if pid is None:
        where = f"{snap.half} {snap.inning}" if snap.inning else snap.detail
        return f"{snap.scoreline()} — {where}.{stamp}"

    name = _name_of(people, pid)
    q = question.lower()
    wants_pitcher = any(w in q for w in _PITCHER_WORDS)
    wants_batter = any(w in q for w in _BATTER_WORDS)
    pitcher = _pitcher_summary(feed, pid, name)
    batter = _batter_summary(feed, pid, name)

    # Prefer the role the question asks about; else whichever has data.
    order = [batter, pitcher] if wants_batter and not wants_pitcher else [pitcher, batter]
    for candidate in order:
        if candidate:
            return candidate + stamp
    return f"{name} isn't in tonight's game yet.{stamp}"


def _roles(feed: dict[str, Any]) -> tuple[set[int], set[int]]:
    """Return ``(pitcher_ids, batter_ids)`` seen in the feed's plays."""
    pitchers: set[int] = set()
    batters: set[int] = set()
    plays = ((feed.get("liveData", {}) or {}).get("plays", {}) or {}).get("allPlays", []) or []
    for play in plays:
        matchup = play.get("matchup", {}) or {}
        pid = (matchup.get("pitcher") or {}).get("id")
        bid = (matchup.get("batter") or {}).get("id")
        if pid:
            pitchers.add(int(pid))
        if bid:
            batters.add(int(bid))
    return pitchers, batters


def _resolve_matchup(question: str, feed: dict[str, Any]) -> tuple[int, str, int, str] | None:
    """Resolve a ``(batter_id, batter_name, pitcher_id, pitcher_name)`` matchup.

    Matches up to two names from the question against the players in the game,
    then assigns batter/pitcher roles from the feed's play-by-play. Returns
    ``None`` if two distinct players in opposing roles can't be identified.
    """
    people = participants(feed)
    pitchers, batters = _roles(feed)
    q = question.lower()

    hits: list[tuple[int, str]] = []
    for pid, name in people:
        if name and name.lower() in q and pid not in {h[0] for h in hits}:
            hits.append((pid, name))
    if len(hits) < 2:
        last_hits = [
            (pid, name) for pid, name in people if name and name.split()[-1].lower() in q.split()
        ]
        for pid, name in last_hits:
            if pid not in {h[0] for h in hits}:
                hits.append((pid, name))

    batter: tuple[int, str] | None = None
    pitcher: tuple[int, str] | None = None
    for pid, name in hits:
        if pitcher is None and pid in pitchers:
            pitcher = (pid, name)
        elif batter is None and pid in batters:
            batter = (pid, name)
    if batter is None or pitcher is None or batter[0] == pitcher[0]:
        return None
    return batter[0], batter[1], pitcher[0], pitcher[1]


def _format_matchup(bname: str, pname: str, line: dict[str, Any], season: int) -> str:
    """Format a ``vs_pitcher`` line into a one-line answer."""
    stamp = "  ·  live · unofficial"
    parts = [f"{line['h']}-for-{line['ab']}"]
    if line.get("hr"):
        parts.append(f"{line['hr']} HR")
    if line.get("bb"):
        parts.append(f"{line['bb']} BB")
    if line.get("k"):
        parts.append(f"{line['k']} K")
    return f"{bname} vs {pname}: {', '.join(parts)} ({season}){stamp}"


def answer_with_client(question: str, feed: dict[str, Any], client: Any, season: int) -> str:
    """Answer a question, reaching to the API for matchup/RISP intents.

    Pure feed-only intents are delegated to :func:`answer_from_feed`.

    Args:
        question: Plain-language question.
        feed: GUMBO feed payload.
        client: An ``MlbStatsClient`` (or any object exposing ``vs_pitcher``
            and ``team_risp``).
        season: 4-digit season year for the network lookups.

    Returns:
        A one-line, stamped answer.
    """
    stamp = "  ·  live · unofficial"
    q = question.lower()

    if any(w in q for w in _RISP_WORDS):
        risp = client.team_risp(PADRES_TEAM_ID, season)
        if not risp:
            return f"Padres RISP splits aren't available yet.{stamp}"
        return f"Padres with RISP: {risp['avg']} ({risp['h']}-for-{risp['ab']}), {season}{stamp}"

    if any(w in q for w in _MATCHUP_WORDS):
        resolved = _resolve_matchup(question, feed)
        if resolved is not None:
            bid, bname, pid, pname = resolved
            line = client.vs_pitcher(bid, pid, season)
            if not line:
                return f"No prior matchup data for {bname} vs {pname}.{stamp}"
            return _format_matchup(bname, pname, line, season)

    return answer_from_feed(question, feed)


def answer(question: str, date: str) -> str:
    """Fetch the Padres' current game and answer ``question``.

    Args:
        question: Plain-language question.
        date: ISO date (YYYY-MM-DD) to resolve the game on.

    Returns:
        A one-line answer, or a message if there is no game.
    """
    from padres_analytics.ingest.mlb_api import MlbStatsClient
    from padres_analytics.live import resolve_game_pk

    season = int(date[:4])
    with MlbStatsClient() as client:
        game_pk = resolve_game_pk(client, date)
        if game_pk is None:
            return f"No Padres game found on {date}."
        feed = client.live_feed(game_pk)
        return answer_with_client(question, feed, client, season)
