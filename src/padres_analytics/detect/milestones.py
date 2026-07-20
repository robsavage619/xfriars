"""Franchise WAR milestone detector — active Padres climbing the all-time list."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from padres_analytics.detect.base import register
from padres_analytics.detect.candidates import (
    ChartDataset,
    Column,
    Mark,
    StatCandidate,
    make_candidate_id,
)

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_SD_BREF = "SDP"
_FRANCHISE_FOUNDED = 1969
_TABLE_ROWS = 10
_ACTIVE_TOP_N = 10  # only emit for players in franchise top N
_REEMIT_DAYS = 30  # silence same player for 30 days after last emit
_NOVELTY_BASE = 0.70
_NOVELTY_TOP5_BONUS = 0.15
_NOVELTY_CLOSE_BONUS = 0.08  # within 1.0 WAR of the next rank above


def _franchise_leaderboard(conn: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Return top _TABLE_ROWS all-time Padre WAR totals (all stints combined)."""
    return conn.execute(f"""
        SELECT mlb_id,
               name_common,
               ROUND(SUM(war), 1) AS career_war,
               MIN(year_id) AS first_yr,
               MAX(year_id) AS last_yr
        FROM hist.bwar_player_seasons
        WHERE team_id = '{_SD_BREF}'
        GROUP BY mlb_id, name_common
        HAVING SUM(war) > 0
        ORDER BY SUM(war) DESC, mlb_id
        LIMIT {_TABLE_ROWS}
    """).fetchall()


def _current_season(conn: duckdb.DuckDBPyConnection) -> int:
    """Most recent season with SDP bWAR data."""
    row = conn.execute(
        f"SELECT MAX(year_id) FROM hist.bwar_player_seasons WHERE team_id = '{_SD_BREF}'"
    ).fetchone()
    return row[0] if row and row[0] else date.today().year


def _active_padre_ids(conn: duckdb.DuckDBPyConnection, season: int) -> set[int]:
    """mlb_id values of players with SDP bWAR data in the current season."""
    rows = conn.execute(
        "SELECT DISTINCT mlb_id FROM hist.bwar_player_seasons WHERE team_id = ? AND year_id = ?",
        [_SD_BREF, season],
    ).fetchall()
    return {r[0] for r in rows}


def _recently_emitted(conn: duckdb.DuckDBPyConnection, subject: str, as_of: date) -> bool:
    cutoff = as_of - timedelta(days=_REEMIT_DAYS)
    row = conn.execute(
        "SELECT 1 FROM stat_candidates WHERE subject = ? AND as_of >= ?",
        [subject, cutoff],
    ).fetchone()
    return row is not None


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class FranchiseWarRankDetector:
    """Emits a candidate when an active Padre is in the franchise all-time WAR top N."""

    name = "franchise_war_rank"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Detect active Padres in the franchise all-time WAR leaderboard.

        Args:
            conn: Read-mode padres.db connection with hist attached.
            as_of: Reference date for the detection run.

        Returns:
            One StatCandidate per active Padre in the franchise top N,
            limited by the 30-day re-emit gate.
        """
        leaderboard = _franchise_leaderboard(conn)
        if not leaderboard:
            return []

        current_yr = _current_season(conn)
        active_ids = _active_padre_ids(conn, current_yr)

        subject = "SDP|franchise_war_leaders"
        if _recently_emitted(conn, subject, as_of):
            logger.debug("franchise_war_rank: skipping — emitted recently")
            return []

        top = leaderboard[:_TABLE_ROWS]
        # Active Padres currently on the all-time list (their rank within the top N).
        active = [
            (i, name, float(war))
            for i, (mlb_id, name, war, _f, _l) in enumerate(top)
            if mlb_id in active_ids and i < _ACTIVE_TOP_N
        ]
        if not active:
            return []

        rows: list[list[str | int | float | None]] = [
            [name, round(float(war), 1)] for (_, name, war, _f, _l) in top
        ]
        highlights = [Mark(row_index=i, label=name) for (i, name, _w) in active]

        lead_i, lead_name, lead_war = active[0]
        lead_rank = lead_i + 1
        extra = ""
        if len(active) > 1:
            extra = "; also " + ", ".join(f"{n} ({_ordinal(i + 1)})" for (i, n, _w) in active[1:])
        headline = (
            f"{lead_name} is {_ordinal(lead_rank)} all-time in Padres franchise WAR "
            f"({lead_war} WAR){extra}"
        )

        novelty = _NOVELTY_BASE
        if lead_rank <= 5:
            novelty += _NOVELTY_TOP5_BONUS
        novelty = min(novelty, 0.95)

        coverage = f"{_FRANCHISE_FOUNDED}-{current_yr}"
        claim = f"since_{_FRANCHISE_FOUNDED}"

        dataset = ChartDataset(
            title="PADRES ALL-TIME WAR LEADERS",
            subtitle=f"Career WAR as a Padre · through {as_of}",
            as_of=as_of,
            columns=[
                Column(key="player", label="Player", role="dimension"),
                Column(key="war", label="WAR", role="measure", format=".1f", higher_is_better=True),
            ],
            rows=rows,
            highlight=highlights,
            framing=headline,
            source="Baseball Reference",
            headline=headline,
            claim_scope=claim,
            population_label=f"Padres franchise, {_FRANCHISE_FOUNDED}-{current_yr}",
            card_hint="bar",
            facts={
                "lead_player": lead_name,
                "lead_rank": lead_rank,
                "lead_war": lead_war,
                "active_leaders": ", ".join(n for (_i, n, _w) in active),
                "active_count": len(active),
            },
        )

        cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))

        return [
            StatCandidate(
                candidate_id=cid,
                detector=self.name,
                subject=subject,
                as_of=as_of,
                category="franchise",
                payload_kind="dataset",
                facts_json=dataset.model_dump(mode="json"),
                provenance_json=[{"source_table": "bwar_player_seasons", "as_of": str(as_of)}],
                coverage_window=coverage,
                claim_scope=claim,
                novelty_score=novelty,
            )
        ]


_WATCH_GAP_MAX = 2.5  # fire only when within this many WAR of the next rank up
_WATCH_RANK_MAX = 20  # only chase targets inside the franchise top N
_WATCH_LEADERBOARD_N = 30
_WATCH_REEMIT_DAYS = 14


class MilestoneWatchDetector:
    """Emits when an active Padre is closing in on a franchise all-time WAR rank.

    The chase is the story: "X is 0.1 WAR from passing Y for 3rd all-time."
    Fires *before* the milestone, anticipatory rather than retrospective.
    """

    name = "milestone_watch"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Detect active Padres within striking distance of a franchise WAR rank.

        Args:
            conn: Read-mode padres.db connection with hist attached.
            as_of: Reference date for the detection run.

        Returns:
            One StatCandidate per active Padre whose gap to the next rank above
            is at most _WATCH_GAP_MAX WAR, limited by the re-emit gate.
        """
        leaderboard = conn.execute(f"""
            SELECT mlb_id,
                   name_common,
                   ROUND(SUM(war), 1) AS career_war,
                   MIN(year_id) AS first_yr,
                   MAX(year_id) AS last_yr
            FROM hist.bwar_player_seasons
            WHERE team_id = '{_SD_BREF}'
            GROUP BY mlb_id, name_common
            HAVING SUM(war) > 0
            ORDER BY SUM(war) DESC
            LIMIT {_WATCH_LEADERBOARD_N}
        """).fetchall()
        if len(leaderboard) < 2:
            return []

        current_yr = _current_season(conn)
        active_ids = _active_padre_ids(conn, current_yr)

        candidates: list[StatCandidate] = []

        for rank_0, (mlb_id, name, career_war, _f, _l) in enumerate(leaderboard):
            if rank_0 == 0 or mlb_id not in active_ids:
                continue

            target_id, target_name, target_war, _t_first, _t_last = leaderboard[rank_0 - 1]
            target_rank = rank_0  # the rank the player would claim by passing
            if target_rank > _WATCH_RANK_MAX:
                continue

            gap = round(target_war - career_war, 1)
            if gap <= 0 or gap > _WATCH_GAP_MAX:
                continue

            subject = f"SDP|milestone_watch|{mlb_id}|{target_id}"
            cutoff = as_of - timedelta(days=_WATCH_REEMIT_DAYS)
            seen = conn.execute(
                "SELECT 1 FROM stat_candidates WHERE subject = ? AND as_of >= ?",
                [subject, cutoff],
            ).fetchone()
            if seen is not None:
                logger.debug("milestone_watch: skipping %s — emitted recently", name)
                continue

            headline = (
                f"{name} ({career_war} WAR) is {gap} WAR from passing "
                f"{target_name} ({target_war}) for {_ordinal(target_rank)} "
                f"all-time among Padres"
            )

            novelty = 0.75
            if gap <= 0.5:
                novelty += 0.15
            elif gap <= 1.0:
                novelty += 0.10
            if target_rank <= 5:
                novelty += 0.05
            novelty = min(novelty, 0.97)

            coverage = f"{_FRANCHISE_FOUNDED}-{current_yr}"
            claim = f"since_{_FRANCHISE_FOUNDED}"

            # Hero card: the gap is the one big number; the chase is the story.
            ordinal_rank = _ordinal(target_rank)
            dataset = ChartDataset(
                title=name.upper(),
                subtitle=f"Career WAR as a Padre · through {as_of}",
                as_of=as_of,
                columns=[
                    Column(key="gap", label="WAR to next rank", role="measure", unit="WAR"),
                ],
                rows=[[gap]],
                hero={
                    "value": f"{gap}",
                    "label": f"WAR from {ordinal_rank} all-time",
                    "context": f"{career_war} WAR · passing {target_name} ({target_war})",
                },
                framing=headline,
                source="Baseball Reference",
                headline=headline,
                claim_scope=claim,
                card_hint="hero",
                facts={
                    "padre_player_id": mlb_id,
                    "player_name": name,
                    "career_sdp_war": float(career_war),
                    "target_id": target_id,
                    "target_name": target_name,
                    "target_war": float(target_war),
                    "target_rank": target_rank,
                    "gap_war": float(gap),
                },
            )

            prov = [
                {
                    "source_table": "bwar_player_seasons",
                    "as_of": str(as_of),
                }
            ]

            cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))

            candidates.append(
                StatCandidate(
                    candidate_id=cid,
                    detector=self.name,
                    subject=subject,
                    as_of=as_of,
                    category="franchise",
                    payload_kind="dataset",
                    facts_json=dataset.model_dump(mode="json"),
                    provenance_json=prov,
                    coverage_window=coverage,
                    claim_scope=claim,
                    novelty_score=novelty,
                    novelty_components={
                        "rarity": round(1 - target_rank / _WATCH_RANK_MAX, 2),
                        "magnitude": round(1 - gap / _WATCH_GAP_MAX, 2),
                        "timeliness": 1.0,
                    },
                )
            )

        return candidates


register(FranchiseWarRankDetector())
register(MilestoneWatchDetector())
