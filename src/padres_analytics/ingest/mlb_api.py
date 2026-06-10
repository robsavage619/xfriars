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


# ── DB-writing ingest functions ───────────────────────────────────────────────


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
