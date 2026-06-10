"""Reply-ammo engine — fast search over verified stat candidates.

Returns the N best verified facts for a query, ranked by novelty x recency.
Used by `pad ammo` for quick reply composition under live posts.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 5
# Recency weight: score decays by this fraction per day older than as_of
_RECENCY_DECAY_PER_DAY = 0.02
_MAX_DECAY = 0.8  # floor: never decay more than 80%


def search_ammo(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    as_of: date | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict]:
    """Search stat_candidates for facts relevant to a query string.

    Searches headline and subject fields. Ranks results by
    novelty_score x recency_weight, newest first on ties.

    Args:
        conn: Read-mode padres.db connection.
        query: Free-text search (case-insensitive substring match).
        as_of: Reference date for recency scoring. Defaults to today.
        limit: Maximum results to return.

    Returns:
        List of result dicts with keys: candidate_id, detector, subject,
        as_of, headline, novelty_score, recency_weight, ammo_score, claim_scope.
    """
    import json
    from datetime import date as date_type

    ref = as_of or date_type.today()
    lower_q = query.lower()

    rows = conn.execute(
        """
        SELECT
            candidate_id,
            detector,
            subject,
            as_of,
            novelty_score,
            facts_json,
            claim_scope,
            status
        FROM stat_candidates
        WHERE
            (LOWER(subject) LIKE ? OR LOWER(facts_json::VARCHAR) LIKE ?)
        ORDER BY novelty_score DESC
        LIMIT 100
        """,
        [f"%{lower_q}%", f"%{lower_q}%"],
    ).fetchall()

    results = []
    for r in rows:
        cid, detector, subject, cand_as_of, novelty, facts_raw, claim_scope, status = r

        facts = json.loads(facts_raw) if isinstance(facts_raw, str) else facts_raw
        headline = facts.get("headline", subject or "")

        # Recency decay
        if cand_as_of:
            age_days = (ref - cand_as_of).days if hasattr(cand_as_of, "days") else 0
        else:
            age_days = 0
        decay = min(_MAX_DECAY, age_days * _RECENCY_DECAY_PER_DAY)
        recency_weight = 1.0 - decay
        ammo_score = (novelty or 0.0) * recency_weight

        results.append(
            {
                "candidate_id": cid,
                "detector": detector,
                "subject": subject,
                "as_of": str(cand_as_of) if cand_as_of else None,
                "headline": headline,
                "novelty_score": round(novelty or 0.0, 4),
                "recency_weight": round(recency_weight, 4),
                "ammo_score": round(ammo_score, 4),
                "claim_scope": claim_scope,
                "status": status,
            }
        )

    # Sort by ammo_score desc, break ties by as_of desc
    results.sort(key=lambda x: (x["ammo_score"], x["as_of"] or ""), reverse=True)
    return results[:limit]
