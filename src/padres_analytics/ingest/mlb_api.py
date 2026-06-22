"""MLB Stats API client — schedule, roster, season stats, leaderboards, boxscores.

Politeness: 2s delay between requests. All data is used on an unofficial,
non-commercial basis per MLB's API usage policy.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from padres_analytics.config import MLB_STATS_API_BASE, PADRES_TEAM_ID
from padres_analytics.ingest.runs import record_run

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 30.0
POLITENESS_DELAY = 2.0


def innings_to_outs(ip: str) -> int:
    """Convert a baseball innings string to whole outs.

    ``inningsPitched`` is notation, not a decimal: the fraction counts thirds
    of an inning (``.1`` = 1 out, ``.2`` = 2 outs). Naive ``float(ip)`` corrupts
    any rate built on it (FIP, ERA). ``"136.2"`` → ``410`` outs.
    """
    ip = (ip or "").strip()
    if not ip:
        return 0
    whole, _, frac = ip.partition(".")
    try:
        outs = int(whole or 0) * 3
        if frac:
            outs += int(frac[0])  # only the first fractional digit is meaningful (0/1/2)
        return outs
    except ValueError:
        return 0


# Stat categories available from /stats/leaders
HITTING_LEADER_STATS = (
    "homeRuns",
    "battingAverage",
    "onBasePlusSlugging",
    "rbi",
    "stolenBases",
    "hits",
    "runs",
    "onBasePercentage",
    "sluggingPercentage",
    "strikeOuts",
)
PITCHING_LEADER_STATS = (
    "earnedRunAverage",
    "wins",
    "strikeOuts",
    "saves",
    "whip",
    "inningsPitched",
)


class MlbApiError(RuntimeError):
    """Raised when the MLB Stats API returns an error."""


class MlbStatsClient:
    """Thin wrapper around the MLB Stats API.

    Owns an httpx.Client; call .close() or use as a context manager.

    Args:
        base_url: Override for testing. Defaults to the production endpoint.
        politeness_delay: Seconds to sleep between requests.
    """

    def __init__(
        self,
        base_url: str = MLB_STATS_API_BASE,
        politeness_delay: float = POLITENESS_DELAY,
    ) -> None:
        """Initialize the client with base URL and politeness delay."""
        self._base = base_url.rstrip("/")
        # The GUMBO live feed lives on the v1.1 path; everything else is v1.
        self._feed_base = self._base.removesuffix("/v1") + "/v1.1"
        self._delay = politeness_delay
        self._client = httpx.Client(timeout=HTTP_TIMEOUT)
        self._last_request: float = 0.0

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> MlbStatsClient:
        """Return self for context manager use."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close on context manager exit."""
        self.close()

    def _get(self, path: str, base: str | None = None, **params: Any) -> dict[str, Any]:
        """GET a JSON endpoint, respecting the politeness delay.

        Args:
            path: URL path relative to base_url.
            base: Override base URL (e.g. the v1.1 feed host). Defaults to v1.
            **params: Query parameters.

        Returns:
            Parsed JSON response dict.

        Raises:
            MlbApiError: On HTTP error or JSON parse failure.
        """
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)

        url = f"{(base or self._base).rstrip('/')}/{path.lstrip('/')}"
        try:
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except httpx.HTTPStatusError as exc:
            raise MlbApiError(f"HTTP {exc.response.status_code} for {url}") from exc
        except Exception as exc:
            raise MlbApiError(f"Request failed for {url}: {exc}") from exc
        finally:
            self._last_request = time.monotonic()

        return data

    # ── Leaders ───────────────────────────────────────────────────────────

    def leaders(
        self,
        stat_type: str,
        season: int,
        limit: int = 25,
        stat_group: str = "hitting",
        game_type: str = "R",
    ) -> list[dict[str, Any]]:
        """Fetch leaderboard entries for one stat type.

        Args:
            stat_type: e.g. ``"homeRuns"``, ``"battingAverage"``, ``"earnedRunAverage"``.
            season: 4-digit year.
            limit: Max rows to return (1-100).
            stat_group: ``"hitting"`` or ``"pitching"``.
            game_type: ``"R"`` (regular season), ``"P"`` (postseason).

        Returns:
            List of leader dicts with keys: rank, player_id, player_name,
            team_id, team_abbr, value.
        """
        data = self._get(
            "stats/leaders",
            leaderCategories=stat_type,
            season=season,
            limit=limit,
            statGroup=stat_group,
            gameType=game_type,
            hydrate="person,team",
        )
        raw_leaders: list[dict[str, Any]] = data.get("leagueLeaders", [{}])[0].get("leaders", [])
        results = []
        for entry in raw_leaders:
            person = entry.get("person") or {}
            team = entry.get("team") or {}
            results.append(
                {
                    "rank": entry.get("rank"),
                    "player_id": person.get("id"),
                    "player_name": person.get("fullName"),
                    "team_id": team.get("id"),
                    "team_abbr": team.get("abbreviation"),
                    "value": str(entry.get("value", "")),
                }
            )
        logger.info("leaders: %s season=%d returned %d rows", stat_type, season, len(results))
        return results

    # ── Schedule ──────────────────────────────────────────────────────────

    def schedule(
        self,
        team_id: int = PADRES_TEAM_ID,
        season: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        game_type: str = "R",
    ) -> list[dict[str, Any]]:
        """Fetch schedule entries for a team.

        Args:
            team_id: MLB team ID.
            season: 4-digit year (mutually exclusive with start/end date).
            start_date: ISO date string (YYYY-MM-DD).
            end_date: ISO date string (YYYY-MM-DD).
            game_type: Comma-separated game types (``"R"``, ``"F,D,L,W"``).

        Returns:
            List of game dicts with keys: game_pk, game_date, game_type,
            status, home_team_id, away_team_id, home_team_abbr, away_team_abbr,
            venue_id, venue_name.
        """
        params: dict[str, Any] = {
            "sportId": 1,
            "teamId": team_id,
            "gameTypes": game_type,
            "hydrate": "team,venue",
        }
        if season:
            params["season"] = season
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date

        data = self._get("schedule", **params)
        games = []
        for date_entry in data.get("dates", []):
            for g in date_entry.get("games", []):
                home = g.get("teams", {}).get("home", {})
                away = g.get("teams", {}).get("away", {})
                venue = g.get("venue") or {}
                games.append(
                    {
                        "game_pk": g["gamePk"],
                        "game_date": date_entry["date"],
                        "game_type": g.get("gameType", "R"),
                        "status": g.get("status", {}).get("codedGameState"),
                        "home_team_id": home.get("team", {}).get("id"),
                        "away_team_id": away.get("team", {}).get("id"),
                        "home_team_abbr": home.get("team", {}).get("abbreviation"),
                        "away_team_abbr": away.get("team", {}).get("abbreviation"),
                        "venue_id": venue.get("id"),
                        "venue_name": venue.get("name"),
                    }
                )
        logger.info("schedule: team=%d returned %d games", team_id, len(games))
        return games

    def game_scores(
        self,
        team_id: int = PADRES_TEAM_ID,
        season: int | None = None,
        game_type: str = "R",
    ) -> list[dict[str, Any]]:
        """Fetch final-game box scores for a team's season (linescore-hydrated).

        Only games whose ``status.abstractGameState`` is ``"Final"`` are
        returned — scheduled and in-progress games carry no settled score.

        Args:
            team_id: MLB team ID.
            season: 4-digit year. Defaults to current season.
            game_type: Comma-separated game types (default regular season).

        Returns:
            One dict per final game with keys: game_pk, game_date,
            home_team_id, away_team_id, home_score, away_score, innings,
            winning_pitcher_id, losing_pitcher_id, save_pitcher_id.
        """
        params: dict[str, Any] = {
            "sportId": 1,
            "teamId": team_id,
            "gameTypes": game_type,
            "hydrate": "linescore,decisions,team",
        }
        if season:
            params["season"] = season

        data = self._get("schedule", **params)
        games = []
        for date_entry in data.get("dates", []):
            for g in date_entry.get("games", []):
                if (g.get("status") or {}).get("abstractGameState") != "Final":
                    continue
                ls = g.get("linescore") or {}
                ls_teams = ls.get("teams") or {}
                home = g.get("teams", {}).get("home", {})
                away = g.get("teams", {}).get("away", {})
                decisions = g.get("decisions") or {}
                games.append(
                    {
                        "game_pk": g["gamePk"],
                        "game_date": date_entry["date"],
                        "home_team_id": home.get("team", {}).get("id"),
                        "away_team_id": away.get("team", {}).get("id"),
                        "home_score": (ls_teams.get("home") or {}).get("runs"),
                        "away_score": (ls_teams.get("away") or {}).get("runs"),
                        "innings": ls.get("currentInning"),
                        "winning_pitcher_id": (decisions.get("winner") or {}).get("id"),
                        "losing_pitcher_id": (decisions.get("loser") or {}).get("id"),
                        "save_pitcher_id": (decisions.get("save") or {}).get("id"),
                    }
                )
        logger.info("game_scores: team=%d season=%s -> %d finals", team_id, season, len(games))
        return games

    # ── Live (GUMBO) ──────────────────────────────────────────────────────

    def live_games(self, date: str, team_id: int = PADRES_TEAM_ID) -> list[dict[str, Any]]:
        """Fetch a team's games on one date with live state (status, score, inning).

        Args:
            date: ISO date string (YYYY-MM-DD) in the venue's local context.
            team_id: MLB team ID.

        Returns:
            One dict per game with keys: game_pk, abstract_state
            (Preview/Live/Final), detailed_state, game_datetime, home_abbr,
            away_abbr, home_score, away_score, inning, inning_half.
        """
        data = self._get("schedule", sportId=1, teamId=team_id, date=date, hydrate="linescore,team")
        out: list[dict[str, Any]] = []
        for date_entry in data.get("dates", []):
            for g in date_entry.get("games", []):
                ls = g.get("linescore") or {}
                ls_teams = ls.get("teams") or {}
                home = g.get("teams", {}).get("home", {})
                away = g.get("teams", {}).get("away", {})
                status = g.get("status", {})
                out.append(
                    {
                        "game_pk": g["gamePk"],
                        "abstract_state": status.get("abstractGameState"),
                        "detailed_state": status.get("detailedState"),
                        "game_datetime": g.get("gameDate"),
                        "home_abbr": home.get("team", {}).get("abbreviation"),
                        "away_abbr": away.get("team", {}).get("abbreviation"),
                        "home_score": (ls_teams.get("home") or {}).get("runs"),
                        "away_score": (ls_teams.get("away") or {}).get("runs"),
                        "inning": ls.get("currentInning"),
                        "inning_half": ls.get("inningHalf"),
                    }
                )
        return out

    def live_feed(self, game_pk: int) -> dict[str, Any]:
        """Fetch the GUMBO live feed for a game (v1.1) — per-pitch, near-real-time.

        Args:
            game_pk: MLB game id.

        Returns:
            The raw GUMBO payload (gameData + liveData). Parse with
            :func:`padres_analytics.live.parse_feed`.
        """
        return self._get(f"game/{game_pk}/feed/live", base=self._feed_base)

    # ── Matchup & situational splits (live) ───────────────────────────────

    def vs_pitcher(self, batter_id: int, pitcher_id: int, season: int) -> dict[str, Any]:
        """Fetch a batter's career-to-date line against one pitcher for a season.

        Uses the ``vsPlayer`` split, which can return several split rows; the
        counting stats are summed and the average recomputed from the totals.

        Args:
            batter_id: MLBAM id of the hitter.
            pitcher_id: MLBAM id of the opposing pitcher.
            season: 4-digit year.

        Returns:
            Flat dict ``{"ab","h","hr","bb","k","avg"}`` or ``{}`` if no data.
        """
        try:
            data = self._get(
                f"people/{batter_id}/stats",
                stats="vsPlayer",
                group="hitting",
                opposingPlayerId=pitcher_id,
                season=season,
            )
        except MlbApiError as exc:
            logger.error("vs_pitcher %d vs %d failed: %s", batter_id, pitcher_id, exc)
            return {}

        splits = (data.get("stats") or [{}])[0].get("splits") or []
        if not splits:
            return {}

        ab = h = hr = bb = k = 0
        for s in splits:
            st = s.get("stat") or {}
            try:
                ab += int(st.get("atBats", 0) or 0)
                h += int(st.get("hits", 0) or 0)
                hr += int(st.get("homeRuns", 0) or 0)
                bb += int(st.get("baseOnBalls", 0) or 0)
                k += int(st.get("strikeOuts", 0) or 0)
            except (ValueError, TypeError):
                continue

        avg = f"{h / ab:.3f}".lstrip("0") if ab else ".000"
        return {"ab": ab, "h": h, "hr": hr, "bb": bb, "k": k, "avg": avg}

    def team_risp(self, team_id: int, season: int) -> dict[str, Any]:
        """Fetch a team's hitting line with runners in scoring position.

        Args:
            team_id: MLB team id.
            season: 4-digit year.

        Returns:
            Flat dict ``{"avg","ab","h"}`` or ``{}`` if no data.
        """
        try:
            data = self._get(
                f"teams/{team_id}/stats",
                stats="statSplits",
                group="hitting",
                sitCodes="risp",
                season=season,
            )
        except MlbApiError as exc:
            logger.error("team_risp %d failed: %s", team_id, exc)
            return {}

        splits = (data.get("stats") or [{}])[0].get("splits") or []
        if not splits:
            return {}

        st = splits[0].get("stat") or {}
        try:
            ab = int(st.get("atBats", 0) or 0)
            h = int(st.get("hits", 0) or 0)
        except (ValueError, TypeError):
            return {}
        avg = str(st.get("avg") or "") or (f"{h / ab:.3f}".lstrip("0") if ab else ".000")
        return {"avg": avg, "ab": ab, "h": h}

    # ── Season stats ──────────────────────────────────────────────────────

    def season_stats(
        self,
        team_id: int = PADRES_TEAM_ID,
        season: int | None = None,
        group: str = "hitting",
    ) -> list[dict[str, Any]]:
        """Fetch season cumulative stats for all players on a team roster.

        Args:
            team_id: MLB team ID.
            season: 4-digit year. Defaults to current season.
            group: ``"hitting"`` or ``"pitching"``.

        Returns:
            List of player-stat dicts with keys: player_id, player_name,
            team_id, team_abbr, stats_json.
        """
        params: dict[str, Any] = {
            "stats": "season",
            "group": group,
            "teamId": team_id,
            "sportId": 1,
            "hydrate": "person,team",
        }
        if season:
            params["season"] = season

        data = self._get("stats", **params)
        rows = []
        for entry in data.get("stats", [{}])[0].get("splits", []):
            person = entry.get("person") or {}
            team = entry.get("team") or {}
            rows.append(
                {
                    "player_id": person.get("id"),
                    "player_name": person.get("fullName"),
                    "team_id": team.get("id"),
                    "team_abbr": team.get("abbreviation"),
                    "stats_json": entry.get("stat") or {},
                }
            )
        logger.info(
            "season_stats: team=%d group=%s returned %d rows",
            team_id,
            group,
            len(rows),
        )
        return rows

    # ── Standings ─────────────────────────────────────────────────────────

    def standings(self, season: int) -> list[dict[str, Any]]:
        """Fetch regular-season standings for all 30 teams.

        Args:
            season: 4-digit year.

        Returns:
            List of dicts: team_id, team_abbr, team_name, division_id, wins,
            losses, win_pct, games_back.
        """
        results: list[dict[str, Any]] = []
        for league_id in (103, 104):  # AL, NL
            data = self._get(
                "standings",
                leagueId=league_id,
                season=season,
                standingsTypes="regularSeason",
            )
            for rec in data.get("records", []):
                division_id = (rec.get("division") or {}).get("id")
                for t in rec.get("teamRecords", []):
                    team = t.get("team") or {}
                    results.append(
                        {
                            "team_id": team.get("id"),
                            "team_abbr": team.get("abbreviation"),
                            "team_name": team.get("name"),
                            "division_id": division_id,
                            "wins": t.get("wins"),
                            "losses": t.get("losses"),
                            "win_pct": float(t.get("winningPercentage", 0.0) or 0.0),
                            "games_back": t.get("gamesBack"),
                        }
                    )
        logger.info("standings: season=%d returned %d teams", season, len(results))
        return results

    # ── Team-season hitting (historical) ───────────────────────────────────

    def team_season_hitting(
        self,
        team_id: int,
        season: int,
    ) -> list[dict[str, Any]]:
        """Fetch every hitter's season counting stats for a team-season.

        The foundation for franchise "first since [legend]" gems — pulls real
        season HR/H/RBI/etc. for all players who appeared for the team that year.

        Args:
            team_id: MLB team ID.
            season: 4-digit year.

        Returns:
            List of dicts: player_id, player_name, and counting/rate stats.
        """
        data = self._get(
            "stats",
            stats="season",
            group="hitting",
            season=season,
            teamId=team_id,
            gameType="R",
            playerPool="all",
            limit=1000,
            hydrate="person",
        )
        splits = data.get("stats", [{}])[0].get("splits", [])
        out = []
        for s in splits:
            player = s.get("player") or s.get("person") or {}
            st = s.get("stat", {})

            def _i(key: str, st: dict = st) -> int:
                try:
                    return int(st.get(key, 0) or 0)
                except (ValueError, TypeError):
                    return 0

            out.append(
                {
                    "player_id": player.get("id"),
                    "player_name": player.get("fullName"),
                    "games": _i("gamesPlayed"),
                    "pa": _i("plateAppearances"),
                    "ab": _i("atBats"),
                    "runs": _i("runs"),
                    "hits": _i("hits"),
                    "doubles": _i("doubles"),
                    "triples": _i("triples"),
                    "hr": _i("homeRuns"),
                    "rbi": _i("rbi"),
                    "sb": _i("stolenBases"),
                    "bb": _i("baseOnBalls"),
                    "so": _i("strikeOuts"),
                    "avg": str(st.get("avg", "") or ""),
                    "obp": str(st.get("obp", "") or ""),
                    "slg": str(st.get("slg", "") or ""),
                    "ops": str(st.get("ops", "") or ""),
                }
            )
        logger.info("team_season_hitting: team=%d season=%d -> %d", team_id, season, len(out))
        return out

    # ── Game logs ──────────────────────────────────────────────────────────

    def player_game_log(
        self,
        player_id: int,
        season: int,
        group: str = "hitting",
    ) -> list[dict[str, Any]]:
        """Fetch a player's per-game hitting log for a season, oldest-first.

        Powers active-streak gems (hit streaks, on-base streaks).

        Args:
            player_id: MLBAM id.
            season: 4-digit year.
            group: stat group.

        Returns:
            List of dicts: game_date, game_pk, ab, hits, bb, hbp.
        """
        data = self._get(
            f"people/{player_id}/stats",
            stats="gameLog",
            season=season,
            group=group,
            gameType="R",
        )
        splits = data.get("stats", [{}])[0].get("splits", [])
        out = []
        for s in splits:
            st = s.get("stat", {})

            def _i(key: str, st: dict = st) -> int:
                try:
                    return int(st.get(key, 0) or 0)
                except (ValueError, TypeError):
                    return 0

            out.append(
                {
                    "game_date": s.get("date"),
                    "game_pk": (s.get("game") or {}).get("gamePk"),
                    "ab": _i("atBats"),
                    "hits": _i("hits"),
                    "bb": _i("baseOnBalls"),
                    "hbp": _i("hitByPitch"),
                }
            )
        return out

    def league_window_hitting(self, season: int, start: str, end: str) -> list[dict[str, Any]]:
        """Fetch every league hitter's on-base inputs for a date window (one call).

        Uses the ``byDateRange`` split so a single request returns the whole
        qualified league for an arbitrary calendar window — the cohort needed to
        control a player's change against league-wide drift over the same dates.

        Args:
            season: 4-digit year.
            start: ISO window start (inclusive).
            end: ISO window end (inclusive).

        Returns:
            List of dicts: player_id, player_name, ab, hits, bb, hbp, pa.
        """
        data = self._get(
            "stats",
            stats="byDateRange",
            group="hitting",
            season=season,
            sportId=1,
            gameType="R",
            startDate=start,
            endDate=end,
            limit=2000,
        )
        splits = data.get("stats", [{}])[0].get("splits", [])
        out = []
        for s in splits:
            player = s.get("player") or s.get("person") or {}
            st = s.get("stat", {})

            def _i(key: str, st: dict = st) -> int:
                try:
                    return int(st.get(key, 0) or 0)
                except (ValueError, TypeError):
                    return 0

            out.append(
                {
                    "player_id": player.get("id"),
                    "player_name": player.get("fullName"),
                    "ab": _i("atBats"),
                    "hits": _i("hits"),
                    "bb": _i("baseOnBalls"),
                    "hbp": _i("hitByPitch"),
                    "pa": _i("plateAppearances"),
                }
            )
        logger.info("league_window_hitting: %s..%s -> %d hitters", start, end, len(out))
        return out

    # ── Team-season pitching (historical) ──────────────────────────────────

    def team_season_pitching(self, team_id: int, season: int) -> list[dict[str, Any]]:
        """Fetch every pitcher's season counting stats for a team-season.

        Args:
            team_id: MLB team ID.
            season: 4-digit year.

        Returns:
            List of dicts: player_id, player_name, and pitching counting stats.
        """
        data = self._get(
            "stats",
            stats="season",
            group="pitching",
            season=season,
            teamId=team_id,
            gameType="R",
            playerPool="all",
            limit=1000,
            hydrate="person",
        )
        splits = data.get("stats", [{}])[0].get("splits", [])
        out = []
        for s in splits:
            player = s.get("player") or s.get("person") or {}
            st = s.get("stat", {})

            def _i(key: str, st: dict = st) -> int:
                try:
                    return int(st.get(key, 0) or 0)
                except (ValueError, TypeError):
                    return 0

            out.append(
                {
                    "player_id": player.get("id"),
                    "player_name": player.get("fullName"),
                    "games": _i("gamesPlayed"),
                    "gs": _i("gamesStarted"),
                    "wins": _i("wins"),
                    "losses": _i("losses"),
                    "saves": _i("saves"),
                    "so": _i("strikeOuts"),
                    "bb": _i("baseOnBalls"),
                    "hr": _i("homeRuns"),
                    "hbp": _i("hitByPitch"),
                    "er": _i("earnedRuns"),
                    "tbf": _i("battersFaced"),
                    "ip": str(st.get("inningsPitched", "") or ""),
                    "era": str(st.get("era", "") or ""),
                    "whip": str(st.get("whip", "") or ""),
                }
            )
        logger.info("team_season_pitching: team=%d season=%d -> %d", team_id, season, len(out))
        return out

    def league_pitching_constant(self, season: int) -> dict[str, float]:
        """Compute the season's FIP constant from league-wide pitching totals.

        FIP is scaled to the ERA baseline via ``C = lgERA - lgCore`` where
        ``lgCore = (13*HR + 3*(BB+HBP) - 2*K) / IP`` over every pitcher in the
        league (one call, no ``teamId``). Grounds the constant in real data
        instead of a hardcoded ~3.10 (an ungrounded threshold is a smell).

        Returns:
            ``{"season", "fip_const", "lg_era", "lg_ip"}``.

        Raises:
            MlbApiError: When the league response carries no usable innings.
        """
        data = self._get(
            "stats",
            stats="season",
            group="pitching",
            season=season,
            gameType="R",
            sportId=1,
            playerPool="all",
            limit=2000,
        )
        splits = data.get("stats", [{}])[0].get("splits", [])
        hr = bb = hbp = so = er = 0
        outs = 0
        for s in splits:
            st = s.get("stat", {})

            def _i(key: str, st: dict = st) -> int:
                try:
                    return int(st.get(key, 0) or 0)
                except (ValueError, TypeError):
                    return 0

            hr += _i("homeRuns")
            bb += _i("baseOnBalls")
            hbp += _i("hitByPitch")
            so += _i("strikeOuts")
            er += _i("earnedRuns")
            outs += innings_to_outs(str(st.get("inningsPitched", "") or ""))
        if outs == 0:
            raise MlbApiError(f"league_pitching_constant: no innings for season {season}")
        ip = outs / 3.0
        lg_era = er * 9.0 / ip
        lg_core = (13 * hr + 3 * (bb + hbp) - 2 * so) / ip
        const = lg_era - lg_core
        logger.info(
            "league_pitching_constant: season=%d pitchers=%d lgERA=%.2f C=%.2f",
            season,
            len(splits),
            lg_era,
            const,
        )
        return {"season": float(season), "fip_const": const, "lg_era": lg_era, "lg_ip": ip}

    # ── Affiliates (farm system) ────────────────────────────────────────────

    def org_affiliates(self, parent_id: int, season: int) -> list[dict[str, Any]]:
        """Return a club's minor-league affiliate teams (AAA→Rookie).

        Args:
            parent_id: Parent MLB team id.
            season: Season year.

        Returns:
            List of dicts: team_id, name, level (sport name), sport_id.
        """
        data = self._get("teams/affiliates", teamIds=parent_id, season=season)
        levels = {11: "AAA", 12: "AA", 13: "High-A", 14: "Single-A", 16: "Rookie"}
        out = []
        for t in data.get("teams", []):
            sport_id = (t.get("sport") or {}).get("id")
            if sport_id in levels:
                out.append(
                    {
                        "team_id": t.get("id"),
                        "name": t.get("name"),
                        "level": levels[sport_id],
                        "sport_id": sport_id,
                    }
                )
        logger.info("org_affiliates: parent=%d season=%d -> %d", parent_id, season, len(out))
        return out

    # ── Roster ────────────────────────────────────────────────────────────

    def roster(
        self,
        team_id: int = PADRES_TEAM_ID,
        season: int | None = None,
        roster_type: str = "40Man",
    ) -> list[dict[str, Any]]:
        """Fetch a team's roster.

        Args:
            team_id: MLB team ID.
            season: 4-digit year. Defaults to current season.
            roster_type: e.g. ``"40Man"``, ``"active"``, ``"fullRoster"``.

        Returns:
            List of dicts: player_id, player_name, position_code, position_name,
            status, jersey_number.
        """
        params: dict[str, Any] = {"rosterType": roster_type}
        if season:
            params["season"] = season
        data = self._get(f"teams/{team_id}/roster", **params)
        results = []
        for entry in data.get("roster", []):
            person = entry.get("person") or {}
            pos = entry.get("position") or {}
            status = entry.get("status") or {}
            results.append(
                {
                    "player_id": person.get("id"),
                    "player_name": person.get("fullName"),
                    "position_code": pos.get("abbreviation"),
                    "position_name": pos.get("name"),
                    "status": status.get("description"),
                    "jersey_number": entry.get("jerseyNumber"),
                }
            )
        logger.info(
            "roster: team=%d type=%s returned %d players", team_id, roster_type, len(results)
        )
        return results


# ── DB-writing ingest functions ───────────────────────────────────────────────


def ingest_roster(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    team_id: int = PADRES_TEAM_ID,
    roster_type: str = "40Man",
) -> int:
    """Fetch a live team roster and write it to main.team_rosters.

    Creates the table if absent and replaces the (team, season, roster_type)
    snapshot. The scan engine prefers this real roster over the simulated
    hist.team_rosters so non-Padres can't leak into Padre-only cards.

    Args:
        conn: Write-mode padres.db connection.
        season: 4-digit year.
        team_id: MLB team ID (default Padres).
        roster_type: Roster type to fetch.

    Returns:
        Rows written.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS team_rosters (
            team_id       INTEGER NOT NULL,
            season        INTEGER NOT NULL,
            roster_type   VARCHAR NOT NULL,
            player_id     INTEGER NOT NULL,
            player_name   VARCHAR,
            position_code VARCHAR,
            position_name VARCHAR,
            status        VARCHAR,
            jersey_number VARCHAR,
            source        VARCHAR,
            ingested_at   TIMESTAMP DEFAULT now(),
            PRIMARY KEY (team_id, season, roster_type, player_id)
        )
        """
    )
    source = f"mlb-stats-api/roster/{team_id}/{season}"
    with record_run(conn, source, note=roster_type) as _run:
        with MlbStatsClient() as client:
            players = client.roster(team_id, season, roster_type)
        conn.execute(
            "DELETE FROM team_rosters WHERE team_id = ? AND season = ? AND roster_type = ?",
            [team_id, season, roster_type],
        )
        for p in players:
            conn.execute(
                """
                INSERT INTO team_rosters
                    (team_id, season, roster_type, player_id, player_name,
                     position_code, position_name, status, jersey_number, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    team_id,
                    season,
                    roster_type,
                    p["player_id"],
                    p["player_name"],
                    p["position_code"],
                    p["position_name"],
                    p["status"],
                    p["jersey_number"],
                    source,
                ],
            )
    logger.info("ingest_roster: team=%d season=%d wrote %d players", team_id, season, len(players))
    return len(players)


def ingest_player_seasons(
    conn: duckdb.DuckDBPyConnection,
    start_season: int,
    end_season: int,
    team_id: int = PADRES_TEAM_ID,
) -> int:
    """Ingest a franchise's full player-season hitting history into main.

    This is the gem data layer: real season counting stats per player per year,
    enabling "first Padre with X since [legend] (year)" and "Nth N-HR season in
    franchise history" gems. Replaces the (season, team) snapshot each run.

    Args:
        conn: Write-mode padres.db connection.
        start_season: First year (Padres franchise began 1969).
        end_season: Last year (inclusive).
        team_id: MLB team ID (default Padres).

    Returns:
        Total player-season rows written.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_season_batting (
            player_id   INTEGER NOT NULL,
            player_name VARCHAR,
            season      INTEGER NOT NULL,
            team_id     INTEGER NOT NULL,
            games INTEGER, pa INTEGER, ab INTEGER, runs INTEGER, hits INTEGER,
            doubles INTEGER, triples INTEGER, hr INTEGER, rbi INTEGER, sb INTEGER,
            bb INTEGER, so INTEGER, avg VARCHAR, obp VARCHAR, slg VARCHAR, ops VARCHAR,
            source VARCHAR, ingested_at TIMESTAMP DEFAULT now(),
            PRIMARY KEY (player_id, season, team_id)
        )
        """
    )
    total = 0
    source = f"mlb-stats-api/team_season_hitting/{team_id}"
    with record_run(conn, source, note=f"{start_season}-{end_season}") as _run:  # noqa: SIM117
        with MlbStatsClient() as client:
            for season in range(start_season, end_season + 1):
                try:
                    rows = client.team_season_hitting(team_id, season)
                except MlbApiError as exc:
                    logger.error("player_seasons %d failed: %s", season, exc)
                    continue
                conn.execute(
                    "DELETE FROM player_season_batting WHERE season = ? AND team_id = ?",
                    [season, team_id],
                )
                for r in rows:
                    if r["player_id"] is None:
                        continue
                    conn.execute(
                        """
                        INSERT INTO player_season_batting (
                            player_id, player_name, season, team_id, games, pa, ab,
                            runs, hits, doubles, triples, hr, rbi, sb, bb, so,
                            avg, obp, slg, ops, source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            r["player_id"],
                            r["player_name"],
                            season,
                            team_id,
                            r["games"],
                            r["pa"],
                            r["ab"],
                            r["runs"],
                            r["hits"],
                            r["doubles"],
                            r["triples"],
                            r["hr"],
                            r["rbi"],
                            r["sb"],
                            r["bb"],
                            r["so"],
                            r["avg"],
                            r["obp"],
                            r["slg"],
                            r["ops"],
                            source,
                        ],
                    )
                total += len(rows)
    logger.info(
        "ingest_player_seasons: team=%d %d-%d wrote %d rows",
        team_id,
        start_season,
        end_season,
        total,
    )
    return total


def ingest_pitcher_seasons(
    conn: duckdb.DuckDBPyConnection,
    start_season: int,
    end_season: int,
    team_id: int = PADRES_TEAM_ID,
) -> int:
    """Ingest a franchise's full pitcher-season history into main.

    Args:
        conn: Write-mode padres.db connection.
        start_season: First year.
        end_season: Last year (inclusive).
        team_id: MLB team ID.

    Returns:
        Total pitcher-season rows written.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_season_pitching (
            player_id INTEGER NOT NULL, player_name VARCHAR,
            season INTEGER NOT NULL, team_id INTEGER NOT NULL,
            games INTEGER, gs INTEGER, wins INTEGER, losses INTEGER, saves INTEGER,
            so INTEGER, bb INTEGER, ip VARCHAR, era VARCHAR, whip VARCHAR,
            source VARCHAR, ingested_at TIMESTAMP DEFAULT now(),
            PRIMARY KEY (player_id, season, team_id)
        )
        """
    )
    # FIP inputs added after the table shipped — ALTER so existing DBs gain them.
    for col in ("hr INTEGER", "hbp INTEGER", "er INTEGER", "tbf INTEGER"):
        conn.execute(f"ALTER TABLE player_season_pitching ADD COLUMN IF NOT EXISTS {col}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS league_pitching_constants (
            season INTEGER PRIMARY KEY, fip_const DOUBLE, lg_era DOUBLE, lg_ip DOUBLE,
            ingested_at TIMESTAMP DEFAULT now()
        )
        """
    )
    total = 0
    source = f"mlb-stats-api/team_season_pitching/{team_id}"
    with record_run(conn, source, note=f"{start_season}-{end_season}") as _run:  # noqa: SIM117
        with MlbStatsClient() as client:
            for season in range(start_season, end_season + 1):
                try:
                    lc = client.league_pitching_constant(season)
                    conn.execute("DELETE FROM league_pitching_constants WHERE season = ?", [season])
                    conn.execute(
                        "INSERT INTO league_pitching_constants (season, fip_const, lg_era, lg_ip) "
                        "VALUES (?, ?, ?, ?)",
                        [season, lc["fip_const"], lc["lg_era"], lc["lg_ip"]],
                    )
                except MlbApiError as exc:
                    logger.error("league_pitching_constant %d failed: %s", season, exc)
                try:
                    rows = client.team_season_pitching(team_id, season)
                except MlbApiError as exc:
                    logger.error("pitcher_seasons %d failed: %s", season, exc)
                    continue
                conn.execute(
                    "DELETE FROM player_season_pitching WHERE season = ? AND team_id = ?",
                    [season, team_id],
                )
                for r in rows:
                    if r["player_id"] is None:
                        continue
                    conn.execute(
                        """
                        INSERT INTO player_season_pitching (
                            player_id, player_name, season, team_id, games, gs, wins,
                            losses, saves, so, bb, hr, hbp, er, tbf, ip, era, whip, source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            r["player_id"],
                            r["player_name"],
                            season,
                            team_id,
                            r["games"],
                            r["gs"],
                            r["wins"],
                            r["losses"],
                            r["saves"],
                            r["so"],
                            r["bb"],
                            r["hr"],
                            r["hbp"],
                            r["er"],
                            r["tbf"],
                            r["ip"],
                            r["era"],
                            r["whip"],
                            source,
                        ],
                    )
                total += len(rows)
    logger.info("ingest_pitcher_seasons: %d-%d wrote %d rows", start_season, end_season, total)
    return total


def ingest_league_windows(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    as_of: date,
    window_days: int = 25,
) -> int:
    """Ingest league-wide hitting for two calendar windows: the control cohort.

    Pulls every qualified league hitter's on-base inputs for the ``recent``
    window (the ``window_days`` ending at ``as_of``) and the ``prior`` window
    (the ``window_days`` before that), so a detector can control a player's
    change against league-wide drift over the *same* calendar dates.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year.
        as_of: End of the recent window (inclusive).
        window_days: Length of each window in calendar days.

    Returns:
        Total league-hitter window rows written across both windows.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS league_window_batting (
            season INTEGER NOT NULL, window_label VARCHAR NOT NULL,
            start_date DATE NOT NULL, end_date DATE NOT NULL,
            player_id INTEGER NOT NULL, player_name VARCHAR,
            ab INTEGER, hits INTEGER, bb INTEGER, hbp INTEGER, pa INTEGER,
            ingested_at TIMESTAMP DEFAULT now(),
            PRIMARY KEY (season, window_label, player_id)
        )
        """
    )
    recent_start = as_of - timedelta(days=window_days - 1)
    prior_end = recent_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=window_days - 1)
    windows = (
        ("prior", prior_start, prior_end),
        ("recent", recent_start, as_of),
    )
    total = 0
    with record_run(conn, "mlb-stats-api/league_window_hitting", note=str(as_of)) as _run:  # noqa: SIM117
        with MlbStatsClient() as client:
            for label, start, end in windows:
                try:
                    rows = client.league_window_hitting(season, start.isoformat(), end.isoformat())
                except MlbApiError as exc:
                    logger.error("league_windows %s failed: %s", label, exc)
                    continue
                conn.execute(
                    "DELETE FROM league_window_batting WHERE season = ? AND window_label = ?",
                    [season, label],
                )
                for r in rows:
                    if r["player_id"] is None:
                        continue
                    conn.execute(
                        """
                        INSERT INTO league_window_batting (
                            season, window_label, start_date, end_date, player_id, player_name,
                            ab, hits, bb, hbp, pa
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            season,
                            label,
                            start,
                            end,
                            r["player_id"],
                            r["player_name"],
                            r["ab"],
                            r["hits"],
                            r["bb"],
                            r["hbp"],
                            r["pa"],
                        ],
                    )
                total += len(rows)
    logger.info("ingest_league_windows: as_of=%s wrote %d rows", as_of, total)
    return total


def ingest_milb(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    parent_id: int = PADRES_TEAM_ID,
) -> int:
    """Ingest real minor-league hitting stats across the org's affiliates.

    Pulls each affiliate team's players (MLBAM-native — no id bridge) so the
    prospect/farm engine runs on real, current MiLB performance. Writes
    main.milb_batting, replacing the season snapshot.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year.
        parent_id: Parent MLB team id.

    Returns:
        Total MiLB player rows written.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS milb_batting (
            player_id INTEGER NOT NULL, player_name VARCHAR, season INTEGER NOT NULL,
            affiliate_id INTEGER NOT NULL, affiliate VARCHAR, level VARCHAR,
            games INTEGER, pa INTEGER, ab INTEGER, runs INTEGER, hits INTEGER,
            doubles INTEGER, triples INTEGER, hr INTEGER, rbi INTEGER, sb INTEGER,
            bb INTEGER, so INTEGER, avg VARCHAR, obp VARCHAR, slg VARCHAR, ops VARCHAR,
            source VARCHAR, ingested_at TIMESTAMP DEFAULT now(),
            PRIMARY KEY (player_id, season, affiliate_id)
        )
        """
    )
    total = 0
    source = f"mlb-stats-api/milb/{parent_id}/{season}"
    with record_run(conn, source, note=f"parent={parent_id}") as _run:  # noqa: SIM117
        with MlbStatsClient() as client:
            affiliates = client.org_affiliates(parent_id, season)
            conn.execute(
                "DELETE FROM milb_batting WHERE season = ? AND affiliate_id IN "
                f"({','.join(str(a['team_id']) for a in affiliates) or '0'})",
                [season],
            )
            for aff in affiliates:
                try:
                    rows = client.team_season_hitting(aff["team_id"], season)
                except MlbApiError as exc:
                    logger.error("milb %s failed: %s", aff["name"], exc)
                    continue
                for r in rows:
                    if r["player_id"] is None:
                        continue
                    conn.execute(
                        """
                        INSERT INTO milb_batting (
                            player_id, player_name, season, affiliate_id, affiliate, level,
                            games, pa, ab, runs, hits, doubles, triples, hr, rbi, sb, bb, so,
                            avg, obp, slg, ops, source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  ?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            r["player_id"],
                            r["player_name"],
                            season,
                            aff["team_id"],
                            aff["name"],
                            aff["level"],
                            r["games"],
                            r["pa"],
                            r["ab"],
                            r["runs"],
                            r["hits"],
                            r["doubles"],
                            r["triples"],
                            r["hr"],
                            r["rbi"],
                            r["sb"],
                            r["bb"],
                            r["so"],
                            r["avg"],
                            r["obp"],
                            r["slg"],
                            r["ops"],
                            source,
                        ],
                    )
                total += len(rows)
    logger.info("ingest_milb: parent=%d season=%d wrote %d rows", parent_id, season, total)
    return total


def ingest_game_logs(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    team_id: int = PADRES_TEAM_ID,
) -> int:
    """Ingest per-game hitting logs for the team's current-season hitters.

    Writes main.player_game_batting (typed for streak queries). Only pulls
    players who have batted this season (from player_season_batting), keeping
    it to ~20 API calls.

    Args:
        conn: Write-mode padres.db connection.
        season: Season year.
        team_id: MLB team id.

    Returns:
        Total game-log rows written.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_game_batting (
            player_id INTEGER NOT NULL, player_name VARCHAR, season INTEGER NOT NULL,
            game_date DATE NOT NULL, game_pk INTEGER,
            ab INTEGER, hits INTEGER, bb INTEGER, hbp INTEGER,
            source VARCHAR, ingested_at TIMESTAMP DEFAULT now(),
            PRIMARY KEY (player_id, game_date, game_pk)
        )
        """
    )
    hitters = conn.execute(
        "SELECT player_id, MAX(player_name) FROM player_season_batting "
        "WHERE season = ? AND team_id = ? AND ab > 0 GROUP BY player_id",
        [season, team_id],
    ).fetchall()
    total = 0
    source = f"mlb-stats-api/gameLog/{team_id}/{season}"
    with record_run(conn, source, note=f"{len(hitters)} hitters") as _run:  # noqa: SIM117
        with MlbStatsClient() as client:
            conn.execute("DELETE FROM player_game_batting WHERE season = ?", [season])
            for pid, pname in hitters:
                try:
                    games = client.player_game_log(pid, season)
                except MlbApiError as exc:
                    logger.error("game_log %s failed: %s", pid, exc)
                    continue
                for g in games:
                    if not g["game_date"]:
                        continue
                    conn.execute(
                        """
                        INSERT INTO player_game_batting
                            (player_id, player_name, season, game_date, game_pk,
                             ab, hits, bb, hbp, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            pid,
                            pname,
                            season,
                            g["game_date"],
                            g["game_pk"],
                            g["ab"],
                            g["hits"],
                            g["bb"],
                            g["hbp"],
                            source,
                        ],
                    )
                total += len(games)
    logger.info("ingest_game_logs: season=%d wrote %d game-rows", season, total)
    return total


def ingest_standings(conn: duckdb.DuckDBPyConnection, season: int) -> int:
    """Fetch live MLB standings and write them to main.standings.

    Creates the table if absent and replaces the season snapshot. The standings
    detector prefers this fresh main.standings over the simulated hist.standings.

    Args:
        conn: Write-mode padres.db connection.
        season: 4-digit year.

    Returns:
        Rows written.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS standings (
            team_id     INTEGER NOT NULL,
            team_abbr   VARCHAR,
            team_name   VARCHAR,
            division_id INTEGER,
            season      INTEGER NOT NULL,
            wins        INTEGER,
            losses      INTEGER,
            win_pct     DOUBLE,
            games_back  VARCHAR,
            source      VARCHAR,
            ingested_at TIMESTAMP DEFAULT now(),
            PRIMARY KEY (team_id, season)
        )
        """
    )
    source = f"mlb-stats-api/standings/{season}"
    with record_run(conn, source) as _run:
        with MlbStatsClient() as client:
            teams = client.standings(season)
        conn.execute("DELETE FROM standings WHERE season = ?", [season])
        for t in teams:
            conn.execute(
                """
                INSERT INTO standings
                    (team_id, team_abbr, team_name, division_id, season,
                     wins, losses, win_pct, games_back, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    t["team_id"],
                    t["team_abbr"],
                    t["team_name"],
                    t["division_id"],
                    season,
                    t["wins"],
                    t["losses"],
                    t["win_pct"],
                    str(t["games_back"]),
                    source,
                ],
            )
    logger.info("ingest_standings: season=%d wrote %d teams", season, len(teams))
    return len(teams)


def ingest_gamebox(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    team_id: int = PADRES_TEAM_ID,
) -> int:
    """Fetch a team's final-game scores and upsert them into game_box.

    Persists the box-score line (runs, innings, pitcher decisions) for every
    completed game, keyed on ``game_pk``. This is the run-differential layer the
    engine needs for Pythagorean cards (R/G, RA/G, expected vs actual record);
    the table previously had a schema but no writer, so it went stale.

    Args:
        conn: Write-mode padres.db connection (game_box created by initialize).
        season: Season year.
        team_id: MLB team id (default Padres).

    Returns:
        Rows upserted.
    """
    source = f"mlb-stats-api/gamebox/{team_id}/{season}"
    with record_run(conn, source) as run:
        with MlbStatsClient() as client:
            games = client.game_scores(team_id, season)
        for g in games:
            conn.execute(
                """
                INSERT INTO game_box
                    (game_pk, game_date, home_team_id, away_team_id, home_score,
                     away_score, innings, winning_pitcher_id, losing_pitcher_id,
                     save_pitcher_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (game_pk) DO UPDATE SET
                    game_date = EXCLUDED.game_date,
                    home_team_id = EXCLUDED.home_team_id,
                    away_team_id = EXCLUDED.away_team_id,
                    home_score = EXCLUDED.home_score,
                    away_score = EXCLUDED.away_score,
                    innings = EXCLUDED.innings,
                    winning_pitcher_id = EXCLUDED.winning_pitcher_id,
                    losing_pitcher_id = EXCLUDED.losing_pitcher_id,
                    save_pitcher_id = EXCLUDED.save_pitcher_id,
                    ingested_at = now()
                """,
                [
                    g["game_pk"],
                    g["game_date"],
                    g["home_team_id"],
                    g["away_team_id"],
                    g["home_score"],
                    g["away_score"],
                    g["innings"],
                    g["winning_pitcher_id"],
                    g["losing_pitcher_id"],
                    g["save_pitcher_id"],
                ],
            )
        run["rows_written"] = len(games)
    logger.info("ingest_gamebox: team=%d season=%d wrote %d games", team_id, season, len(games))
    return len(games)


def ingest_leaders(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    stat_types: tuple[str, ...] | None = None,
    limit: int = 25,
) -> int:
    """Fetch leaderboards from MLB Stats API and write to mlb_leaders.

    Clears existing rows for (season, stat_type) before inserting so the
    table always reflects the current rankings snapshot.

    Args:
        conn: Write-mode padres.db connection.
        season: 4-digit year.
        stat_types: Stat type names to ingest. Defaults to all hitting + pitching.
        limit: Number of leaders per stat type (1-100).

    Returns:
        Total rows written.
    """
    if stat_types is None:
        stat_types = HITTING_LEADER_STATS + PITCHING_LEADER_STATS

    total = 0
    source = f"mlb-stats-api/leaders/{season}"

    with record_run(conn, source, note=f"stats={','.join(stat_types)}") as run:
        with MlbStatsClient() as client:
            for stat_type in stat_types:
                stat_group = "pitching" if stat_type in PITCHING_LEADER_STATS else "hitting"
                try:
                    rows = client.leaders(
                        stat_type=stat_type,
                        season=season,
                        limit=limit,
                        stat_group=stat_group,
                    )
                except MlbApiError as exc:
                    logger.error("leaders fetch failed for %s: %s", stat_type, exc)
                    continue

                # Replace snapshot for this stat_type + season
                conn.execute(
                    "DELETE FROM mlb_leaders WHERE season = ? AND stat_type = ?",
                    [season, stat_type],
                )
                for row in rows:
                    conn.execute(
                        """
                        INSERT INTO mlb_leaders
                            (season, stat_group, stat_type, rank, player_id,
                             player_name, team_id, team_abbr, value)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            season,
                            stat_group,
                            stat_type,
                            row["rank"],
                            row["player_id"],
                            row["player_name"],
                            row["team_id"],
                            row["team_abbr"],
                            row["value"],
                        ],
                    )
                total += len(rows)
                logger.info(
                    "ingest_leaders: %s season=%d wrote %d rows", stat_type, season, len(rows)
                )

        run["rows_written"] = total

    return total
