"""Leaderboard detector — league-wide top-N with Padre highlighted.

Queries the local mlb_leaders table (populated by pad ingest leaders).
Refuses to emit if the table is empty or the last ingest is stale (>48h).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from padres_analytics.config import PADRES_TEAM_ID
from padres_analytics.detect.base import register
from padres_analytics.detect.candidates import (
    StatCandidate,
    TablePayload,
    make_candidate_id,
)
from padres_analytics.detect.scoring import novelty_score
from padres_analytics.ingest.runs import last_complete_run

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Staleness threshold — refuse to emit if last complete ingest is older than this
_MAX_STALE_HOURS = 48

# Stat display metadata: (display_name, group, format_fn, coverage)
_STAT_META: dict[str, tuple[str, str, str, str]] = {
    "homeRuns": ("HR", "hitting", "int", "since_2010"),
    "battingAverage": ("AVG", "hitting", "avg", "since_2010"),
    "onBasePlusSlugging": ("OPS", "hitting", "ops", "since_2010"),
    "rbi": ("RBI", "hitting", "int", "since_2010"),
    "stolenBases": ("SB", "hitting", "int", "since_2010"),
    "hits": ("H", "hitting", "int", "since_2010"),
    "runs": ("R", "hitting", "int", "since_2010"),
    "strikeOuts": ("K", "hitting", "int", "since_2010"),
    "earnedRunAverage": ("ERA", "pitching", "era", "since_2010"),
    "wins": ("W", "pitching", "int", "since_2010"),
    "saves": ("SV", "pitching", "int", "since_2010"),
    "whip": ("WHIP", "pitching", "era", "since_2010"),
}

_STAT_TITLES: dict[str, str] = {
    "homeRuns": "Home Runs",
    "battingAverage": "Batting Average",
    "onBasePlusSlugging": "OPS",
    "rbi": "RBI",
    "stolenBases": "Stolen Bases",
    "hits": "Hits",
    "runs": "Runs",
    "strikeOuts": "Strikeouts",
    "earnedRunAverage": "ERA",
    "wins": "Wins",
    "saves": "Saves",
    "whip": "WHIP",
}


def _format_value(value: str, fmt: str) -> str:
    """Format a stat value for display.

    Args:
        value: Raw string value from the API.
        fmt: One of ``"int"``, ``"avg"``, ``"ops"``, ``"era"``.

    Returns:
        Formatted string.
    """
    try:
        f = float(value)
    except (ValueError, TypeError):
        return value
    if fmt == "int":
        return str(int(f))
    if fmt == "avg":
        # ".394" not "0.394"
        s = f"{f:.3f}"
        return s.lstrip("0") or ".000"
    if fmt in ("ops", "era"):
        return f"{f:.3f}"
    return value


def _is_stale(conn: duckdb.DuckDBPyConnection, season: int) -> bool:
    """Return True if the mlb_leaders table hasn't been refreshed recently."""
    source = f"mlb-stats-api/leaders/{season}"
    last = last_complete_run(conn, source)
    if last is None:
        return True
    age = datetime.now(UTC) - last.replace(tzinfo=UTC)
    return age > timedelta(hours=_MAX_STALE_HOURS)


def _build_leaderboard_candidate(
    conn: duckdb.DuckDBPyConnection,
    stat_type: str,
    season: int,
    as_of: date,
) -> StatCandidate | None:
    """Build one leaderboard candidate for a single stat type.

    Shows top 10 with the Padre row highlighted. If no Padre appears in the
    top 10, extends the window to include the Padre's position (up to rank 25).
    Returns None if no Padre is present in the ingested data.

    Args:
        conn: Read-mode padres.db connection.
        stat_type: e.g. ``"homeRuns"``.
        season: 4-digit year.
        as_of: Reference date.

    Returns:
        StatCandidate or None.
    """
    meta = _STAT_META.get(stat_type)
    if meta is None:
        logger.warning("leaderboards: unknown stat_type %r — skipping", stat_type)
        return None

    abbr, stat_group, fmt, claim_scope = meta

    # Fetch rows from local table
    rows = conn.execute(
        """
        SELECT rank, player_name, team_abbr, team_id, value
        FROM mlb_leaders
        WHERE season = ? AND stat_type = ?
        ORDER BY rank ASC
        LIMIT 25
        """,
        [season, stat_type],
    ).fetchall()

    if not rows:
        logger.debug("leaderboards: no data for %s season=%d", stat_type, season)
        return None

    # Find the Padre rank
    padre_rank: int | None = None
    for r in rows:
        if r[3] == PADRES_TEAM_ID:
            padre_rank = r[0]
            break

    if padre_rank is None:
        logger.debug(
            "leaderboards: no Padre in top %d for %s season=%d",
            len(rows),
            stat_type,
            season,
        )
        return None

    # Show top 10 or extend to include the Padre row
    display_limit = max(10, padre_rank)
    display_rows = [r for r in rows if r[0] <= display_limit][:10]

    padre_idx = next((i for i, r in enumerate(display_rows) if r[3] == PADRES_TEAM_ID), None)

    table_rows = [
        [
            str(r[0]),
            r[1] or "—",
            r[2] or "—",
            _format_value(r[4], fmt),
        ]
        for r in display_rows
    ]

    padre_row = next((r for r in rows if r[3] == PADRES_TEAM_ID), None)
    padre_name = padre_row[1] if padre_row else "Padre"
    padre_value = _format_value(padre_row[4], fmt) if padre_row else "—"
    padre_value_raw = padre_row[4] if padre_row else "0"

    title = f"{season} MLB {_STAT_TITLES.get(stat_type, abbr)} Leaders"
    subtitle = f"{abbr} — {season} regular season · through {as_of.isoformat()}"
    headline = f"{padre_name} ranks #{padre_rank} in MLB {abbr} ({padre_value}) in {season}"

    facts: dict = {
        "stat_type": stat_type,
        "stat_abbr": abbr,
        "stat_group": stat_group,
        "season": season,
        "padre_rank": padre_rank,
        "padre_name": padre_name,
        "padre_value": padre_value,
        "padre_value_raw": padre_value_raw,
        "total_in_table": len(display_rows),
    }

    payload = TablePayload(
        title=title,
        subtitle=subtitle,
        as_of=as_of,
        columns=["Rank", "Player", "Team", abbr],
        rows=table_rows,
        highlight_row=padre_idx,
        source="MLB Stats API",
        headline=headline,
        claim_scope=claim_scope,
    )

    # Novelty scoring
    rank_rarity = max(0.0, 1.0 - (padre_rank - 1) / 25)
    magnitude = 0.7
    timeliness = 0.8
    rootability = 0.75
    legibility = 0.9

    score, components = novelty_score(
        {
            "rarity": rank_rarity,
            "magnitude": magnitude,
            "timeliness": timeliness,
            "rootability": rootability,
            "legibility": legibility,
        },
        detector="leaderboard",
    )

    cid = make_candidate_id(
        "leaderboard",
        f"SDP|{season}|{stat_type}",
        {**payload.model_dump(mode="json"), **facts},
    )

    return StatCandidate(
        candidate_id=cid,
        detector="leaderboard",
        subject=f"SDP|{season}|{stat_type}",
        as_of=as_of,
        category="season",
        payload_kind="table",
        facts_json={**payload.model_dump(mode="json"), **facts},
        provenance_json=[
            {
                "source_table": "mlb_leaders",
                "sql": (
                    "SELECT rank, player_name, team_abbr, value "
                    "FROM mlb_leaders WHERE season=? AND stat_type=? ORDER BY rank"
                ),
                "params": {"season": season, "stat_type": stat_type},
                "as_of": str(as_of),
            }
        ],
        coverage_window=f"{season}-{season}",
        claim_scope=claim_scope,
        novelty_score=score,
        novelty_components=components,
    )


class LeaderboardDetector:
    """Emits current-season MLB leaderboard candidates with Padre highlighted."""

    name = "leaderboard"

    def run(
        self,
        conn: duckdb.DuckDBPyConnection,
        as_of: date,
    ) -> list[StatCandidate]:
        """Run leaderboard detection for all configured stat types.

        Args:
            conn: Read-only padres.db connection.
            as_of: Reference date.

        Returns:
            List of StatCandidate objects.
        """
        season = as_of.year
        candidates: list[StatCandidate] = []

        if _is_stale(conn, season):
            logger.warning(
                "leaderboard: mlb_leaders for season=%d is stale or missing. "
                "Run 'pad ingest leaders' first.",
                season,
            )
            return []

        for stat_type in _STAT_META:
            try:
                cand = _build_leaderboard_candidate(conn, stat_type, season, as_of)
            except Exception as exc:
                logger.error("leaderboard: %s failed: %s", stat_type, exc)
                continue
            if cand:
                candidates.append(cand)

        return candidates


_leaderboard = LeaderboardDetector()
register(_leaderboard)
