"""On This Day detector — franchise history from game_logs and transactions."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from padres_analytics.config import BREF
from padres_analytics.detect.base import register
from padres_analytics.detect.candidates import (
    StatCandidate,
    TablePayload,
    make_candidate_id,
)
from padres_analytics.detect.scoring import novelty_score

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Los_Angeles")

# Coverage metadata — tied to hist.game_logs date range
_GAMELOGS_COVERAGE = "1990-2024"
_GAMELOGS_CLAIM = "since_1990"
_TRANSACTIONS_YEAR_MIN = 2010
_TRANSACTIONS_COVERAGE = f"{_TRANSACTIONS_YEAR_MIN}-2026"
_TRANSACTIONS_CLAIM = f"since_{_TRANSACTIONS_YEAR_MIN}"

# Type codes worth surfacing in On This Day
_NOTABLE_TX_CODES = {"TR", "SFA", "REL", "RET", "CLW"}


def _la_today(as_of: date) -> tuple[int, int]:
    """Return (month, day) for the reference date.

    Feb 29 maps to Mar 1 in non-leap years so the detector always fires.

    Args:
        as_of: The reference date.

    Returns:
        (month, day) tuple.
    """
    return (as_of.month, as_of.day)


def _game_results(conn: duckdb.DuckDBPyConnection, month: int, day: int) -> list[dict]:
    """Query Padres game results on (month, day) from hist.game_logs.

    Args:
        conn: Connection with hist attached.
        month: Calendar month.
        day: Calendar day.

    Returns:
        List of row dicts, sorted by game_date descending.
    """
    rows = conn.execute(
        """
        SELECT
            EXTRACT(YEAR FROM game_date)::INTEGER AS year,
            CASE
                WHEN home_team_bref = ? THEN visitor_team_bref
                ELSE home_team_bref
            END AS opponent,
            CASE
                WHEN home_team_bref = ? THEN home_score
                ELSE visitor_score
            END AS padres_score,
            CASE
                WHEN home_team_bref = ? THEN visitor_score
                ELSE home_score
            END AS opp_score,
            CASE WHEN home_team_bref = ? THEN 'H' ELSE 'A' END AS home_away,
            game_date
        FROM hist.game_logs
        WHERE
            EXTRACT(MONTH FROM game_date) = ?
            AND EXTRACT(DAY   FROM game_date) = ?
            AND (home_team_bref = ? OR visitor_team_bref = ?)
        ORDER BY game_date DESC
        """,
        [BREF, BREF, BREF, BREF, month, day, BREF, BREF],
    ).fetchall()
    return [
        {
            "year": r[0],
            "opponent": r[1],
            "padres_score": r[2],
            "opp_score": r[3],
            "home_away": r[4],
            "game_date": str(r[5]),
        }
        for r in rows
    ]


def _notable_transactions(
    conn: duckdb.DuckDBPyConnection,
    month: int,
    day: int,
) -> list[dict]:
    """Query notable Padres transactions on (month, day) from hist.transactions.

    Args:
        conn: Connection with hist attached.
        month: Calendar month.
        day: Calendar day.

    Returns:
        List of row dicts, sorted by date descending.
    """
    codes_placeholder = ",".join("?" * len(_NOTABLE_TX_CODES))
    rows = conn.execute(
        f"""
        SELECT
            EXTRACT(YEAR FROM date)::INTEGER AS year,
            type_code,
            type_desc,
            player_name,
            description,
            COALESCE(from_team_name, '') AS from_team,
            COALESCE(to_team_name, '')   AS to_team,
            date
        FROM hist.transactions
        WHERE
            EXTRACT(MONTH FROM date) = ?
            AND EXTRACT(DAY   FROM date) = ?
            AND EXTRACT(YEAR  FROM date) >= ?
            AND type_code IN ({codes_placeholder})
            AND (from_team_name LIKE '%Padres%' OR to_team_name LIKE '%Padres%')
        ORDER BY date DESC
        LIMIT 20
        """,
        [month, day, _TRANSACTIONS_YEAR_MIN, *sorted(_NOTABLE_TX_CODES)],
    ).fetchall()
    return [
        {
            "year": r[0],
            "type_code": r[1],
            "type_desc": r[2],
            "player_name": r[3],
            "description": r[4],
            "from_team": r[5],
            "to_team": r[6],
            "date": str(r[7]),
        }
        for r in rows
    ]


def _build_game_results_candidate(
    rows: list[dict],
    month: int,
    day: int,
    as_of: date,
) -> StatCandidate | None:
    """Build a StatCandidate from game_logs results.

    Args:
        rows: Game result dicts from _game_results.
        month: Calendar month.
        day: Calendar day.
        as_of: Reference date.

    Returns:
        A StatCandidate, or None if no rows.
    """
    if not rows:
        return None

    wins = sum(
        1
        for r in rows
        if r["padres_score"] is not None
        and r["opp_score"] is not None
        and r["padres_score"] > r["opp_score"]
    )
    losses = sum(
        1
        for r in rows
        if r["padres_score"] is not None
        and r["opp_score"] is not None
        and r["padres_score"] < r["opp_score"]
    )
    total = len(rows)

    month_names = [
        "",
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    date_label = f"{month_names[month]} {day}"

    # Best and worst margins for the headline
    margins = [
        r["padres_score"] - r["opp_score"]
        for r in rows
        if r["padres_score"] is not None and r["opp_score"] is not None
    ]
    best_margin = max(margins) if margins else 0
    worst_margin = min(margins) if margins else 0

    # Table: top 10 results, most recent first
    display_rows = rows[:10]
    table_rows = [
        [
            str(r["year"]),
            r["opponent"] or "???",
            "W"
            if (r["padres_score"] or 0) > (r["opp_score"] or 0)
            else "L"
            if (r["padres_score"] or 0) < (r["opp_score"] or 0)
            else "T",
            f"{r['padres_score']}-{r['opp_score']}" if r["padres_score"] is not None else "—",
            r["home_away"],
        ]
        for r in display_rows
    ]

    facts: dict = {
        "date_label": date_label,
        "wins": wins,
        "losses": losses,
        "total_games": total,
        "best_margin": best_margin,
        "worst_margin": worst_margin,
        "coverage": _GAMELOGS_COVERAGE,
        "games": [
            {
                "year": r["year"],
                "opponent": r["opponent"],
                "padres_score": r["padres_score"],
                "opp_score": r["opp_score"],
            }
            for r in rows
        ],
    }

    win_pct = wins / total if total > 0 else 0.0
    headline = f"Padres are {wins}-{losses} on {date_label} since 1990" + (
        f", best margin +{best_margin}" if best_margin >= 5 else ""
    )

    payload = TablePayload(
        title=f"Padres on {date_label}",
        subtitle=f"since 1990 · {wins}W-{losses}L in {total} games",
        as_of=as_of,
        columns=["Year", "Opp", "W/L", "Score", "H/A"],
        rows=table_rows,
        highlight_row=None,
        source="Baseball-Reference via savage-trade-evaluator",
        headline=headline,
        claim_scope=_GAMELOGS_CLAIM,
    )

    # Rarity: higher if we have unusual records (lopsided W/L)
    rarity = min(1.0, abs(win_pct - 0.5) * 4)
    # Magnitude: based on total games (more history = more compelling)
    magnitude = min(1.0, total / 20.0)
    # Timeliness: always moderate for On This Day
    timeliness = 0.5
    # Rootability: simple table, easily shareable
    rootability = 0.6
    # Legibility: very clear concept
    legibility = 0.9

    score, components = novelty_score(
        {
            "rarity": rarity,
            "magnitude": magnitude,
            "timeliness": timeliness,
            "rootability": rootability,
            "legibility": legibility,
        },
        detector="on_this_day",
    )

    cid = make_candidate_id("on_this_day", f"SDP|{date_label}|gamelogs", facts)

    return StatCandidate(
        candidate_id=cid,
        detector="on_this_day",
        subject=f"SDP|{date_label}",
        as_of=as_of,
        category="historical",
        payload_kind="table",
        facts_json={**payload.model_dump(mode="json"), **facts},
        provenance_json=[
            {
                "source_table": "hist.game_logs",
                "sql": "SELECT ... FROM hist.game_logs WHERE month=? AND day=? AND team=?",
                "params": {"month": month, "day": day, "team": BREF},
                "as_of": str(as_of),
            }
        ],
        coverage_window=_GAMELOGS_COVERAGE,
        claim_scope=_GAMELOGS_CLAIM,
        novelty_score=score,
        novelty_components=components,
    )


def _build_transaction_candidate(
    rows: list[dict],
    month: int,
    day: int,
    as_of: date,
) -> StatCandidate | None:
    """Build a StatCandidate from notable transactions.

    Args:
        rows: Transaction dicts from _notable_transactions.
        month: Calendar month.
        day: Calendar day.
        as_of: Reference date.

    Returns:
        A StatCandidate, or None if no rows.
    """
    if not rows:
        return None

    month_names = [
        "",
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    date_label = f"{month_names[month]} {day}"

    # Prefer trades over other types
    sorted_rows = sorted(
        rows,
        key=lambda r: (0 if r["type_code"] == "TR" else 1, -r["year"]),
    )
    display_rows = sorted_rows[:10]

    type_labels = {
        "TR": "Trade",
        "SFA": "Free Agent",
        "REL": "Released",
        "RET": "Retired",
        "CLW": "Waivers",
    }

    table_rows = [
        [
            str(r["year"]),
            type_labels.get(r["type_code"], r["type_desc"] or r["type_code"]),
            r["player_name"] or "—",
            # Keep description under ~50 chars
            (r["description"] or "")[:50] + ("…" if len(r["description"] or "") > 50 else ""),
        ]
        for r in display_rows
    ]

    trades = [r for r in rows if r["type_code"] == "TR"]
    trade_count = len(trades)
    headline_player = display_rows[0]["player_name"] or "players"
    headline = (
        f"Padres made {trade_count} trade{'s' if trade_count != 1 else ''} on {date_label}"
        if trade_count > 1
        else f"Padres activity on {date_label} includes {headline_player}"
    )

    facts: dict = {
        "date_label": date_label,
        "total_transactions": len(rows),
        "trade_count": trade_count,
        "coverage": _TRANSACTIONS_COVERAGE,
        "transactions": [
            {
                "year": r["year"],
                "type_code": r["type_code"],
                "player_name": r["player_name"],
                "description": r["description"],
            }
            for r in rows
        ],
    }

    payload = TablePayload(
        title=f"Padres on {date_label}",
        subtitle="notable moves — since 2010",
        as_of=as_of,
        columns=["Year", "Type", "Player", "Detail"],
        rows=table_rows,
        highlight_row=None,
        source="MLB Transactions via savage-trade-evaluator",
        headline=headline,
        claim_scope=_TRANSACTIONS_CLAIM,
    )

    # Trades are inherently more novel
    rarity = min(1.0, 0.3 + trade_count * 0.2)
    magnitude = min(1.0, len(rows) / 5.0)
    timeliness = 0.5
    rootability = 0.7 if trade_count > 0 else 0.4
    legibility = 0.8

    score, components = novelty_score(
        {
            "rarity": rarity,
            "magnitude": magnitude,
            "timeliness": timeliness,
            "rootability": rootability,
            "legibility": legibility,
        },
        detector="on_this_day",
    )

    cid = make_candidate_id("on_this_day", f"SDP|{date_label}|transactions", facts)

    return StatCandidate(
        candidate_id=cid,
        detector="on_this_day",
        subject=f"SDP|{date_label}|moves",
        as_of=as_of,
        category="historical",
        payload_kind="table",
        facts_json={**payload.model_dump(mode="json"), **facts},
        provenance_json=[
            {
                "source_table": "hist.transactions",
                "sql": (
                    "SELECT ... FROM hist.transactions "
                    "WHERE month=? AND day=? AND team LIKE '%Padres%'"
                ),
                "params": {"month": month, "day": day},
                "as_of": str(as_of),
            }
        ],
        coverage_window=_TRANSACTIONS_COVERAGE,
        claim_scope=_TRANSACTIONS_CLAIM,
        novelty_score=score,
        novelty_components=components,
    )


class OnThisDayDetector:
    """Emits franchise history candidates for today's calendar date."""

    name = "on_this_day"

    def run(
        self,
        conn: duckdb.DuckDBPyConnection,
        as_of: date,
    ) -> list[StatCandidate]:
        """Run On This Day detection.

        Args:
            conn: Read-only padres.db connection with hist attached.
            as_of: Reference date in America/Los_Angeles.

        Returns:
            List of StatCandidate objects (0, 1, or 2).
        """
        month, day = _la_today(as_of)
        candidates: list[StatCandidate] = []

        # Game results from hist.game_logs
        try:
            game_rows = _game_results(conn, month, day)
        except Exception as exc:
            logger.error("on_this_day: game_logs query failed: %s", exc)
            game_rows = []

        # Transactions from hist.transactions
        try:
            tx_rows = _notable_transactions(conn, month, day)
        except Exception as exc:
            logger.error("on_this_day: transactions query failed: %s", exc)
            tx_rows = []

        game_candidate = _build_game_results_candidate(game_rows, month, day, as_of)
        tx_candidate = _build_transaction_candidate(tx_rows, month, day, as_of)

        if game_candidate:
            candidates.append(game_candidate)
        if tx_candidate:
            candidates.append(tx_candidate)

        if not candidates:
            logger.info(
                "on_this_day: no results for %s-%02d-%02d — skipping",
                as_of.year,
                month,
                day,
            )

        return candidates


# Register the singleton
_on_this_day = OnThisDayDetector()
register(_on_this_day)
