"""Crossjoin detectors — SD efficiency and trade return, sourced from hist tables."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.base import register
from padres_analytics.detect.candidates import StatCandidate, TablePayload, make_candidate_id

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_SD_BREF = "SDP"
_SD_TEAM_ID = 135

# Only emit $/WAR if SD's last complete season has data
_DOLLAR_PER_WAR_SQL = """
WITH payroll AS (
    SELECT team_bref,
           ROUND(SUM(cap_hit) / 1e6, 1) AS pay_m
    FROM hist.spotrac_player_contracts
    WHERE season = ?
    GROUP BY team_bref
),
pos_war AS (
    SELECT team_id,
           ROUND(SUM(GREATEST(war, 0)), 1) AS war
    FROM hist.bwar_player_seasons
    WHERE year_id = ?
    GROUP BY team_id
)
SELECT
    p.team_bref                                                             AS team,
    p.pay_m,
    COALESCE(w.war, 0)                                                      AS war,
    ROUND(p.pay_m / NULLIF(COALESCE(w.war, 0), 0), 2)                      AS m_per_war,
    RANK() OVER (
        ORDER BY p.pay_m / NULLIF(COALESCE(w.war, 0), 0) ASC
    )                                                                        AS eff_rank
FROM payroll p
LEFT JOIN pos_war w ON p.team_bref = w.team_id
WHERE COALESCE(w.war, 0) > 0
ORDER BY m_per_war ASC
"""

_TRADE_WAR_SQL = """
WITH trade_legs AS (
    SELECT
        tm.player_id,
        EXTRACT(YEAR FROM tm.date)::INT AS trade_yr,
        CASE
            WHEN tm.to_team_id   = ? THEN 'acquired'
            WHEN tm.from_team_id = ? THEN 'surrendered'
        END AS direction
    FROM hist.trade_movements tm
    WHERE (tm.to_team_id = ? OR tm.from_team_id = ?)
      AND EXTRACT(YEAR FROM tm.date) >= 2010
),
eligible_players AS (
    SELECT DISTINCT mlb_id FROM hist.bwar_player_seasons
),
war_window AS (
    SELECT
        tl.player_id,
        tl.trade_yr,
        tl.direction,
        COALESCE(SUM(CASE
            WHEN tl.direction = 'acquired'
                 AND b.team_id = 'SDP'
                 AND b.year_id BETWEEN tl.trade_yr AND tl.trade_yr + 4
            THEN b.war
            WHEN tl.direction = 'surrendered'
                 AND b.team_id != 'SDP'
                 AND b.year_id BETWEEN tl.trade_yr AND tl.trade_yr + 4
            THEN b.war
            ELSE 0
        END), 0) AS war_5yr
    FROM trade_legs tl
    INNER JOIN eligible_players ep ON tl.player_id = ep.mlb_id
    LEFT JOIN hist.bwar_player_seasons b ON tl.player_id = b.mlb_id
    GROUP BY tl.player_id, tl.trade_yr, tl.direction
)
SELECT
    ra.gm,
    MIN(ra.season)                                                              AS era_start,
    MAX(ra.season)                                                              AS era_end,
    ROUND(SUM(CASE WHEN ww.direction = 'acquired'   THEN ww.war_5yr ELSE 0 END), 1) AS acq_5yr,
    ROUND(SUM(CASE WHEN ww.direction = 'surrendered' THEN ww.war_5yr ELSE 0 END), 1) AS sur_5yr,
    ROUND(SUM(CASE
                  WHEN ww.direction = 'acquired'    THEN  ww.war_5yr
                  WHEN ww.direction = 'surrendered' THEN -ww.war_5yr
                  ELSE 0
              END), 1)                                                           AS net_5yr
FROM war_window ww
JOIN hist.team_regime_assignments ra
     ON ra.bref_code = 'SDP' AND ra.season = ww.trade_yr
GROUP BY ra.gm
ORDER BY era_start
"""


class DollarPerWarDetector:
    """SD payroll efficiency vs MLB, using most recent complete season."""

    name = "dollar_per_war"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Detect SD's $/WAR rank among MLB teams (least-efficient bottom 10 shown).

        Args:
            conn: Read-mode padres.db with hist attached.
            as_of: Reference date.

        Returns:
            One StatCandidate if SD data is available, else empty list.
        """
        season = as_of.year - 1  # use last complete season

        rows = conn.execute(_DOLLAR_PER_WAR_SQL, [season, season]).fetchall()
        if not rows:
            logger.warning("dollar_per_war: no payroll data for season=%d", season)
            return []

        total_teams = len(rows)

        sd_row_raw = next((r for r in rows if r[0] == _SD_BREF), None)
        if sd_row_raw is None:
            logger.warning("dollar_per_war: SDP not found in season=%d payroll data", season)
            return []

        sd_eff_rank = sd_row_raw[4]

        # Show the 10 least-efficient teams (highest $/WAR = worst value).
        # rows is sorted ASC by m_per_war, so bottom 10 = last 10.
        # If SD is not in the last 10, replace the 10th row with SD.
        bottom_10 = list(rows[-10:])
        if not any(r[0] == _SD_BREF for r in bottom_10):
            bottom_10[-1] = sd_row_raw  # pragma: no cover — SD is rank 22/29

        # Re-sort by eff_rank ascending so display order is consistent
        bottom_10_sorted = sorted(bottom_10, key=lambda r: r[4])

        table_rows = [
            [str(r[4]), r[0], f"${r[1]}M", str(r[2]), f"${r[3]}M"] for r in bottom_10_sorted
        ]

        sd_idx = next((i for i, r in enumerate(bottom_10_sorted) if r[0] == _SD_BREF), None)

        headline = (
            f"In {season}, the Padres spent ${sd_row_raw[3]}M per WAR — "
            f"{_ordinal(int(sd_eff_rank))} of {total_teams} MLB teams "
            f"(${sd_row_raw[1]}M payroll, {sd_row_raw[2]} WAR)"
        )

        payload = TablePayload(
            title=f"{season} MLB Payroll Efficiency",
            subtitle=f"$/WAR — least efficient 10 of {total_teams} MLB teams",
            as_of=as_of,
            columns=["Rank", "Team", "Payroll", "WAR", "$/WAR"],
            rows=table_rows,
            highlight_row=sd_idx,
            source="Spotrac / Baseball Reference",
            headline=headline,
            claim_scope="since_2015",
        )

        facts = {
            **payload.model_dump(mode="json"),
            "season": season,
            "sd_eff_rank": int(sd_eff_rank),
            "sd_total_teams": total_teams,
            "sd_payroll_m": float(sd_row_raw[1]),
            "sd_war": float(sd_row_raw[2]),
            "sd_m_per_war": float(sd_row_raw[3]),
        }

        prov = [
            {
                "source_tables": ["hist.spotrac_player_contracts", "hist.bwar_player_seasons"],
                "sql": _DOLLAR_PER_WAR_SQL.strip(),
                "params": {"season": season},
                "as_of": str(as_of),
            }
        ]

        cid = make_candidate_id(self.name, f"SDP|{season}|payroll_efficiency", facts)

        rarity = max(0.0, 1.0 - (int(sd_eff_rank) - 1) / total_teams)
        novelty = round(0.55 + 0.30 * rarity, 3)

        return [
            StatCandidate(
                candidate_id=cid,
                detector=self.name,
                subject=f"SDP|{season}|payroll_efficiency",
                as_of=as_of,
                category="historical",
                payload_kind="table",
                facts_json=facts,
                provenance_json=prov,
                coverage_window=f"{season}-{season}",
                claim_scope="since_2015",
                novelty_score=novelty,
            )
        ]


class TradeWarDetector:
    """Net WAR returned from trades for each SD GM era (5-year window)."""

    name = "trade_war_balance"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit SD GM-era trade WAR balance from hist tables.

        Args:
            conn: Read-mode padres.db with hist attached.
            as_of: Reference date.

        Returns:
            One StatCandidate summarizing all GM eras, or empty list.
        """
        rows = conn.execute(
            _TRADE_WAR_SQL,
            [_SD_TEAM_ID, _SD_TEAM_ID, _SD_TEAM_ID, _SD_TEAM_ID],
        ).fetchall()

        if not rows:
            logger.warning("trade_war_balance: no trade data found")
            return []

        # Highlight the most recent GM era (last row after ORDER BY era_start)
        highlight_idx = len(rows) - 1
        current_gm_row = rows[highlight_idx]
        current_gm = current_gm_row[0]
        current_net = current_gm_row[5]

        table_rows = [
            [
                r[0],  # GM name
                f"{r[1]}-{r[2]}",  # era
                str(r[3]),  # acq_5yr
                str(r[4]),  # sur_5yr
                str(r[5]),  # net_5yr
            ]
            for r in rows
        ]

        headline = (
            f"SD trade returns by GM era (5-year WAR window): "
            f"{current_gm} sits at {current_net:+.1f} net WAR since {current_gm_row[1]}"
        )

        payload = TablePayload(
            title="Padres Trades by GM Era",
            subtitle="5-year WAR window · acquired vs surrendered",
            as_of=as_of,
            columns=["GM", "Era", "Acq WAR", "Sur WAR", "Net WAR"],
            rows=table_rows,
            highlight_row=highlight_idx,
            source="Baseball Reference / trade-movements",
            headline=headline,
            claim_scope="since_2010",
        )

        facts = {
            **payload.model_dump(mode="json"),
            "current_gm": current_gm,
            "current_net_war": float(current_net),
            "eras": [
                {
                    "gm": r[0],
                    "era_start": r[1],
                    "era_end": r[2],
                    "acq_5yr": float(r[3]),
                    "sur_5yr": float(r[4]),
                    "net_5yr": float(r[5]),
                }
                for r in rows
            ],
        }

        prov = [
            {
                "source_tables": [
                    "hist.trade_movements",
                    "hist.bwar_player_seasons",
                    "hist.team_regime_assignments",
                ],
                "sql": _TRADE_WAR_SQL.strip(),
                "params": {"sd_team_id": _SD_TEAM_ID},
                "as_of": str(as_of),
            }
        ]

        cid = make_candidate_id(self.name, "SDP|trade_war_balance", facts)

        return [
            StatCandidate(
                candidate_id=cid,
                detector=self.name,
                subject="SDP|trade_war_balance",
                as_of=as_of,
                category="historical",
                payload_kind="table",
                facts_json=facts,
                provenance_json=prov,
                coverage_window="2010-present",
                claim_scope="since_2010",
                novelty_score=0.72,
            )
        ]


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


register(DollarPerWarDetector())
register(TradeWarDetector())
