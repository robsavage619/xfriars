"""Division-race detector — the rivalry/standings angle casual fans engage with.

Emits one ranked bar card of the NL West with the Padres highlighted, headlined
by their games-back (or lead). Prefers the freshly-ingested ``main.standings``
(``pad ingest standings``) and falls back to the simulated ``hist.standings``.
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
    RarityEvidence,
    StatCandidate,
    make_candidate_id,
)

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_SD_ID = 135
# NL West by MLBAM team id → display abbreviation (robust across data sources).
_NL_WEST: dict[int, str] = {119: "LAD", 135: "SD", 109: "AZ", 137: "SF", 115: "COL"}


def _resolve_standings_table(conn: duckdb.DuckDBPyConnection) -> tuple[str, int] | None:
    """Return (qualified_table, season), preferring fresh main over simulated hist.

    Args:
        conn: Connection with hist attached.

    Returns:
        (table, season) for the most recent season with NL West rows, or None.
    """
    ids = ",".join(str(i) for i in _NL_WEST)
    for table in ("standings", "hist.standings"):
        try:
            row = conn.execute(
                f"SELECT MAX(season) FROM {table} WHERE team_id IN ({ids})"
            ).fetchone()
        except Exception:
            continue
        if row and row[0] is not None:
            return table, int(row[0])
    return None


def _games_back(leader: tuple[int, int], team: tuple[int, int]) -> float:
    """Games back of the leader: ((lead_w - w) + (l - lead_l)) / 2."""
    lw, ll = leader
    w, ls = team
    return ((lw - w) + (ls - ll)) / 2.0


class NlWestRaceDetector:
    """Ranked NL West standings card with the Padres highlighted."""

    name = "nl_west_race"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit one NL West race card from the freshest standings available.

        Args:
            conn: Read-mode padres.db connection with hist attached.
            as_of: Reference date.

        Returns:
            A single-element list, or empty if standings or the Padres are absent.
        """
        resolved = _resolve_standings_table(conn)
        if resolved is None:
            logger.debug("nl_west_race: no standings table available")
            return []
        table, year = resolved
        ids = ",".join(str(i) for i in _NL_WEST)

        try:
            rows = conn.execute(
                f"""
                SELECT team_id, wins, losses, win_pct
                FROM {table}
                WHERE season = ? AND team_id IN ({ids})
                ORDER BY win_pct DESC
                """,
                [year],
            ).fetchall()
        except Exception as exc:
            logger.warning("nl_west_race: query failed: %s", exc)
            return []

        if not rows or not any(int(r[0]) == _SD_ID for r in rows):
            return []

        leader = (int(rows[0][1]), int(rows[0][2]))
        data_rows: list[list[str | int | float | None]] = []
        highlight: list[Mark] = []
        sd_w = sd_l = 0
        sd_gb = 0.0
        for i, (tid, w, ls, wpct) in enumerate(rows):
            abbr = _NL_WEST.get(int(tid), str(tid))
            data_rows.append([f"{abbr} {int(w)}-{int(ls)}", round(float(wpct), 3)])
            if int(tid) == _SD_ID:
                highlight.append(Mark(row_index=i, label="Padres"))
                sd_w, sd_l = int(w), int(ls)
                sd_gb = _games_back(leader, (int(w), int(ls)))

        if int(rows[0][0]) == _SD_ID:
            second = rows[1] if len(rows) > 1 else None
            lead_margin = (
                _games_back((sd_w, sd_l), (int(second[1]), int(second[2]))) if second else 0.0
            )
            headline = (
                f"The Padres ({sd_w}-{sd_l}) lead the NL West by {abs(lead_margin):.1f} games"
            )
        else:
            rival = _NL_WEST.get(int(rows[0][0]), "the leader")
            headline = (
                f"The Padres ({sd_w}-{sd_l}) are {sd_gb:.1f} games back of {rival} in the NL West"
            )

        dataset = ChartDataset(
            title="NL WEST RACE",
            subtitle=f"{year} Standings · through {as_of}",
            as_of=as_of,
            columns=[
                Column(key="team", label="Team", role="dimension"),
                Column(
                    key="win_pct", label="Win%", role="measure", format=".3f", domain=(0.0, 1.0)
                ),
            ],
            rows=data_rows,
            highlight=highlight,
            framing=headline,
            source="MLB Standings",
            headline=headline,
            claim_scope=f"{year}",
            population_label="NL West",
            card_hint="bar",
            facts={
                "padres_wins": sd_w,
                "padres_losses": sd_l,
                "games_back": round(sd_gb, 1),
                "leader": _NL_WEST.get(int(rows[0][0]), str(rows[0][0])),
                "season": year,
            },
        )

        # A division position is not a statistical tail — five teams occupy five
        # slots every day. The interest here is race heat, which interest.py reads
        # off games_back in the facts dict.
        evidence = RarityEvidence(kind="none")
        subject = f"SDP|nl_west_race|{year}"
        cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))

        return [
            StatCandidate(
                candidate_id=cid,
                detector=self.name,
                subject=subject,
                as_of=as_of,
                category="standings",
                payload_kind="dataset",
                facts_json=dataset.model_dump(mode="json"),
                provenance_json=[{"source_table": table.replace("hist.", ""), "as_of": str(as_of)}],
                coverage_window=f"{year}-{year}",
                claim_scope=f"{year}",
                novelty_score=0.0,  # overwritten by emit() from rarity_evidence
                rarity_evidence=evidence,
            )
        ]


register(NlWestRaceDetector())
