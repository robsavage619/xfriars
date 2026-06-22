"""Trade / deadline-history engine — real Padres transaction history.

Runs on the real historical trade data (hist.trade_player_unified), not the
simulated 2026 universe. Surfaces deadline-timely context: how active the
Padres have been at recent deadlines and who they brought in.
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

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_SD_BREF = "SDP"
_DEADLINE_MONTH = 7  # July — the trade-deadline window
_YEARS_SHOWN = 8


class DeadlineHistoryDetector:
    """Padres deadline (July) acquisition activity by year — deadline-timely."""

    name = "deadline_history"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit a by-year deadline-additions bar with the latest haul named.

        Args:
            conn: Read-mode padres.db connection with hist attached.
            as_of: Reference date.

        Returns:
            A single-element list, or empty if trade history is unavailable.
        """
        try:
            counts = conn.execute(
                """
                SELECT trade_season, COUNT(*) AS n
                FROM hist.trade_player_unified
                WHERE to_team_bref = ? AND EXTRACT(MONTH FROM date) = ?
                  AND trade_season < ?
                GROUP BY trade_season
                ORDER BY trade_season DESC
                LIMIT ?
                """,
                [_SD_BREF, _DEADLINE_MONTH, as_of.year, _YEARS_SHOWN],
            ).fetchall()
        except Exception as exc:
            logger.warning("deadline_history: trade data unavailable (%s)", exc)
            return []
        if len(counts) < 3:
            return []

        # Most recent completed deadline + the players brought in (for the headline).
        latest_year = counts[0][0]
        latest_n = counts[0][1]
        names = [
            r[0]
            for r in conn.execute(
                """
                SELECT player_name FROM hist.trade_player_unified
                WHERE to_team_bref = ? AND EXTRACT(MONTH FROM date) = ? AND trade_season = ?
                ORDER BY date
                """,
                [_SD_BREF, _DEADLINE_MONTH, latest_year],
            ).fetchall()
        ]
        named = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        headline = (
            f"The Padres added {latest_n} players at the {latest_year} deadline "
            f"({named}) — A.J. Preller's track record ahead of the {as_of.year} deadline"
        )

        # Oldest-first for a left-to-right time read; highlight the latest year.
        ordered = list(reversed(counts))
        rows: list[list[str | int | float | None]] = [[str(yr), int(n)] for yr, n in ordered]
        highlight = [Mark(row_index=len(ordered) - 1, label=str(latest_year))]

        dataset = ChartDataset(
            title="PADRES DEADLINE ADDITIONS",
            subtitle="MLB players acquired in July, by year",
            as_of=as_of,
            columns=[
                Column(key="year", label="Year", role="dimension"),
                Column(key="adds", label="Players", role="measure", format="d"),
            ],
            rows=rows,
            highlight=highlight,
            framing=headline,
            source="Retrosheet / MLB transactions",
            headline=headline,
            claim_scope="since_2010",
            population_label="Padres July acquisitions",
            card_hint="bar",
            facts={
                "latest_year": int(latest_year),
                "latest_additions": int(latest_n),
                "_no_rank": True,  # years are chronological, not a ranking
            },
        )
        score, components = novelty_score(
            {
                "rarity": 0.78,
                "magnitude": min(latest_n / 8.0, 1.0),
                "timeliness": 0.95,  # deadline approaching
                "rootability": 0.9,
                "legibility": 0.92,
            },
            detector=self.name,
        )
        subject = f"SDP|deadline_history|{as_of.year}"
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
                provenance_json=[{"source_table": "trade_player_unified", "as_of": str(as_of)}],
                coverage_window=f"2010-{as_of.year}",
                claim_scope="since_2010",
                novelty_score=score,
                novelty_components=components,
            )
        ]


register(DeadlineHistoryDetector())
