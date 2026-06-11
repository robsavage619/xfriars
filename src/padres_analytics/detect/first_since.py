"""First-since detector — season feats whose novelty is a counted historical fact.

The Codify pattern: the SQL that finds the feat also counts its precedents.
"First Padre with a 6-WAR season since Jake Peavy (2007)" — the gap and the
occurrence count ARE the novelty score, not tuned weights.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from padres_analytics.detect.base import register
from padres_analytics.detect.candidates import StatCandidate, TablePayload, make_candidate_id

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_SD_BREF = "SDP"
_FRANCHISE_FOUNDED = 1969
_TABLE_ROWS = 10
_REEMIT_DAYS = 30

# Season-WAR thresholds, highest first. A player is credited with the highest
# threshold their accrued season WAR has crossed. 2-WAR seasons are routine —
# the floor is 3.
_WAR_THRESHOLDS = (8.0, 7.0, 6.0, 5.0, 4.0, 3.0)

# Fire only when the drought is long or the feat is genuinely rare.
_MIN_YEARS_SINCE = 5
_RARE_COUNT_MAX = 4  # ... or at most this many prior occurrences ever


def _current_season(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute(
        f"SELECT MAX(year_id) FROM hist.bwar_player_seasons WHERE team_id = '{_SD_BREF}'"
    ).fetchone()
    return row[0] if row and row[0] else date.today().year


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


class WarSeasonFirstSinceDetector:
    """Emits when an active Padre's season WAR crosses a historically rare threshold.

    Accrued WAR only — no projection. Crossing 6 WAR in August fires
    immediately; a 2.1-WAR June is correctly silent.
    """

    name = "first_since"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Detect threshold-crossing season WAR feats with long droughts.

        Args:
            conn: Read-mode padres.db connection with hist attached.
            as_of: Reference date for the detection run.

        Returns:
            One StatCandidate per qualifying player, limited by the re-emit gate.
        """
        season = _current_season(conn)

        # Current-season WAR per active Padre (sum across role/stint rows)
        current = conn.execute(
            """
            SELECT mlb_id, ANY_VALUE(name_common), ROUND(SUM(war), 1) AS season_war
            FROM hist.bwar_player_seasons
            WHERE team_id = ? AND year_id = ?
            GROUP BY mlb_id
            HAVING SUM(war) >= ?
            ORDER BY season_war DESC
            """,
            [_SD_BREF, season, min(_WAR_THRESHOLDS)],
        ).fetchall()
        if not current:
            return []

        candidates: list[StatCandidate] = []

        for mlb_id, name, season_war in current:
            threshold = next((t for t in _WAR_THRESHOLDS if season_war >= t), None)
            if threshold is None:
                continue

            # Every prior Padre season at or above the threshold — the precedents.
            priors = conn.execute(
                """
                SELECT year_id, ANY_VALUE(name_common), ROUND(SUM(war), 1) AS yr_war
                FROM hist.bwar_player_seasons
                WHERE team_id = ? AND year_id < ?
                GROUP BY mlb_id, year_id
                HAVING SUM(war) >= ?
                ORDER BY year_id DESC
                """,
                [_SD_BREF, season, threshold],
            ).fetchall()

            n_prior = len(priors)
            if n_prior == 0:
                years_since = season - _FRANCHISE_FOUNDED
                headline = (
                    f"{name} has the first {threshold:.0f}-WAR season "
                    f"in Padres history ({season_war} WAR in {season})"
                )
            else:
                last_year, last_name, _last_war = priors[0]
                years_since = season - last_year
                if years_since < _MIN_YEARS_SINCE and n_prior > _RARE_COUNT_MAX:
                    continue
                occurrence = _ordinal(n_prior + 1)
                headline = (
                    f"{name} ({season_war} WAR) has the first {threshold:.0f}-WAR "
                    f"Padres season since {last_name} in {last_year} — "
                    f"just the {occurrence} in franchise history"
                )

            subject = f"SDP|first_since|war{threshold:.0f}|{mlb_id}|{season}"
            if _recently_emitted(conn, subject, as_of):
                logger.debug("first_since: skipping %s — emitted recently", name)
                continue

            # Table: current season on top (highlighted), precedents below.
            table_rows: list[list[str | int | float]] = [[str(season), name, str(season_war)]]
            for yr, p_name, p_war in priors[: _TABLE_ROWS - 1]:
                table_rows.append([str(yr), p_name, str(p_war)])

            # Novelty IS the counted history: drought length + scarcity.
            drought_part = min(0.25, years_since * 0.01)
            scarcity_part = 0.10 if n_prior <= _RARE_COUNT_MAX else 0.0
            novelty = min(0.97, 0.62 + drought_part + scarcity_part)

            coverage = f"{_FRANCHISE_FOUNDED}-{season}"
            claim = f"since_{_FRANCHISE_FOUNDED}"

            payload = TablePayload(
                title=f"{threshold:.0f}-WAR Seasons in Padres History",
                subtitle=f"Single-season bWAR · through {as_of}",
                as_of=as_of,
                columns=["Year", "Player", "WAR"],
                rows=table_rows,
                highlight_row=0,
                source="Baseball Reference",
                headline=headline,
                claim_scope=claim,
            )

            facts = {
                **payload.model_dump(mode="json"),
                "player_id": mlb_id,
                "player_name": name,
                "season": season,
                "season_war": float(season_war),
                "threshold": float(threshold),
                "prior_occurrences": n_prior,
                "years_since_last": years_since,
            }

            prov = [
                {
                    "source_table": "hist.bwar_player_seasons",
                    "sql": (
                        "SELECT year_id, SUM(war) FROM hist.bwar_player_seasons "
                        "WHERE team_id=? AND year_id<? GROUP BY mlb_id, year_id "
                        "HAVING SUM(war)>=?"
                    ),
                    "params": [_SD_BREF, season, threshold],
                    "as_of": str(as_of),
                }
            ]

            cid = make_candidate_id(self.name, subject, facts)

            candidates.append(
                StatCandidate(
                    candidate_id=cid,
                    detector=self.name,
                    subject=subject,
                    as_of=as_of,
                    category="season",
                    payload_kind="table",
                    facts_json=facts,
                    provenance_json=prov,
                    coverage_window=coverage,
                    claim_scope=claim,
                    novelty_score=novelty,
                    novelty_components={
                        "rarity": round(scarcity_part / 0.10, 2) if scarcity_part else 0.0,
                        "magnitude": round(threshold / max(_WAR_THRESHOLDS), 2),
                        "timeliness": round(drought_part / 0.25, 2),
                    },
                )
            )

        return candidates


register(WarSeasonFirstSinceDetector())
