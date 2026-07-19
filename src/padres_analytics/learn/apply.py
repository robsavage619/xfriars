"""Read the latest prior snapshot and apply it to a candidate's ranking.

This is the consumer half of the loop, and it is deliberately the *only* place
priors touch anything. Priors adjust rank order and nothing else — they never
reach ``facts_json``, never change a claim, and never relax a statistical gate.
A learned engine still has to be a correct one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from padres_analytics.learn.priors import FeatureStat, combine

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_CACHE_KEY = "_learned_priors_cache"


def latest_stats(conn: duckdb.DuckDBPyConnection) -> dict[str, FeatureStat]:
    """Load the most recent prior snapshot.

    Returns:
        Feature key -> FeatureStat; empty when no run has been recorded (the
        cold-start case, which yields neutral multipliers everywhere).
    """
    try:
        rows = conn.execute(
            """
            SELECT feature, n_pos, n_total, multiplier
            FROM learned_priors
            WHERE run_id = (SELECT run_id FROM learning_runs ORDER BY created_at DESC LIMIT 1)
            """
        ).fetchall()
    except Exception as exc:
        logger.debug("learn.apply: no prior snapshot available (%s)", exc)
        return {}
    return {
        str(r[0]): FeatureStat(
            feature=str(r[0]),
            n_pos=float(r[1]) if r[1] is not None else 0.0,
            n_total=float(r[2]) if r[2] is not None else 0.0,
            multiplier=float(r[3]),
        )
        for r in rows
    }


def apply_priors(
    stats: dict[str, FeatureStat],
    score: float,
    features: tuple[str, ...] | list[str],
) -> tuple[float, dict[str, float]]:
    """Scale a novelty score by what review history says about this shape.

    Args:
        stats: Prior snapshot (from :func:`latest_stats`).
        score: The raw novelty score.
        features: The candidate's feature keys.

    Returns:
        ``(adjusted_score, components)`` where components record the multiplier
        and the raw score, so ``pad detect list`` can show why a candidate moved.
    """
    if not stats:
        return score, {}
    mult = combine(stats, features)
    if mult == 1.0:
        return score, {}
    adjusted = max(0.0, min(1.0, score * mult))
    return adjusted, {"editorial_prior": round(mult, 4), "raw_novelty": round(score, 4)}
