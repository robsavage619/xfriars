"""Live (in-game) snapshot from the MLB GUMBO feed.

The first slice of the live path: resolve the Padres' current game, parse the
GUMBO ``feed/live`` payload, and surface the last pitch + the batter's line so
far tonight. Read-only — no tables are written here; the persistence/poller
layer comes later.

Everything here is **unofficial and preliminary**: pitch types are auto-classified
and exit-velocity is revised after the fact, so a live answer must be stamped as
such and never archived as truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from padres_analytics.config import PADRES_TEAM_ID

if TYPE_CHECKING:
    from padres_analytics.ingest.mlb_api import MlbStatsClient

# Resolution priority when a team has more than one game on the date.
_STATE_RANK = {"Live": 0, "Preview": 1, "Final": 2}


@dataclass(frozen=True)
class LivePitch:
    """The most recent pitch of the current at-bat."""

    pitcher: str
    batter: str
    pitch_type: str | None  # e.g. "Slider"
    velo: float | None  # mph (startSpeed)
    result: str | None  # e.g. "Swinging Strike", "Ball", "In play, out(s)"
    balls: int
    strikes: int
    outs: int

    def describe(self) -> str:
        """One-line, human-readable pitch summary."""
        bits = []
        if self.velo is not None and self.pitch_type:
            bits.append(f"{self.velo:.1f} mph {self.pitch_type}")
        elif self.pitch_type:
            bits.append(self.pitch_type)
        if self.result:
            bits.append(self.result)
        head = ", ".join(bits) if bits else "pitch"
        return f"{head} ({self.balls}-{self.strikes}, {self.outs} out)"


@dataclass(frozen=True)
class BatterLine:
    """A batter's box-score line in the current game."""

    name: str
    ab: int
    h: int
    hr: int
    bb: int
    k: int
    rbi: int

    def line(self) -> str:
        """Compact line, e.g. ``2-for-3, HR, RBI``."""
        out = [f"{self.h}-for-{self.ab}"]
        if self.hr:
            out.append(f"{self.hr} HR" if self.hr > 1 else "HR")
        if self.rbi:
            out.append(f"{self.rbi} RBI")
        if self.bb:
            out.append(f"{self.bb} BB")
        if self.k:
            out.append(f"{self.k} K")
        return ", ".join(out)


@dataclass(frozen=True)
class LiveSnapshot:
    """A point-in-time read of a game."""

    game_pk: int
    state: str  # "Live" | "Preview" | "Final" | "Unknown"
    detail: str  # detailedState, e.g. "In Progress", "Warmup", "Final"
    away_abbr: str
    home_abbr: str
    away_score: int | None
    home_score: int | None
    inning: int | None
    half: str | None  # "Top" | "Bottom"
    last_pitch: LivePitch | None
    batter_line: BatterLine | None
    as_of: str | None  # feed metaData timeStamp

    @property
    def is_live(self) -> bool:
        """True when the game is in progress."""
        return self.state == "Live"

    def scoreline(self) -> str:
        """Compact scoreboard string, e.g. ``LAD 5 @ SD 3``."""
        a = self.away_score if self.away_score is not None else 0
        h = self.home_score if self.home_score is not None else 0
        return f"{self.away_abbr} {a} @ {self.home_abbr} {h}"


@dataclass(frozen=True)
class PitchRow:
    """One pitch with context, ready to persist to ``live_pitches``."""

    game_pk: int
    at_bat_index: int
    pitch_number: int
    inning: int | None
    half: str | None
    pitcher_id: int | None
    pitcher: str
    batter_id: int | None
    batter: str
    pitch_type: str | None
    pitch_code: str | None
    velo: float | None
    result: str | None
    is_swing: bool
    is_whiff: bool
    in_play: bool
    balls: int | None
    strikes: int | None


def _name(person: dict[str, Any] | None) -> str:
    return (person or {}).get("fullName", "") if person else ""


def _is_swing(result: str | None, details: dict[str, Any]) -> bool:
    """Did the batter offer at the pitch?"""
    if details.get("isInPlay"):
        return True
    r = result or ""
    return any(tag in r for tag in ("Swinging", "Foul", "In play", "Missed Bunt"))


def _is_whiff(result: str | None) -> bool:
    """A swing and miss (not a foul or a ball in play)."""
    return "Swinging Strike" in (result or "")


def iter_pitches(feed: dict[str, Any]) -> list[PitchRow]:
    """Extract every pitch in the game from a GUMBO feed (cumulative each poll).

    Walks ``liveData.plays.allPlays`` so re-running on a later poll simply yields
    a superset — the persistence layer upserts idempotently on the primary key.
    """
    game = feed.get("gameData", {}) or {}
    game_pk = int(game.get("game", {}).get("pk", 0) or feed.get("gamePk", 0) or 0)
    plays = ((feed.get("liveData", {}) or {}).get("plays", {}) or {}).get("allPlays", []) or []
    rows: list[PitchRow] = []
    for play in plays:
        about = play.get("about", {}) or {}
        matchup = play.get("matchup", {}) or {}
        pitcher = matchup.get("pitcher") or {}
        batter = matchup.get("batter") or {}
        at_bat = play.get("atBatIndex")
        if at_bat is None:
            continue
        for event in play.get("playEvents", []) or []:
            if not event.get("isPitch"):
                continue
            details = event.get("details", {}) or {}
            pitch_data = event.get("pitchData", {}) or {}
            count = event.get("count", {}) or {}
            result = details.get("description")
            rows.append(
                PitchRow(
                    game_pk=game_pk,
                    at_bat_index=int(at_bat),
                    pitch_number=int(event.get("pitchNumber", 0) or 0),
                    inning=about.get("inning"),
                    half=about.get("halfInning"),
                    pitcher_id=pitcher.get("id"),
                    pitcher=_name(pitcher),
                    batter_id=batter.get("id"),
                    batter=_name(batter),
                    pitch_type=(details.get("type") or {}).get("description"),
                    pitch_code=(details.get("type") or {}).get("code"),
                    velo=pitch_data.get("startSpeed"),
                    result=result,
                    is_swing=_is_swing(result, details),
                    is_whiff=_is_whiff(result),
                    in_play=bool(details.get("isInPlay")) or (result or "").startswith("In play"),
                    balls=count.get("balls"),
                    strikes=count.get("strikes"),
                )
            )
    return rows


def resolve_game_pk(
    client: MlbStatsClient, date: str, *, team_id: int = PADRES_TEAM_ID
) -> int | None:
    """Return the most relevant game's pk for the date, or ``None``."""
    game = pick_game(client.live_games(date, team_id=team_id))
    return int(game["game_pk"]) if game else None


def pick_game(games: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the most relevant game: live first, else upcoming, else most recent final.

    Args:
        games: Output of ``MlbStatsClient.live_games``.

    Returns:
        The chosen game dict, or ``None`` if the list is empty.
    """
    if not games:
        return None
    return min(
        games,
        key=lambda g: (_STATE_RANK.get(g.get("abstract_state", ""), 3), g.get("game_datetime", "")),
    )


def parse_feed(feed: dict[str, Any]) -> LiveSnapshot:
    """Parse a GUMBO ``feed/live`` payload into a :class:`LiveSnapshot`.

    Defensive against missing keys: a Preview game (no plays yet) yields a
    snapshot with ``last_pitch`` and ``batter_line`` set to ``None``.
    """
    game = feed.get("gameData", {}) or {}
    live = feed.get("liveData", {}) or {}
    status = game.get("status", {}) or {}
    teams = game.get("teams", {}) or {}
    linescore = live.get("linescore", {}) or {}
    ls_teams = linescore.get("teams", {}) or {}
    plays = live.get("plays", {}) or {}
    current = plays.get("currentPlay", {}) or {}
    matchup = current.get("matchup", {}) or {}
    count = current.get("count", {}) or {}

    last_pitch: LivePitch | None = None
    for event in reversed(current.get("playEvents", []) or []):
        if not event.get("isPitch"):
            continue
        pitch_data = event.get("pitchData", {}) or {}
        details = event.get("details", {}) or {}
        last_pitch = LivePitch(
            pitcher=_name(matchup.get("pitcher")),
            batter=_name(matchup.get("batter")),
            pitch_type=(details.get("type") or {}).get("description"),
            velo=pitch_data.get("startSpeed"),
            result=details.get("description"),
            balls=int(count.get("balls", 0) or 0),
            strikes=int(count.get("strikes", 0) or 0),
            outs=int(count.get("outs", 0) or 0),
        )
        break

    batter_line = _batter_line(live, matchup)

    return LiveSnapshot(
        game_pk=int(game.get("game", {}).get("pk", 0) or feed.get("gamePk", 0) or 0),
        state=status.get("abstractGameState", "Unknown"),
        detail=status.get("detailedState", ""),
        away_abbr=(teams.get("away", {}) or {}).get("abbreviation", "AWY"),
        home_abbr=(teams.get("home", {}) or {}).get("abbreviation", "HOM"),
        away_score=(ls_teams.get("away") or {}).get("runs"),
        home_score=(ls_teams.get("home") or {}).get("runs"),
        inning=linescore.get("currentInning"),
        half=linescore.get("inningHalf"),
        last_pitch=last_pitch,
        batter_line=batter_line,
        as_of=(feed.get("metaData", {}) or {}).get("timeStamp"),
    )


def _batter_line(live: dict[str, Any], matchup: dict[str, Any]) -> BatterLine | None:
    batter = matchup.get("batter") or {}
    bid = batter.get("id")
    if not bid:
        return None
    boxscore = (live.get("boxscore", {}) or {}).get("teams", {}) or {}
    for side in ("home", "away"):
        players = (boxscore.get(side, {}) or {}).get("players", {}) or {}
        player = players.get(f"ID{bid}")
        if not player:
            continue
        batting = (player.get("stats", {}) or {}).get("batting", {}) or {}
        if not batting:
            continue
        return BatterLine(
            name=_name(batter),
            ab=int(batting.get("atBats", 0) or 0),
            h=int(batting.get("hits", 0) or 0),
            hr=int(batting.get("homeRuns", 0) or 0),
            bb=int(batting.get("baseOnBalls", 0) or 0),
            k=int(batting.get("strikeOuts", 0) or 0),
            rbi=int(batting.get("rbi", 0) or 0),
        )
    return None


def current_snapshot(
    client: MlbStatsClient, date: str, *, team_id: int = PADRES_TEAM_ID
) -> LiveSnapshot | None:
    """Resolve the team's game for ``date`` and return a live snapshot.

    Args:
        client: An open MLB Stats client.
        date: ISO date (YYYY-MM-DD) to resolve the game on.
        team_id: MLB team ID.

    Returns:
        A :class:`LiveSnapshot`, or ``None`` if there is no game on the date.
    """
    game = pick_game(client.live_games(date, team_id=team_id))
    if game is None:
        return None
    return parse_feed(client.live_feed(int(game["game_pk"])))
