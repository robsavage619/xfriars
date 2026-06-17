"""Gem engine — Sarah Langs-style 'first since / Nth all-time / chase' gems.

Computes real franchise career leaderboards from player_season_batting and
surfaces the most compelling *active-player* situation for each counting stat:
the all-time lead, a close chase of a named legend, or a notable all-time rank.

This is the engine shape Langs gems share — rare standing + named precedent —
running on real 1969-present franchise history.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.base import register
from padres_analytics.detect.candidates import (
    ChartDataset,
    Column,
    Mark,
    StatCandidate,
    make_candidate_id,
)
from padres_analytics.detect.scoring import novelty_score
from padres_analytics.detect.sql import ordinal

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# (column, singular noun, abbreviation, "close chase" gap threshold)
_STATS: tuple[tuple[str, str, str, int], ...] = (
    ("hr", "home run", "HR", 6),
    ("hits", "hit", "H", 40),
    ("rbi", "RBI", "RBI", 25),
    ("sb", "stolen base", "SB", 8),
    ("doubles", "double", "2B", 12),
)
_TOP_N = 10  # rows shown on the all-time bar card
_RANK_CEILING = 15  # only surface an active player inside the all-time top N


def _career_leaderboard(
    conn: duckdb.DuckDBPyConnection, col: str
) -> list[tuple[int, str, int, int]]:
    """Return (player_id, name, career_total, last_season) for a stat, best-first."""
    return conn.execute(
        f"""
        SELECT player_id, MAX(player_name) AS name, SUM({col}) AS total, MAX(season) AS last_yr
        FROM player_season_batting
        GROUP BY player_id
        HAVING SUM({col}) > 0
        ORDER BY total DESC
        """
    ).fetchall()


class CareerChaseDetector:
    """Franchise all-time leaderboards + active-Padre chase gems per counting stat."""

    name = "career_chase"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit one all-time gem card per counting stat with a live storyline.

        Args:
            conn: Read-mode padres.db connection.
            as_of: Reference date.

        Returns:
            Up to len(_STATS) StatCandidate gem cards.
        """
        try:
            active = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT player_id FROM player_season_batting WHERE season = ?",
                    [as_of.year],
                ).fetchall()
            }
        except Exception as exc:
            logger.warning("career_chase: no player_season_batting (%s)", exc)
            return []
        if not active:
            return []

        candidates: list[StatCandidate] = []
        for col, noun, abbr, gap_thresh in _STATS:
            board = _career_leaderboard(conn, col)
            if len(board) < _TOP_N:
                continue
            cand = self._build(board, col, noun, abbr, gap_thresh, active, as_of)
            if cand is not None:
                candidates.append(cand)
        return candidates

    def _build(
        self,
        board: list[tuple[int, str, int, int]],
        col: str,
        noun: str,
        abbr: str,
        gap_thresh: int,
        active: set[int],
        as_of: date,
    ) -> StatCandidate | None:
        from padres_analytics.detect.sql import fmt_name

        # Best active player inside the all-time top N.
        focal_rank = next(
            (i for i, r in enumerate(board) if r[0] in active and i < _RANK_CEILING), None
        )
        if focal_rank is None:
            return None

        pid, pname_raw, total, _last = board[focal_rank]
        pname = fmt_name(pname_raw)
        rank1 = focal_rank + 1

        if focal_rank == 0:
            headline = f"{pname} is the Padres' all-time {noun} leader ({total} {abbr})"
            tier = "franchise_record"
        else:
            ahead_name = fmt_name(board[focal_rank - 1][1])
            ahead_total = board[focal_rank - 1][2]
            gap = ahead_total - total
            if 0 < gap <= gap_thresh:
                headline = (
                    f"{pname} ({total} {abbr}) needs {gap} to pass {ahead_name} "
                    f"({ahead_total}) for {ordinal(rank1 - 1)} on the Padres' all-time {noun} list"
                )
                tier = "chase"
            else:
                headline = (
                    f"{pname} is {ordinal(rank1)} on the Padres' "
                    f"all-time {noun} list ({total} {abbr})"
                )
                tier = "rank"

        # All-time top-N bar; active Padres highlighted among the legends.
        top = board[:_TOP_N]
        rows: list[list[str | int | float | None]] = [[fmt_name(r[1]), int(r[2])] for r in top]
        highlight = [
            Mark(row_index=i, label=fmt_name(r[1])) for i, r in enumerate(top) if r[0] in active
        ]

        dataset = ChartDataset(
            title=f"PADRES ALL-TIME {abbr}",
            subtitle=f"Franchise career {noun}s · 1969-{as_of.year}",
            as_of=as_of,
            columns=[
                Column(key="player", label="Player", role="dimension"),
                Column(key=col, label=abbr, role="measure", format="d", higher_is_better=True),
            ],
            rows=rows,
            highlight=highlight,
            framing=headline,
            source="MLB Stats API",
            headline=headline,
            claim_scope="franchise_1969",
            population_label=f"Padres franchise, 1969-{as_of.year}",
            card_hint="bar",
            facts={
                "player_name": pname,
                "career_total": int(total),
                "franchise_rank": rank1,
                "stat": abbr,
                "tier": tier,
            },
        )

        # Leading the franchise or a tight chase is the most gem-worthy.
        rarity = {"franchise_record": 0.95, "chase": 0.9, "rank": 0.78}[tier]
        score, components = novelty_score(
            {
                "rarity": rarity,
                "magnitude": max(0.0, 1.0 - (rank1 - 1) / _RANK_CEILING),
                "timeliness": 0.85,
                "rootability": 0.92,
                "legibility": 0.95,
            },
            detector=self.name,
        )
        subject = f"SDP|career_{col}|{pid}|{as_of.year}"
        cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))
        return StatCandidate(
            candidate_id=cid,
            detector=self.name,
            subject=subject,
            as_of=as_of,
            category="franchise",
            payload_kind="dataset",
            facts_json=dataset.model_dump(mode="json"),
            provenance_json=[{"source_table": "player_season_batting", "as_of": str(as_of)}],
            coverage_window=f"1969-{as_of.year}",
            claim_scope="franchise_1969",
            novelty_score=score,
            novelty_components=components,
        )


register(CareerChaseDetector())
