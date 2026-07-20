"""Prospect / farm-system engine — how the Padres' minor leaguers are performing.

Runs on real minor-league stats ingested per affiliate (main.milb_batting,
``pad ingest milb``) — MLBAM-native, so no id bridge and no simulated data.
Surfaces the system's top performers and names any who are ranked prospects.
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
from padres_analytics.detect.sql import fmt_name

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_MIN_PA = 100  # plate appearances for a real MiLB sample
_TOP_N = 8


class FarmPerformanceDetector:
    """Top hitters across the Padres farm system, by OPS — real MiLB performance."""

    name = "farm_performance"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit a 'top farm hitters' bar with the leader (and any ranked prospect) named.

        Args:
            conn: Read-mode padres.db connection.
            as_of: Reference date.

        Returns:
            A single-element list, or empty if MiLB data is unavailable.
        """
        try:
            rows = conn.execute(
                """
                SELECT player_id, player_name, level, hr,
                       TRY_CAST(ops AS DOUBLE) AS ops, TRY_CAST(avg AS DOUBLE) AS avg
                FROM milb_batting
                WHERE season = ? AND pa >= ? AND TRY_CAST(ops AS DOUBLE) IS NOT NULL
                ORDER BY ops DESC, player_id
                LIMIT ?
                """,
                [as_of.year, _MIN_PA, _TOP_N],
            ).fetchall()
        except Exception as exc:
            logger.warning("farm_performance: no milb_batting (%s)", exc)
            return []
        if len(rows) < 4:
            return []

        # Names of ranked prospects (best-effort match against the real ranking list).
        ranked: set[str] = set()
        try:
            ranked = {
                fmt_name(str(r[0])).lower()
                for r in conn.execute(
                    "SELECT player_name FROM hist.prospect_rankings "
                    "WHERE org IN ('SD','SDP') AND rank_year >= ?",
                    [as_of.year - 2],
                ).fetchall()
            }
        except Exception:
            ranked = set()

        leader = rows[0]
        lead_name = fmt_name(leader[1])
        lead_tag = " (a top org prospect)" if lead_name.lower() in ranked else ""
        headline = (
            f"{lead_name} is raking at {leader[2]} — "
            f".{round(leader[5] * 1000):03d} AVG, {leader[4]:.3f} OPS, {leader[3]} HR, "
            f"tops in the Padres farm system{lead_tag}"
        )

        data_rows: list[list[str | int | float | None]] = [
            [f"{fmt_name(r[1])} ({r[2]})", round(float(r[4]), 3)] for r in rows
        ]
        highlight = [Mark(row_index=0, label=lead_name)]

        dataset = ChartDataset(
            title="PADRES FARM — TOP HITTERS",
            subtitle=f"{as_of.year} minor leagues · OPS (min {_MIN_PA} PA)",
            as_of=as_of,
            columns=[
                Column(key="player", label="Player", role="dimension"),
                Column(key="ops", label="OPS", role="measure", format=".3f", higher_is_better=True),
            ],
            rows=data_rows,
            highlight=highlight,
            framing=headline,
            source="MLB Stats API (MiLB)",
            headline=headline,
            claim_scope=f"{as_of.year}",
            population_label="Padres farm system",
            card_hint="bar",
            facts={
                "leader": lead_name,
                "leader_level": leader[2],
                "leader_ops": round(float(leader[4]), 3),
                "leader_hr": int(leader[3]),
            },
        )
        score, components = novelty_score(
            {
                "rarity": min(float(leader[4]) / 1.1, 0.95),
                "magnitude": min(float(leader[4]) / 1.1, 0.95),
                "timeliness": 0.85,
                "rootability": 0.85,
                "legibility": 0.92,
            },
            detector=self.name,
        )
        subject = f"SDP|farm_performance|{as_of.year}"
        cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))
        return [
            StatCandidate(
                candidate_id=cid,
                detector=self.name,
                subject=subject,
                as_of=as_of,
                category="prospects",
                payload_kind="dataset",
                facts_json=dataset.model_dump(mode="json"),
                provenance_json=[{"source_table": "milb_batting", "as_of": str(as_of)}],
                coverage_window=f"{as_of.year}-{as_of.year}",
                claim_scope=f"{as_of.year}",
                novelty_score=score,
                novelty_components=components,
            )
        ]


register(FarmPerformanceDetector())
