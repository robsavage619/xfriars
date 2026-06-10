"""StatCandidate model and payload types."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Annotation(BaseModel):
    """Annotation point for a series chart."""

    x: float
    y: float
    label: str


class TablePayload(BaseModel):
    """Payload for a ranked-table stat card."""

    kind: Literal["table"] = "table"
    title: str
    subtitle: str | None = None
    as_of: date
    columns: Annotated[list[str], Field(min_length=1, max_length=6)]
    rows: Annotated[list[list[str | int | float]], Field(max_length=10)]
    highlight_row: int | None = None  # 0-based index of the Padre row
    source: str
    headline: str  # one-sentence hook for Claude; never rendered on card
    claim_scope: str  # rendered into subtitle when bounded


class SeriesPayload(BaseModel):
    """Payload for a time-series trend card."""

    kind: Literal["series"] = "series"
    title: str
    as_of: date
    x_label: str
    y_label: str
    points: list[tuple[float, float]]
    annotation: Annotation | None = None
    source: str
    headline: str
    claim_scope: str


class StatCandidate(BaseModel):
    """A detector-emitted stat with full provenance."""

    candidate_id: str
    detector: str
    subject: str | None = None
    as_of: date
    category: str | None = None  # in_game | season | historical
    payload_kind: str
    facts_json: dict
    provenance_json: list[dict]
    coverage_window: str
    claim_scope: str
    novelty_score: float
    novelty_components: dict | None = None
    status: str = "new"


def make_candidate_id(detector: str, subject: str | None, facts: dict) -> str:
    """Compute a deterministic 16-char hex ID from detector + subject + facts.

    Args:
        detector: Detector name (e.g. "on_this_day").
        subject: Optional subject label (e.g. "SDP|Jun 9").
        facts: The facts_json dict (will be JSON-serialized with sorted keys).

    Returns:
        16-char hex string (first 8 bytes of SHA-256).
    """
    payload = f"{detector}|{subject or ''}|{json.dumps(facts, sort_keys=True, default=str)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
