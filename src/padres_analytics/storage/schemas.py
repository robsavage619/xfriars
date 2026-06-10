"""Versioned DDL for padres-analytics DuckDB schemas.

Schemas evolve via additive migrations only. Every table carries
``ingested_at`` for provenance. SCHEMA_VERSION is bumped whenever DDL changes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version  INTEGER NOT NULL,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Freshness guard — detectors refuse stale/partial inputs (§2.5)
    """
    CREATE TABLE IF NOT EXISTS ingest_runs (
        run_id       VARCHAR PRIMARY KEY,
        source       VARCHAR NOT NULL,
        started_at   TIMESTAMP,
        finished_at  TIMESTAMP,
        complete     BOOLEAN DEFAULT FALSE,
        rows_written INTEGER,
        note         VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stat_candidates (
        candidate_id      VARCHAR PRIMARY KEY,
        detector          VARCHAR NOT NULL,
        subject           VARCHAR,
        as_of             DATE NOT NULL,
        category          VARCHAR,
        payload_kind      VARCHAR NOT NULL,
        facts_json        JSON NOT NULL,
        provenance_json   JSON NOT NULL,
        coverage_window   VARCHAR NOT NULL,
        claim_scope       VARCHAR NOT NULL,
        novelty_score     DOUBLE NOT NULL,
        novelty_components JSON,
        status            VARCHAR DEFAULT 'new',
        ingested_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_candidates_status
        ON stat_candidates(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_candidates_detector
        ON stat_candidates(detector)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_candidates_as_of
        ON stat_candidates(as_of)
    """,
    """
    CREATE TABLE IF NOT EXISTS tweet_drafts (
        draft_id             VARCHAR PRIMARY KEY,
        candidate_id         VARCHAR REFERENCES stat_candidates(candidate_id),
        draft_kind           VARCHAR DEFAULT 'feed',
        thread_id            VARCHAR,
        thread_order         INTEGER,
        reply_to_url         VARCHAR,
        text                 VARCHAR NOT NULL,
        media_path           VARCHAR,
        is_projection        BOOLEAN DEFAULT FALSE,
        model                VARCHAR,
        source               VARCHAR DEFAULT 'skill',
        interesting_judgment VARCHAR,
        verification_json    JSON,
        status               VARCHAR DEFAULT 'pending',
        created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        posted_tweet_id      VARCHAR,
        posted_at            TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS post_metrics (
        posted_tweet_id     VARCHAR,
        captured_at         TIMESTAMP,
        impressions         INTEGER,
        likes               INTEGER,
        reposts             INTEGER,
        replies             INTEGER,
        bookmarks           INTEGER,
        follows_attributed  INTEGER,
        PRIMARY KEY (posted_tweet_id, captured_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS predictions (
        prediction_id VARCHAR PRIMARY KEY,
        draft_id      VARCHAR,
        claim         VARCHAR NOT NULL,
        posted_at     TIMESTAMP,
        resolves_by   DATE,
        outcome       VARCHAR DEFAULT 'open'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS corrections (
        correction_id       VARCHAR PRIMARY KEY,
        original_tweet_id   VARCHAR,
        what_was_wrong      VARCHAR,
        root_cause          VARCHAR,
        corrected_at        TIMESTAMP
    )
    """,
)


def initialize(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables idempotently and record schema version.

    Args:
        conn: An open write-mode DuckDB connection to padres.db.
    """
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)

    current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    recorded = current[0] if current else None

    if recorded is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", [SCHEMA_VERSION])
        logger.info("Initialized schema version %d", SCHEMA_VERSION)
    elif recorded < SCHEMA_VERSION:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", [SCHEMA_VERSION])
        logger.info("Migrated schema from %d to %d", recorded, SCHEMA_VERSION)
    else:
        logger.debug("Schema is current at version %d", recorded)
