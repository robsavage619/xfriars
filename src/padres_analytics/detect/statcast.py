"""Statcast-powered detectors.

Four detectors pulling from Statcast tables and hist.bwar_player_seasons:

  statcast_profile  — Percentile bar chart for notable Padre hitters
  xstats_unlucky    — MLB xwOBA-wOBA gap leaderboard (biggest underperformers)
  sprint_speed      — MLB sprint-speed leaderboard
  barrel_rate       — MLB barrel-rate leaderboard

Table resolution: for each Statcast table, ``_tbl()`` checks main. (padres.db,
populated by ``pad ingest statcast``) first and falls back to hist. (trades.db).
This means detectors automatically use the most current data available.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.base import register
from padres_analytics.detect.candidates import (
    StatCandidate,
    TablePayload,
    make_candidate_id,
)
from padres_analytics.detect.scoring import novelty_score
from padres_analytics.detect.sql import fmt_name as _fmt_name
from padres_analytics.detect.sql import max_year as _max_year
from padres_analytics.detect.sql import ordinal as _ordinal
from padres_analytics.detect.sql import padre_ids as _padre_ids
from padres_analytics.detect.sql import resolve_table as _tbl

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Statcast percentile rank columns to include in the player profile card.
# Tuples: (column_name, display_label)
# All columns are "higher = better" percentile ranks (0-100).
_PROFILE_METRICS: list[tuple[str, str]] = [
    ("xwoba", "xwOBA"),
    ("exit_velocity", "EXIT VELO"),
    ("brl_percent", "BARREL %"),
    ("hard_hit_percent", "HARD HIT %"),
    ("sprint_speed", "SPRINT SPD"),
    ("k_percent", "K-CONTROL"),
]

# Minimum qualifying thresholds
_MIN_PROFILE_METRICS = 4  # non-null metrics required to emit a profile card
_MIN_PA_XSTATS = 100  # plate appearances for xstats gap
_MIN_COMPETITIVE_RUNS = 10  # sprint speed competitive runs
_MIN_BARREL_ATTEMPTS = 100  # batted ball attempts for barrel rate


def _leaderboard_candidate(
    *,
    conn: duckdb.DuckDBPyConnection,
    as_of: date,
    detector_name: str,
    subject_prefix: str,
    query: str,
    query_params: list,
    padre_ids: set[int],
    id_col_idx: int,
    name_col_idx: int,
    value_col_idx: int,
    table_columns: list[str],
    title: str,
    subtitle: str,
    source: str,
    coverage_window: str,
    claim_scope: str,
    value_fmt: str = "str",
    top_n: int = 10,
) -> StatCandidate | None:
    """Build a leaderboard StatCandidate with the top Padre row highlighted.

    Returns None if no Padre appears in the top ``top_n`` rows.

    Args:
        conn: DB connection with hist attached.
        as_of: Reference date.
        detector_name: Detector name string (for candidate ID).
        subject_prefix: Subject string prefix.
        query: SQL query returning rows ordered best-first. Must return at least
            25 rows to give the Padre a chance to appear.
        query_params: Positional params for the query.
        padre_ids: Set of MLBAM IDs for current Padres.
        id_col_idx: Index into each row tuple for the player_id.
        name_col_idx: Index into each row tuple for the player_name.
        value_col_idx: Index into each row tuple for the primary display value.
        table_columns: Column headers for the TablePayload.
        title: Card title.
        subtitle: Card subtitle.
        source: Data source label.
        coverage_window: Coverage window string.
        claim_scope: Claim scope string.
        value_fmt: One of 'str', 'decimal3', 'decimal1', 'pct1'.
        top_n: Rows to show (default 10).

    Returns:
        StatCandidate or None.
    """
    rows = conn.execute(query, query_params).fetchall()
    if not rows:
        return None

    def fmt_val(v: str | int | float | None) -> str:
        if value_fmt == "decimal3" and v is not None:
            return f"{float(v):.3f}"
        if value_fmt == "decimal1" and v is not None:
            return f"{float(v):.1f}"
        if value_fmt == "pct1" and v is not None:
            return f"{float(v):.1f}%"
        return str(v)

    # Find the highest-ranked Padre in the result
    padre_rank_pos: int | None = None
    for i, row in enumerate(rows):
        if row[id_col_idx] in padre_ids:
            padre_rank_pos = i
            break

    if padre_rank_pos is None or padre_rank_pos >= top_n:
        return None

    display_rows = list(rows[:top_n])
    padre_idx = padre_rank_pos  # 0-based index in display_rows

    table_rows: list[list[str | int | float]] = [
        [str(i + 1), _fmt_name(str(row[name_col_idx])), fmt_val(row[value_col_idx])]
        for i, row in enumerate(display_rows)
    ]

    padre_row = display_rows[padre_idx]
    padre_name = _fmt_name(str(padre_row[name_col_idx]))
    padre_val = fmt_val(padre_row[value_col_idx])
    padre_rank = padre_idx + 1

    # Build the headline from the Padre's position
    stat_label = table_columns[-1]
    headline = (
        f"{padre_name} ranks #{padre_rank} in MLB {stat_label} ({padre_val}) — {as_of.isoformat()}"
    )

    facts: dict = {
        "padre_name": padre_name,
        "padre_rank": padre_rank,
        "padre_value": padre_val,
        "padre_player_id": padre_row[id_col_idx],
        "total_shown": len(display_rows),
    }

    payload = TablePayload(
        title=title,
        subtitle=subtitle,
        as_of=as_of,
        columns=["#", "Player", table_columns[-1]],
        rows=table_rows,
        highlight_row=padre_idx,
        source=source,
        headline=headline,
        claim_scope=claim_scope,
    )

    rank_rarity = max(0.0, 1.0 - (padre_rank - 1) / top_n)
    score, components = novelty_score(
        {
            "rarity": rank_rarity,
            "magnitude": 0.75,
            "timeliness": 0.8,
            "rootability": 0.85,
            "legibility": 0.9,
        },
        detector=detector_name,
    )

    cid = make_candidate_id(
        detector_name,
        f"{subject_prefix}|{as_of.isoformat()}",
        {**payload.model_dump(mode="json"), **facts},
    )

    return StatCandidate(
        candidate_id=cid,
        detector=detector_name,
        subject=f"{subject_prefix}|{as_of.isoformat()}",
        as_of=as_of,
        category="season",
        payload_kind="table",
        facts_json={**payload.model_dump(mode="json"), **facts},
        provenance_json=[
            {
                "source_table": f"hist.{detector_name.replace('_', '')}",
                "as_of": str(as_of),
            }
        ],
        coverage_window=coverage_window,
        claim_scope=claim_scope,
        novelty_score=score,
        novelty_components=components,
    )


# ── statcast_profile ─────────────────────────────────────────────────────────


class StatcastProfileDetector:
    """Emits a Statcast percentile bar chart for each notable Padre hitter.

    Pulls from hist.statcast_batter_percentile_ranks (latest complete season).
    Each Padre with >= _MIN_PROFILE_METRICS non-null metrics gets a candidate.
    The "value" column holds the raw 0-100 percentile; bars scale relative to
    the player's own best tool (not a leaderboard comparison).
    """

    name = "statcast_profile"

    def run(
        self,
        conn: duckdb.DuckDBPyConnection,
        as_of: date,
    ) -> list[StatCandidate]:
        """Run the statcast_profile detector.

        Args:
            conn: Read-only padres.db connection with hist attached.
            as_of: Reference date.

        Returns:
            One candidate per qualifying Padre hitter.
        """
        statcast_year = _max_year(conn, "statcast_batter_percentile_ranks")
        if statcast_year is None:
            logger.warning("statcast_profile: no data in hist.statcast_batter_percentile_ranks")
            return []

        # Use bwar season matching the Statcast data (not necessarily current year)
        bwar_year = statcast_year if statcast_year <= as_of.year else as_of.year
        padre_ids = _padre_ids(conn, bwar_year)
        if not padre_ids:
            logger.warning("statcast_profile: no SDP players in bwar year=%d", bwar_year)
            return []

        metric_cols = ", ".join(f"s.{col}" for col, _ in _PROFILE_METRICS)
        placeholders = ",".join("?" * len(padre_ids))

        src = _tbl(conn, "statcast_batter_percentile_ranks")
        rows = conn.execute(
            f"""
            SELECT s.player_id, s.player_name, {metric_cols}
            FROM {src} s
            WHERE s.year = ?
              AND s.player_id IN ({placeholders})
            ORDER BY s.player_name
            """,
            [statcast_year, *sorted(padre_ids)],
        ).fetchall()

        if not rows:
            logger.debug("statcast_profile: no matching rows for year=%d", statcast_year)
            return []

        candidates: list[StatCandidate] = []
        for row in rows:
            player_id = row[0]
            player_name = _fmt_name(str(row[1]))
            # Metric values start at index 2
            metric_values: list[tuple[str, float | None]] = [
                (label, row[2 + i] if row[2 + i] is not None else None)
                for i, (_, label) in enumerate(_PROFILE_METRICS)
            ]

            valid = [(label, v) for label, v in metric_values if v is not None]
            if len(valid) < _MIN_PROFILE_METRICS:
                continue

            # Build bar-chart rows: ["", label, percentile_value]
            table_rows = [["", label, round(v, 0)] for label, v in valid]

            # Highlight the strongest metric
            best_idx = max(range(len(valid)), key=lambda i: valid[i][1])

            # Profile score = average percentile (used for novelty/rarity)
            avg_pctile = sum(v for _, v in valid) / len(valid)
            max_pctile = max(v for _, v in valid)

            # Ordinal summary of top metric
            best_label, best_val = valid[best_idx]
            headline = (
                f"{player_name} is in the {_ordinal(best_val)} percentile in "
                f"{best_label} ({statcast_year} Statcast) "
                f"— avg percentile {avg_pctile:.0f}"
            )

            facts: dict = {
                "player_id": player_id,
                "player_name": player_name,
                "statcast_year": statcast_year,
                "bwar_year": bwar_year,
                "avg_percentile": round(avg_pctile, 1),
                "best_metric": best_label,
                "best_percentile": best_val,
                "metrics": {label: v for label, v in valid},
            }

            # "Fernando Tatis Jr." → "TATIS JR."
            name_parts = player_name.split()
            last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else player_name

            payload = TablePayload(
                title=f"{last_name.upper()} — STATCAST PROFILE",
                subtitle=f"{statcast_year} Season · Statcast Percentile Rankings",
                as_of=as_of,
                columns=["", "Metric", "Pctile"],
                rows=table_rows,
                highlight_row=best_idx,
                source="Baseball Savant",
                headline=headline,
                claim_scope="since_2015",
            )

            # Novelty: elite profiles and high-contrast profiles are more interesting
            rarity = max_pctile / 100.0
            magnitude = avg_pctile / 100.0
            score, components = novelty_score(
                {
                    "rarity": rarity,
                    "magnitude": magnitude,
                    "timeliness": 0.75,
                    "rootability": 0.85,
                    "legibility": 0.95,
                },
                detector=self.name,
            )

            cid = make_candidate_id(
                self.name,
                f"SDP|{player_id}|{statcast_year}",
                {**payload.model_dump(mode="json"), **facts},
            )

            candidates.append(
                StatCandidate(
                    candidate_id=cid,
                    detector=self.name,
                    subject=f"SDP|{player_id}|{statcast_year}",
                    as_of=as_of,
                    category="season",
                    payload_kind="table",
                    facts_json={**payload.model_dump(mode="json"), **facts},
                    provenance_json=[
                        {
                            "source_table": "hist.statcast_batter_percentile_ranks",
                            "sql": (
                                "SELECT player_id, player_name, xwoba, exit_velocity, "
                                "brl_percent, hard_hit_percent, sprint_speed, k_percent "
                                f"FROM hist.statcast_batter_percentile_ranks "
                                f"WHERE year = {statcast_year} AND player_id = {player_id}"
                            ),
                            "as_of": str(as_of),
                        }
                    ],
                    coverage_window=f"{statcast_year}-{statcast_year}",
                    claim_scope="since_2015",
                    novelty_score=score,
                    novelty_components=components,
                )
            )

        return candidates


# ── xstats_unlucky ────────────────────────────────────────────────────────────


class XStatsUnluckyDetector:
    """MLB leaderboard of hitters most outperformed by their expected stats.

    A large positive gap (xwOBA >> actual wOBA) means the player's quality of
    contact deserves better results — they're running unlucky. The Padre with
    the largest gap gets highlighted.

    Uses hist.statcast_batting_expected, which tracks current-season data.
    """

    name = "xstats_unlucky"

    def run(
        self,
        conn: duckdb.DuckDBPyConnection,
        as_of: date,
    ) -> list[StatCandidate]:
        """Run xstats_unlucky detection.

        Args:
            conn: Read-only padres.db connection with hist attached.
            as_of: Reference date.

        Returns:
            Zero or one candidate.
        """
        season = as_of.year
        padre_ids = _padre_ids(conn, season)
        if not padre_ids:
            logger.debug("xstats_unlucky: no SDP players in bwar year=%d", season)
            return []

        src_expected = _tbl(conn, "statcast_batting_expected")
        rows = conn.execute(
            f"""
            SELECT player_id, player_name, pa, woba, est_woba,
                   ROUND(est_woba - woba, 3) AS gap
            FROM {src_expected}
            WHERE year = ?
              AND pa >= ?
            ORDER BY gap DESC
            LIMIT 25
            """,
            [season, _MIN_PA_XSTATS],
        ).fetchall()

        if not rows:
            logger.debug("xstats_unlucky: no data for year=%d", season)
            return []

        # Find the top Padre
        padre_rank_pos: int | None = None
        for i, row in enumerate(rows):
            if row[0] in padre_ids:
                padre_rank_pos = i
                break

        if padre_rank_pos is None or padre_rank_pos >= 10:
            return []

        display = rows[:10]
        padre_idx = padre_rank_pos
        padre_row = display[padre_idx]
        padre_name = _fmt_name(str(padre_row[1]))
        padre_rank = padre_idx + 1
        gap = padre_row[5]
        actual = padre_row[3]
        expected = padre_row[4]

        table_rows: list[list[str | int | float]] = [
            [
                str(i + 1),
                _fmt_name(str(r[1])),
                str(r[2]),
                f"{r[3]:.3f}",
                f"{r[4]:.3f}",
                f"+{r[5]:.3f}" if r[5] >= 0 else f"{r[5]:.3f}",
            ]
            for i, r in enumerate(display)
        ]

        headline = (
            f"{padre_name} ranks #{padre_rank} in MLB for xwOBA gap "
            f"(actual {actual:.3f} vs. expected {expected:.3f} = +{gap:.3f}) "
            f"in {season} — running unlucky, contact quality not showing in results"
        )

        facts: dict = {
            "season": season,
            "padre_name": padre_name,
            "padre_rank": padre_rank,
            "padre_player_id": padre_row[0],
            "padre_woba": actual,
            "padre_xwoba": expected,
            "padre_gap": gap,
        }

        payload = TablePayload(
            title="MOST UNLUCKY HITTERS",
            subtitle=f"xwOBA vs. Actual wOBA Gap · {season} Season · Min {_MIN_PA_XSTATS} PA",
            as_of=as_of,
            columns=["#", "Player", "PA", "wOBA", "xwOBA", "Gap"],
            rows=table_rows,
            highlight_row=padre_idx,
            source="Baseball Savant",
            headline=headline,
            claim_scope="since_2015",
        )

        rank_rarity = max(0.0, 1.0 - (padre_rank - 1) / 10)
        score, components = novelty_score(
            {
                "rarity": rank_rarity,
                "magnitude": min(abs(gap) / 0.10, 1.0),
                "timeliness": 0.95,
                "rootability": 0.85,
                "legibility": 0.9,
            },
            detector=self.name,
        )

        cid = make_candidate_id(
            self.name,
            f"SDP|{season}|xstats_gap",
            {**payload.model_dump(mode="json"), **facts},
        )

        return [
            StatCandidate(
                candidate_id=cid,
                detector=self.name,
                subject=f"SDP|{season}|xstats_gap",
                as_of=as_of,
                category="season",
                payload_kind="table",
                facts_json={**payload.model_dump(mode="json"), **facts},
                provenance_json=[
                    {
                        "source_table": "hist.statcast_batting_expected",
                        "sql": (
                            "SELECT player_id, player_name, pa, woba, est_woba, "
                            f"ROUND(est_woba - woba, 3) AS gap "
                            f"FROM hist.statcast_batting_expected "
                            f"WHERE year = {season} AND pa >= {_MIN_PA_XSTATS} "
                            "ORDER BY gap DESC LIMIT 25"
                        ),
                        "as_of": str(as_of),
                    }
                ],
                coverage_window=f"{season}-{season}",
                claim_scope="since_2015",
                novelty_score=score,
                novelty_components=components,
            )
        ]


# ── sprint_speed ──────────────────────────────────────────────────────────────


class SprintSpeedDetector:
    """MLB top-10 sprint speed leaderboard with the fastest Padre highlighted.

    Uses hist.statcast_sprint_speed. Fires only when a Padre ranks in the top 10.
    """

    name = "sprint_speed"

    def run(
        self,
        conn: duckdb.DuckDBPyConnection,
        as_of: date,
    ) -> list[StatCandidate]:
        """Run sprint_speed detection.

        Args:
            conn: Read-only padres.db connection with hist attached.
            as_of: Reference date.

        Returns:
            Zero or one candidate.
        """
        speed_year = _max_year(conn, "statcast_sprint_speed")
        if speed_year is None:
            return []

        bwar_year = speed_year if speed_year <= as_of.year else as_of.year
        padre_ids = _padre_ids(conn, bwar_year)

        src_speed = _tbl(conn, "statcast_sprint_speed")
        cand = _leaderboard_candidate(
            conn=conn,
            as_of=as_of,
            detector_name=self.name,
            subject_prefix=f"SDP|sprint_speed|{speed_year}",
            query=f"""
                SELECT player_id, player_name, sprint_speed
                FROM {src_speed}
                WHERE year = ?
                  AND competitive_runs >= ?
                ORDER BY sprint_speed DESC
                LIMIT 25
            """,
            query_params=[speed_year, _MIN_COMPETITIVE_RUNS],
            padre_ids=padre_ids,
            id_col_idx=0,
            name_col_idx=1,
            value_col_idx=2,
            table_columns=["#", "Player", "ft/s"],
            title=f"{speed_year} MLB SPRINT SPEED",
            subtitle=(
                f"Fastest runners in baseball · {speed_year} Season"
                f" · Min {_MIN_COMPETITIVE_RUNS} tracked runs"
            ),
            source="Baseball Savant",
            coverage_window=f"{speed_year}-{speed_year}",
            claim_scope="since_2015",
            value_fmt="decimal1",
        )

        return [cand] if cand else []


# ── barrel_rate ───────────────────────────────────────────────────────────────


class BarrelRateDetector:
    """MLB top-10 barrel-rate leaderboard with the hardest-hitting Padre highlighted.

    Uses hist.statcast_batter_exitvelo_barrels. Fires only when a Padre ranks top 10.
    """

    name = "barrel_rate"

    def run(
        self,
        conn: duckdb.DuckDBPyConnection,
        as_of: date,
    ) -> list[StatCandidate]:
        """Run barrel_rate detection.

        Args:
            conn: Read-only padres.db connection with hist attached.
            as_of: Reference date.

        Returns:
            Zero or one candidate.
        """
        barrel_year = _max_year(conn, "statcast_batter_exitvelo_barrels")
        if barrel_year is None:
            return []

        bwar_year = barrel_year if barrel_year <= as_of.year else as_of.year
        padre_ids = _padre_ids(conn, bwar_year)

        src_barrels = _tbl(conn, "statcast_batter_exitvelo_barrels")
        cand = _leaderboard_candidate(
            conn=conn,
            as_of=as_of,
            detector_name=self.name,
            subject_prefix=f"SDP|barrel_rate|{barrel_year}",
            query=f"""
                SELECT player_id, player_name, attempts, brl_percent
                FROM {src_barrels}
                WHERE year = ?
                  AND attempts >= ?
                ORDER BY brl_percent DESC
                LIMIT 25
            """,
            query_params=[barrel_year, _MIN_BARREL_ATTEMPTS],
            padre_ids=padre_ids,
            id_col_idx=0,
            name_col_idx=1,
            value_col_idx=3,
            table_columns=["#", "Player", "Brl%"],
            title=f"{barrel_year} MLB BARREL RATE",
            subtitle=(
                f"Hardest hitters by barrel rate · {barrel_year} Season"
                f" · Min {_MIN_BARREL_ATTEMPTS} batted balls"
            ),
            source="Baseball Savant",
            coverage_window=f"{barrel_year}-{barrel_year}",
            claim_scope="since_2015",
            value_fmt="pct1",
        )

        return [cand] if cand else []


# ── Registration ──────────────────────────────────────────────────────────────

register(StatcastProfileDetector())
register(XStatsUnluckyDetector())
register(SprintSpeedDetector())
register(BarrelRateDetector())
