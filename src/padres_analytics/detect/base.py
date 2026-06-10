"""Detector protocol, registry, and emit() helper."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from padres_analytics.detect.candidates import StatCandidate
from padres_analytics.detect.scoring import min_novelty_threshold

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Detector] = {}


@runtime_checkable
class Detector(Protocol):
    """Protocol every detector must satisfy."""

    name: str

    def run(
        self,
        conn: duckdb.DuckDBPyConnection,
        as_of: date,
    ) -> list[StatCandidate]:
        """Run the detector and return zero or more candidates.

        Args:
            conn: A read-only connection to padres.db with hist attached.
            as_of: The reference date for "today" (always America/Los_Angeles).

        Returns:
            List of StatCandidate objects. Empty list is valid (no news).
        """
        ...


def register(detector: Detector) -> Detector:
    """Register a detector instance in the global registry.

    Args:
        detector: A Detector-protocol-compliant instance.

    Returns:
        The same detector (allows use as a decorator on instances).
    """
    _REGISTRY[detector.name] = detector
    logger.debug("Registered detector: %s", detector.name)
    return detector


def get_detector(name: str) -> Detector:
    """Retrieve a registered detector by name.

    Args:
        name: Detector name.

    Returns:
        The detector instance.

    Raises:
        KeyError: If no detector with this name is registered.
    """
    if name not in _REGISTRY:
        raise KeyError(f"No detector named '{name}'. Registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def all_detectors() -> list[str]:
    """Return sorted list of all registered detector names."""
    return sorted(_REGISTRY)


def emit(
    conn: duckdb.DuckDBPyConnection,
    candidates: list[StatCandidate],
) -> int:
    """Write candidates to stat_candidates, skipping below-threshold and duplicates.

    Args:
        conn: A write-mode connection to padres.db.
        candidates: Candidates returned by a detector.

    Returns:
        Number of new rows inserted.
    """
    threshold = min_novelty_threshold()
    inserted = 0

    for c in candidates:
        if c.novelty_score < threshold:
            logger.info(
                "Suppressed %s (score %.2f < threshold %.2f)",
                c.candidate_id,
                c.novelty_score,
                threshold,
            )
            continue

        existing = conn.execute(
            "SELECT candidate_id FROM stat_candidates WHERE candidate_id = ?",
            [c.candidate_id],
        ).fetchone()

        if existing:
            logger.debug("Duplicate candidate %s — skipping", c.candidate_id)
            continue

        conn.execute(
            """
            INSERT INTO stat_candidates (
                candidate_id, detector, subject, as_of, category,
                payload_kind, facts_json, provenance_json,
                coverage_window, claim_scope, novelty_score, novelty_components
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                c.candidate_id,
                c.detector,
                c.subject,
                c.as_of,
                c.category,
                c.payload_kind,
                json.dumps(c.facts_json),
                json.dumps(c.provenance_json),
                c.coverage_window,
                c.claim_scope,
                c.novelty_score,
                json.dumps(c.novelty_components) if c.novelty_components else None,
            ],
        )
        inserted += 1
        logger.info("Emitted candidate %s (%s)", c.candidate_id, c.detector)

    return inserted
