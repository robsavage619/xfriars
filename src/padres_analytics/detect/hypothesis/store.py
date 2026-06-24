"""Persistence for the hypothesis queue and the explored-space ledger."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.hypothesis.spec import HypothesisSpec

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Terminal outcomes recorded for every processed spec — all visible, none silent.
OUTCOMES = (
    "invalid",
    "coverage_blocked",
    "no_data",
    "below_gate",
    "emitted",
    "unsupported_window",
)


@dataclass(frozen=True)
class Outcome:
    """One row of the explored-space ledger, summarized for the context pack."""

    spec_hash: str
    metric_id: str
    rationale: str
    as_of: str
    outcome: str
    max_rarity: float | None
    reason: str


def enqueue(conn: duckdb.DuckDBPyConnection, specs: list[HypothesisSpec], as_of: date) -> int:
    """Insert proposed specs into the queue, skipping ones already pending.

    Args:
        conn: Write-mode connection.
        specs: Validated-or-not proposals (validation happens at scan time).
        as_of: Reference date the proposals were made for.

    Returns:
        Number of new rows enqueued.
    """
    inserted = 0
    for spec in specs:
        h = spec.spec_hash()
        existing = conn.execute(
            "SELECT 1 FROM hypothesis_queue WHERE spec_hash = ? AND status = 'pending'",
            [h],
        ).fetchone()
        if existing:
            logger.debug("enqueue: %s already pending — skipping", h)
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO hypothesis_queue (spec_hash, spec_json, rationale, as_of, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            [h, spec.model_dump_json(), spec.rationale, as_of],
        )
        inserted += 1
    return inserted


def pending(conn: duckdb.DuckDBPyConnection) -> list[HypothesisSpec]:
    """Return all pending specs in creation order."""
    rows = conn.execute(
        "SELECT spec_json FROM hypothesis_queue WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()
    return [HypothesisSpec.model_validate_json(r[0]) for r in rows]


def mark_processed(conn: duckdb.DuckDBPyConnection, spec_hash: str) -> None:
    """Flip a queued spec to processed so it is not scanned again."""
    conn.execute(
        "UPDATE hypothesis_queue SET status = 'processed' WHERE spec_hash = ?",
        [spec_hash],
    )


def log_outcome(
    conn: duckdb.DuckDBPyConnection,
    spec: HypothesisSpec,
    as_of: date,
    outcome: str,
    *,
    max_rarity: float | None = None,
    candidate_id: str | None = None,
    reason: str = "",
) -> None:
    """Record the result of scanning one spec into the explored-space ledger."""
    log_id = hashlib.sha256(f"{spec.spec_hash()}|{as_of}".encode()).hexdigest()[:16]
    conn.execute(
        """
        INSERT OR REPLACE INTO hypothesis_log (
            log_id, spec_hash, metric_id, rationale, as_of,
            outcome, max_rarity, candidate_id, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            log_id,
            spec.spec_hash(),
            spec.id,
            spec.rationale,
            as_of,
            outcome,
            max_rarity,
            candidate_id,
            reason,
        ],
    )
    logger.info("hypothesis %s -> %s (%s)", spec.id, outcome, reason or "ok")


def explored(conn: duckdb.DuckDBPyConnection, limit: int = 60) -> list[Outcome]:
    """Return recent ledger entries, newest first — the anti-dead-horse signal."""
    rows = conn.execute(
        """
        SELECT spec_hash, metric_id, rationale, as_of, outcome, max_rarity, reason
        FROM hypothesis_log
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    return [
        Outcome(
            spec_hash=r[0],
            metric_id=r[1],
            rationale=r[2] or "",
            as_of=str(r[3]),
            outcome=r[4],
            max_rarity=float(r[5]) if r[5] is not None else None,
            reason=r[6] or "",
        )
        for r in rows
    ]


def explored_json(conn: duckdb.DuckDBPyConnection, limit: int = 60) -> list[dict]:
    """The ledger as plain dicts for embedding in the JSON context pack."""
    return [
        {
            "spec_hash": o.spec_hash,
            "metric_id": o.metric_id,
            "rationale": o.rationale,
            "as_of": o.as_of,
            "outcome": o.outcome,
            "max_rarity": o.max_rarity,
            "reason": o.reason,
        }
        for o in explored(conn, limit)
    ]
