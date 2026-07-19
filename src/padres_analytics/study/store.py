"""Persistence for study dossiers."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from padres_analytics.study.dossier import StudyDossier

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


def save(conn: duckdb.DuckDBPyConnection, dossier: StudyDossier) -> None:
    """Persist a frozen dossier.

    Args:
        conn: Write-mode connection.
        dossier: The dossier to store.
    """
    conn.execute(
        """
        INSERT INTO study_dossiers
            (study_id, candidate_id, subject_id, subject_name, tree, as_of,
             digest, dossier_json, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')
        """,
        [
            dossier.study_id,
            dossier.candidate_id,
            dossier.subject_id,
            dossier.subject_name,
            dossier.tree,
            dossier.as_of,
            dossier.digest(),
            json.dumps(dossier.audit_corpus(), default=str),
        ],
    )
    logger.info("study: saved %s (%s)", dossier.study_id, dossier.summary())


def load(conn: duckdb.DuckDBPyConnection, study_id: str) -> StudyDossier | None:
    """Load a dossier by id, or None if absent."""
    row = conn.execute(
        "SELECT dossier_json FROM study_dossiers WHERE study_id = ?", [study_id]
    ).fetchone()
    if row is None:
        return None
    raw = row[0]
    return StudyDossier.model_validate(json.loads(raw) if isinstance(raw, str) else raw)


def recent(conn: duckdb.DuckDBPyConnection, limit: int = 20) -> list[tuple]:
    """Recent studies as (study_id, subject_name, tree, as_of, status)."""
    try:
        return conn.execute(
            """
            SELECT study_id, subject_name, tree, as_of, status
            FROM study_dossiers ORDER BY created_at DESC LIMIT ?
            """,
            [limit],
        ).fetchall()
    except Exception as exc:
        logger.debug("study: listing unavailable: %s", exc)
        return []
