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


# (column, singular noun, abbr, round milestones, proximity gap)
_CLUB_MILESTONES: tuple[tuple[str, str, str, tuple[int, ...], int], ...] = (
    ("hr", "home run", "HR", (100, 150, 200, 250, 300), 12),
    ("hits", "hit", "H", (500, 1000, 1500, 2000, 2500, 3000), 80),
    ("rbi", "RBI", "RBI", (500, 750, 1000, 1250), 50),
    ("sb", "stolen base", "SB", (100, 150, 200, 300), 15),
    ("doubles", "double", "2B", (200, 300, 400), 25),
)
_MAX_CLUB_RANK = 12  # only surface genuinely exclusive clubs ("Nth ever" must be small)


class MilestoneClubDetector:
    """'Nth Padre ever to reach [round milestone]' — rare-club approach gems.

    Forward-looking and valid mid-season: a player's career total is monotonic,
    so "N away from the 200-HR club, would be the Mth Padre ever" is always true.
    """

    name = "milestone_club"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit a rare-club approach gem per stat where an active Padre is near a milestone.

        Args:
            conn: Read-mode padres.db connection.
            as_of: Reference date.

        Returns:
            Up to len(_CLUB_MILESTONES) gem cards.
        """
        from padres_analytics.detect.sql import fmt_name

        try:
            active = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT player_id FROM player_season_batting WHERE season = ?",
                    [as_of.year],
                ).fetchall()
            }
        except Exception:
            return []
        if not active:
            return []

        out: list[StatCandidate] = []
        for col, noun, abbr, milestones, gap_max in _CLUB_MILESTONES:
            board = _career_leaderboard(conn, col)  # (pid, name, total, last_yr) best-first
            totals = [t for _, _, t, _ in board]
            # Best active player approaching (but not yet at) a round milestone.
            best: tuple | None = None
            for pid, name_raw, total, _last in board:
                if pid not in active:
                    continue
                nxt = next((m for m in milestones if m > total), None)
                if nxt is None:
                    continue
                gap = nxt - total
                if 0 < gap <= gap_max and (best is None or gap < best[3]):
                    best = (pid, fmt_name(name_raw), total, gap, nxt)
            if best is None:
                continue
            pid, pname, total, gap, milestone = best
            club_size = sum(1 for t in totals if t >= milestone)
            nth = club_size + 1
            # Exclusivity gate: "2nd ever" is a gem, "33rd ever" is not.
            if nth > _MAX_CLUB_RANK:
                continue
            if club_size == 0:
                club_line = f"the first Padre ever to reach {milestone}"
            else:
                club_line = f"the {ordinal(nth)} Padre ever to reach {milestone}"
            headline = (
                f"{pname} is {gap} {abbr} from {milestone} career {noun}s as a Padre — "
                f"would be {club_line}"
            )

            dataset = ChartDataset(
                title=pname.upper(),
                subtitle=f"Career {noun}s · Padres franchise",
                as_of=as_of,
                columns=[Column(key=col, label=abbr, role="measure", format="d")],
                rows=[[int(total)]],
                hero={
                    "value": str(gap),
                    "label": f"{abbr} from {milestone}",
                    "context": f"{total} now · would be {club_line.replace('the ', '')}",
                },
                framing=headline,
                source="MLB Stats API",
                headline=headline,
                claim_scope="franchise_1969",
                card_hint="hero",
                facts={
                    "padre_player_id": pid,
                    "player_name": pname,
                    "career_total": int(total),
                    "milestone": int(milestone),
                    "gap": int(gap),
                    "club_size": club_size,
                    "would_be_nth": nth,
                    "stat": abbr,
                },
            )
            score, components = novelty_score(
                {
                    "rarity": min(0.86 + (5 - club_size) * 0.02, 0.96) if club_size < 5 else 0.84,
                    "magnitude": max(0.0, 1.0 - gap / gap_max),
                    "timeliness": 0.85,
                    "rootability": 0.9,
                    "legibility": 0.95,
                },
                detector=self.name,
            )
            subject = f"SDP|club_{col}|{pid}|{milestone}"
            cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))
            out.append(
                StatCandidate(
                    candidate_id=cid,
                    detector=self.name,
                    subject=subject,
                    as_of=as_of,
                    category="franchise",
                    payload_kind="dataset",
                    facts_json=dataset.model_dump(mode="json"),
                    provenance_json=[
                        {"source_table": "player_season_batting", "as_of": str(as_of)}
                    ],
                    coverage_window=f"1969-{as_of.year}",
                    claim_scope="franchise_1969",
                    novelty_score=score,
                    novelty_components=components,
                )
            )
        return out


register(MilestoneClubDetector())
