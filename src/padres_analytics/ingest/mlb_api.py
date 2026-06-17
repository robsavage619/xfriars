"""MLB Stats API client — schedule, roster, season stats, leaderboards, boxscores.

Politeness: 2s delay between requests. All data is used on an unofficial,
non-commercial basis per MLB's API usage policy.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from padres_analytics.config import MLB_STATS_API_BASE, PADRES_TEAM_ID
from padres_analytics.ingest.runs import record_run

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 30.0
POLITENESS_DELAY = 2.0

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

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        """GET a JSON endpoint, respecting the politeness delay.

        Args:
            path: URL path relative to base_url.
            **params: Query parameters.

        Returns:
            Parsed JSON response dict.

        Raises:
            MlbApiError: On HTTP error or JSON parse failure.
        """
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)

        url = f"{self._base}/{path.lstrip('/')}"
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
                    "ip": str(st.get("inningsPitched", "") or ""),
                    "era": str(st.get("era", "") or ""),
                    "whip": str(st.get("whip", "") or ""),
                }
            )
        logger.info("team_season_pitching: team=%d season=%d -> %d", team_id, season, len(out))
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
    total = 0
    source = f"mlb-stats-api/team_season_pitching/{team_id}"
    with record_run(conn, source, note=f"{start_season}-{end_season}") as _run:  # noqa: SIM117
        with MlbStatsClient() as client:
            for season in range(start_season, end_season + 1):
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
                            losses, saves, so, bb, ip, era, whip, source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            r["ip"],
                            r["era"],
                            r["whip"],
                            source,
                        ],
                    )
                total += len(rows)
    logger.info("ingest_pitcher_seasons: %d-%d wrote %d rows", start_season, end_season, total)
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
