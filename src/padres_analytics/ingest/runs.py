"""Ingest run recording — freshness guard per §2.5."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


@contextmanager
def record_run(
    conn: duckdb.DuckDBPyConnection,
    source: str,
    note: str | None = None,
) -> Generator[dict[str, object], None, None]:
    """Context manager that records an ingest_runs row.

    Yields a mutable dict so callers can set ``rows_written`` before exit.
    On clean exit sets ``complete=True`` and ``finished_at``. On exception
    sets ``complete=False`` so detectors can refuse stale inputs.

    Args:
        conn: Write-mode padres.db connection.
        source: Logical source name (e.g. ``"mlb-stats-api/leaders"``).
        note: Optional free-text context.

    Yields:
        Mutable result dict with key ``rows_written`` (int, default 0).

    Example::

        with record_run(conn, "mlb-stats-api/leaders", note="homeRuns 2026") as run:
            rows = ingest_leaders(conn, ...)
            run["rows_written"] = rows
    """
    run_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)
    result: dict[str, object] = {"rows_written": 0}

    conn.execute(
        """
        INSERT INTO ingest_runs (run_id, source, started_at, complete, note)
        VALUES (?, ?, ?, FALSE, ?)
        """,
        [run_id, source, started_at, note],
    )
    logger.debug("ingest_run %s started: source=%s", run_id, source)

    try:
        yield result
        finished_at = datetime.now(UTC)
        conn.execute(
            """
            UPDATE ingest_runs
            SET finished_at = ?, complete = TRUE, rows_written = ?
            WHERE run_id = ?
            """,
            [finished_at, result["rows_written"], run_id],
        )
        logger.info(
            "ingest_run %s complete: source=%s rows=%s",
            run_id,
            source,
            result["rows_written"],
        )
    except Exception:
        finished_at = datetime.now(UTC)
        conn.execute(
            """
            UPDATE ingest_runs
            SET finished_at = ?, complete = FALSE
            WHERE run_id = ?
            """,
            [finished_at, run_id],
        )
        logger.error("ingest_run %s failed: source=%s", run_id, source)
        raise


def last_complete_run(
    conn: duckdb.DuckDBPyConnection,
    source: str,
) -> datetime | None:
    """Return the finished_at timestamp of the most recent complete run for source.

    Args:
        conn: Read-mode padres.db connection.
        source: Logical source name.

    Returns:
        datetime (UTC) or None if no complete run exists.
    """
    row = conn.execute(
        """
        SELECT MAX(finished_at)
        FROM ingest_runs
        WHERE source = ? AND complete = TRUE
        """,
        [source],
    ).fetchone()
    return row[0] if row and row[0] is not None else None
