"""Persistence for referee verdicts and adjudications."""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

from padres_analytics.review.models import Adjudication, FailureMode, ReviewVerdict

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


def record(
    conn: duckdb.DuckDBPyConnection,
    target_kind: str,
    target_id: str,
    adjudication: Adjudication,
) -> None:
    """Persist every verdict in an adjudication.

    Args:
        conn: Write-mode connection.
        target_kind: draft | candidate | study.
        target_id: The reviewed target.
        adjudication: The panel's decision.
    """
    for v in adjudication.verdicts:
        conn.execute(
            """
            INSERT INTO review_verdicts (
                verdict_id, target_kind, target_id, packet_hash, lens,
                verdict, failure_mode, evidence, confidence, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(uuid.uuid4())[:8],
                target_kind,
                target_id,
                adjudication.packet_hash,
                v.lens,
                v.effective_verdict(),
                v.failure_mode,
                v.evidence[:500],
                v.confidence,
                adjudication.outcome,
            ],
        )
    logger.info(
        "referee: recorded %d verdict(s) for %s %s (%s)",
        len(adjudication.verdicts),
        target_kind,
        target_id,
        adjudication.outcome,
    )


def latest(
    conn: duckdb.DuckDBPyConnection,
    target_kind: str,
    target_id: str,
) -> Adjudication | None:
    """Return the most recent adjudication for a target, or None.

    Args:
        conn: Read connection.
        target_kind: draft | candidate | study.
        target_id: The reviewed target.

    Returns:
        The reconstructed Adjudication, or None if never reviewed.
    """
    # Group on packet_hash, not on the timestamp: the lenses of one review land
    # microseconds apart, so matching MAX(reviewed_at) returns a single verdict
    # and silently drops the rest of the panel.
    rows = conn.execute(
        """
        SELECT packet_hash, lens, verdict, failure_mode, evidence, confidence, outcome
        FROM review_verdicts
        WHERE target_kind = ? AND target_id = ?
          AND packet_hash = (
              SELECT packet_hash FROM review_verdicts
              WHERE target_kind = ? AND target_id = ?
              ORDER BY reviewed_at DESC LIMIT 1
          )
        ORDER BY lens
        """,
        [target_kind, target_id, target_kind, target_id],
    ).fetchall()
    if not rows:
        return None

    verdicts = [
        ReviewVerdict(
            lens=r[1],
            verdict=r[2],
            failure_mode=r[3],
            evidence=r[4] or "",
            confidence=float(r[5]) if r[5] is not None else 1.0,
        )
        for r in rows
    ]
    modes: list[FailureMode] = [v.failure_mode for v in verdicts if v.failure_mode is not None]
    return Adjudication(
        outcome=rows[0][6],
        packet_hash=rows[0][0],
        verdicts=verdicts,
        failure_modes=modes,
        rationale="; ".join(f"{v.lens}: {v.failure_mode}" for v in verdicts if v.failure_mode),
    )


def failure_mode_counts(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Aggregate failure modes — what the referee keeps killing, and why.

    Feeds the learning priors (Phase 2) and the hypothesis context pack so the
    discovery side stops proposing shapes that keep getting refuted.
    """
    try:
        rows = conn.execute(
            """
            SELECT failure_mode, lens, COUNT(*) AS n
            FROM review_verdicts
            WHERE failure_mode IS NOT NULL
            GROUP BY failure_mode, lens
            ORDER BY n DESC
            """
        ).fetchall()
    except Exception as exc:
        logger.debug("referee: failure_mode_counts unavailable: %s", exc)
        return []
    return [{"failure_mode": r[0], "lens": r[1], "n": int(r[2])} for r in rows]


def block_rate_by_lens(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Per-lens block rate.

    A lens that never blocks is a rubber stamp, which is itself a defect — this
    makes that visible instead of reading as a clean sheet.
    """
    try:
        rows = conn.execute(
            """
            SELECT lens,
                   COUNT(*) AS reviewed,
                   SUM(CASE WHEN verdict = 'BLOCK' THEN 1 ELSE 0 END) AS blocked
            FROM review_verdicts
            GROUP BY lens
            ORDER BY lens
            """
        ).fetchall()
    except Exception as exc:
        logger.debug("referee: block_rate_by_lens unavailable: %s", exc)
        return []
    out = []
    for lens, reviewed, blocked in rows:
        n, b = int(reviewed), int(blocked or 0)
        out.append({"lens": lens, "reviewed": n, "blocked": b, "block_rate": (b / n) if n else 0.0})
    return out


def as_json(adjudication: Adjudication) -> str:
    """Serialize an adjudication for inbox files and CLI output."""
    return json.dumps(adjudication.model_dump(mode="json"), indent=2)
