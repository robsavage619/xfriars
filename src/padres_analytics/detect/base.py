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

# Scopes that assert something about San Diego specifically. A claim at one of
# these scopes about a player who was never a Padre is not merely wrong, it is
# structurally impossible, so it raises rather than warns.
_FRANCHISE_SCOPES = ("franchise", "since_1969")


class SubjectNotAPadreError(ValueError):
    """A franchise-scoped candidate names a player with no San Diego season."""


def _check_subject(
    candidate: StatCandidate,
    franchise_ids: set[int],
    franchise_names: set[str] | None = None,
) -> None:
    """Reject a franchise-scoped claim whose subject never played for San Diego.

    Guards the class of bug that let ``career_chase`` emit "Aaron Judge is the
    Padres' all-time home run leader (302 HR)" — a leaderboard query missing its
    team filter. The detector-side SQL is fixed, but the gate is what stops the
    next unscoped query from reaching the board.

    Checks the id when facts carry one and falls back to the name when they do
    not: ``career_chase`` writes only ``player_name``, so an id-only gate would
    miss the very claim that motivated this check.

    Args:
        candidate: The candidate about to be written.
        franchise_ids: Every player id with a Padres season. An empty set means
            the source tables are missing, in which case the check is skipped —
            it must not fail every candidate when it simply cannot verify.
        franchise_names: Names of the same players, for id-less facts.

    Raises:
        SubjectNotAPadreError: If the subject is identifiable and not a Padre.
    """
    if not franchise_ids:
        return
    if not any(s in candidate.claim_scope for s in _FRANCHISE_SCOPES):
        return

    facts = candidate.facts_json.get("facts") or {}
    subject_id = facts.get("padre_player_id") or facts.get("player_id")
    subject_name = facts.get("player_name") or facts.get("lead_player")

    if subject_id is not None:
        if int(subject_id) in franchise_ids:
            return
    elif subject_name and franchise_names is not None:
        if subject_name in franchise_names:
            return
    else:
        return  # nothing identifiable to check

    raise SubjectNotAPadreError(
        f"{candidate.detector}: claim scoped '{candidate.claim_scope}' names "
        f"{subject_name or f'player_id={subject_id}'}, who has no San Diego season. "
        f"The detector's query is almost certainly missing a team filter."
    )


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
    from padres_analytics.detect.sql import franchise_player_ids, franchise_player_names

    threshold = min_novelty_threshold()
    franchise_ids = franchise_player_ids(conn)
    franchise_names = franchise_player_names(conn)
    inserted = 0

    for c in candidates:
        _check_subject(c, franchise_ids, franchise_names)

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
