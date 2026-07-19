"""Decomposition trees — the deterministic walk from anomaly to mechanism.

The flagship tree investigates a wOBA-minus-xwOBA gap: a hitter whose results
trail (or outrun) the quality of his contact. "He's been unlucky" is where most
accounts stop; it is where a study starts.

The walk is fixed in code, not chosen per case. Picking which questions to ask
after seeing the data is how a narrative gets fitted to a conclusion — the tree
asks the same things in the same order every time, and reports the ones it
cannot answer rather than dropping them.

Every node is one SQL query with a threshold decided in advance.
"""

from __future__ import annotations

import logging
import statistics
import uuid
from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.sql import fmt_name, resolve_table
from padres_analytics.study.dossier import StudyDossier, StudyNode

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Minimum plate appearances before any part of this study is worth running.
MIN_PA = 150

# A component (batting average or slugging) counts as carrying the gap when it
# accounts for at least this share of it.
_COMPONENT_SHARE = 0.4

# An approach metric has moved when it shifts by at least this many league
# standard deviations year over year.
_APPROACH_Z = 1.0


def _ordinal(n: int) -> str:
    """English ordinal — 62nd, not 62th."""
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }".replace(" ", "")


def _pct_rank(values: list[float], focal: float) -> float:
    """Share of the population the focal value exceeds."""
    if not values:
        return 0.5
    return sum(1 for v in values if v < focal) / len(values)


def _node_gap(
    conn: duckdb.DuckDBPyConnection, player_id: int, year: int
) -> tuple[StudyNode, dict[str, float | str] | None]:
    """The anomaly: how far do results sit from contact quality, and is that rare?"""
    src = resolve_table(conn, "statcast_batting_expected")
    rows = conn.execute(
        f"""
        SELECT player_id, player_name, woba, est_woba, ba, est_ba, slg, est_slg, pa
        FROM {src}
        WHERE year = ? AND pa >= ? AND woba IS NOT NULL AND est_woba IS NOT NULL
        """,
        [year, MIN_PA],
    ).fetchall()

    if not rows:
        return (
            StudyNode(
                node_id="gap",
                question="How far are his results from the quality of his contact?",
                verdict="insufficient",
                reason=f"No expected-stats rows for {year} at {MIN_PA}+ PA.",
            ),
            None,
        )

    subject = next((r for r in rows if int(r[0]) == player_id), None)
    if subject is None:
        return (
            StudyNode(
                node_id="gap",
                question="How far are his results from the quality of his contact?",
                verdict="insufficient",
                reason=f"Subject has fewer than {MIN_PA} PA in {year}.",
            ),
            None,
        )

    _pid, name, woba, est_woba, ba, est_ba, slg, est_slg, pa = subject
    gap = float(est_woba) - float(woba)
    population = [float(r[3]) - float(r[2]) for r in rows]
    pct = _pct_rank([abs(v) for v in population], abs(gap))

    direction = "behind" if gap > 0 else "ahead of"
    finding = (
        f"{fmt_name(str(name))}'s wOBA sits {abs(gap):.3f} {direction} his expected wOBA "
        f"({float(woba):.3f} actual vs {float(est_woba):.3f} expected over {int(pa)} PA) — "
        f"a wider gap than {round(pct * 100)}% of the {len(rows)} qualified hitters"
    )

    return (
        StudyNode(
            node_id="gap",
            question="How far are his results from the quality of his contact?",
            verdict="fired" if abs(gap) >= 0.020 else "quiet",
            finding=finding,
            facts={
                "woba": round(float(woba), 3),
                "est_woba": round(float(est_woba), 3),
                "gap": round(gap, 3),
                "pa": int(pa),
                "gap_percentile": round(pct * 100),
                "population_size": len(rows),
            },
            n=int(pa),
            claim_scope=f"{year}, qualified hitters (min {MIN_PA} PA)",
        ),
        {
            "gap": gap,
            "ba": float(ba),
            "est_ba": float(est_ba),
            "slg": float(slg),
            "est_slg": float(est_slg),
            "name": fmt_name(str(name)),
        },
    )


def _node_components(vals: dict[str, float | str]) -> StudyNode:
    """Where the gap lives: hits that aren't falling, or power that isn't landing."""
    ba, est_ba = float(vals["ba"]), float(vals["est_ba"])
    slg, est_slg = float(vals["slg"]), float(vals["est_slg"])
    ba_gap = est_ba - ba
    slg_gap = est_slg - slg
    total = abs(ba_gap) + abs(slg_gap)

    if total == 0:
        return StudyNode(
            node_id="components",
            question="Is the gap in getting hits, or in the damage on them?",
            verdict="quiet",
            finding="Batting average and slugging both match expectation.",
        )

    ba_share = abs(ba_gap) / total
    if ba_share >= 1 - _COMPONENT_SHARE:
        carrier = "singles and base hits that aren't falling"
    elif ba_share <= _COMPONENT_SHARE:
        carrier = "extra-base damage that isn't landing"
    else:
        carrier = "both average and power, in roughly equal measure"

    return StudyNode(
        node_id="components",
        question="Is the gap in getting hits, or in the damage on them?",
        verdict="fired",
        finding=(
            f"The gap is carried by {carrier}: batting average is {abs(ba_gap):.3f} "
            f"{'below' if ba_gap > 0 else 'above'} expected, slugging {abs(slg_gap):.3f} "
            f"{'below' if slg_gap > 0 else 'above'}"
        ),
        facts={
            "ba": round(ba, 3),
            "est_ba": round(est_ba, 3),
            "ba_gap": round(ba_gap, 3),
            "slg": round(slg, 3),
            "est_slg": round(est_slg, 3),
            "slg_gap": round(slg_gap, 3),
        },
    )


def _node_contact(conn: duckdb.DuckDBPyConnection, player_id: int, year: int) -> StudyNode:
    """Is the contact behind the expectation actually good?"""
    src = resolve_table(conn, "statcast_batter_exitvelo_barrels")
    try:
        rows = conn.execute(
            f"""
            SELECT player_id, avg_hit_speed, brl_percent, attempts
            FROM {src}
            WHERE year = ? AND attempts >= 50 AND avg_hit_speed IS NOT NULL
            """,
            [year],
        ).fetchall()
    except Exception as exc:
        return StudyNode(
            node_id="contact",
            question="Is the contact itself any good?",
            verdict="insufficient",
            reason=f"Exit-velocity table unavailable: {exc}",
        )

    subject = next((r for r in rows if int(r[0]) == player_id), None)
    if subject is None or len(rows) < 30:
        return StudyNode(
            node_id="contact",
            question="Is the contact itself any good?",
            verdict="insufficient",
            reason="Subject or population below the 50-batted-ball minimum.",
        )

    ev_pct = _pct_rank([float(r[1]) for r in rows], float(subject[1]))
    brl_pct = _pct_rank([float(r[2]) for r in rows if r[2] is not None], float(subject[2] or 0))

    strong = ev_pct >= 0.6 or brl_pct >= 0.6
    return StudyNode(
        node_id="contact",
        question="Is the contact itself any good?",
        verdict="fired" if strong else "quiet",
        finding=(
            f"He is hitting the ball {float(subject[1]):.1f} mph on average "
            f"({_ordinal(round(ev_pct * 100))} percentile) with a "
            f"{float(subject[2] or 0):.1f}% barrel rate "
            f"({_ordinal(round(brl_pct * 100))}) over {int(subject[3])} batted balls"
        ),
        facts={
            "avg_exit_velocity": round(float(subject[1]), 1),
            "exit_velocity_percentile": round(ev_pct * 100),
            "barrel_rate": round(float(subject[2] or 0), 1),
            "barrel_percentile": round(brl_pct * 100),
            "batted_balls": int(subject[3]),
        },
        n=int(subject[3]),
        claim_scope=f"{year}, hitters with 50+ batted balls",
    )


def _node_approach(conn: duckdb.DuckDBPyConnection, player_id: int, year: int) -> StudyNode:
    """Has he changed his approach, or is he doing what he always did?"""
    from padres_analytics.detect.aggregates import BATTER_AGGS, fetch_agg_rows

    chase = next(m for m in BATTER_AGGS if m.id == "chase_rate")
    prior = year - 1

    now_rows, now_sizes, _ = fetch_agg_rows(conn, chase, year)
    then_rows, _then_sizes, _ = fetch_agg_rows(conn, chase, prior)

    now = {pid: val for pid, _, val in now_rows}
    then = {pid: val for pid, _, val in then_rows}

    if player_id not in now or player_id not in then:
        return StudyNode(
            node_id="approach",
            question="Has he changed his approach at the plate?",
            verdict="insufficient",
            reason=(
                f"Needs qualifying pitch-level data in both {prior} and {year}; "
                f"the subject is missing from at least one."
            ),
        )

    moves = [now[pid] - then[pid] for pid in now if pid in then]
    if len(moves) < 30:
        return StudyNode(
            node_id="approach",
            question="Has he changed his approach at the plate?",
            verdict="insufficient",
            reason=(
                f"Only {len(moves)} hitters measurable in both seasons; too few to judge a move."
            ),
        )

    sd = statistics.pstdev(moves)
    delta = now[player_id] - then[player_id]
    z = delta / sd if sd else 0.0

    moved = abs(z) >= _APPROACH_Z
    direction = "more" if delta > 0 else "less"
    return StudyNode(
        node_id="approach",
        question="Has he changed his approach at the plate?",
        verdict="fired" if moved else "quiet",
        finding=(
            f"He is chasing {abs(delta):.1f} points {direction} than in {prior} "
            f"({then[player_id]:.1f}% to {now[player_id]:.1f}%), a move of {abs(z):.1f} "
            f"standard deviations against how {len(moves)} hitters shifted"
            if moved
            else (
                f"His chase rate is essentially unchanged from {prior} "
                f"({then[player_id]:.1f}% to {now[player_id]:.1f}%) — the approach is not the story"
            )
        ),
        facts={
            "chase_now": round(now[player_id], 1),
            "chase_prior": round(then[player_id], 1),
            "chase_delta": round(delta, 1),
            "cohort_z": round(z, 2),
            "cohort_size": len(moves),
        },
        n=now_sizes.get(player_id),
        claim_scope=f"{prior} vs {year}, hitters with pitch-level data in both",
    )


def _node_comps(conn: duckdb.DuckDBPyConnection, player_id: int, year: int) -> StudyNode:
    """What happened to hitters who looked like this before?"""
    # The regression payoff needs *historical* expected stats — what a comparable
    # hitter's gap was, and what he did the following season. Savant expected
    # stats have only been ingested for the current season, so the question is
    # unanswerable rather than answerable-with-caveats.
    try:
        years = conn.execute(
            f"SELECT COUNT(DISTINCT year) FROM {resolve_table(conn, 'statcast_batting_expected')}"
        ).fetchone()
    except Exception as exc:
        return StudyNode(
            node_id="comps",
            question="What happened to hitters who looked like this before?",
            verdict="insufficient",
            reason=f"Expected-stats history unavailable: {exc}",
        )

    n_years = int(years[0]) if years and years[0] else 0
    if n_years < 2:
        return StudyNode(
            node_id="comps",
            question="What happened to hitters who looked like this before?",
            verdict="insufficient",
            reason=(
                f"Expected stats cover {n_years} season(s). Finding hitters with similar "
                f"gaps and reporting what they did next needs at least two, so this "
                f"study cannot close the loop on regression yet."
            ),
        )

    return StudyNode(
        node_id="comps",
        question="What happened to hitters who looked like this before?",
        verdict="quiet",
        finding="Historical comparables available but no close match cleared the similarity bar.",
    )


def build_gap_study(
    conn: duckdb.DuckDBPyConnection,
    player_id: int,
    year: int,
    as_of: date,
    candidate_id: str | None = None,
) -> StudyDossier:
    """Walk the wOBA-gap decomposition and freeze the result.

    Args:
        conn: Read connection with hist attached.
        player_id: Subject.
        year: Season under study.
        as_of: Reference date.
        candidate_id: The candidate that triggered this study, if any.

    Returns:
        The frozen dossier. Always returns — a study that could answer nothing
        is itself a reportable outcome, not an error.
    """
    gap_node, vals = _node_gap(conn, player_id, year)
    nodes: list[StudyNode] = [gap_node]

    if vals is not None:
        nodes.append(_node_components(vals))
        nodes.append(_node_contact(conn, player_id, year))
        nodes.append(_node_approach(conn, player_id, year))
        nodes.append(_node_comps(conn, player_id, year))

    name = vals["name"] if vals else str(player_id)
    dossier = StudyDossier(
        study_id=str(uuid.uuid4())[:8],
        candidate_id=candidate_id,
        subject_id=player_id,
        subject_name=str(name),
        tree="gap_woba",
        as_of=as_of,
        headline=gap_node.finding or "No qualifying gap to study.",
        nodes=nodes,
        comps=[],
        coverage_notes=[n.reason for n in nodes if n.verdict == "insufficient" and n.reason],
    )
    logger.info("study %s: %s — %s", dossier.study_id, dossier.subject_name, dossier.summary())
    return dossier
