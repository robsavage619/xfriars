"""Draft inbox ingest, validation, and state machine."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from padres_analytics.detect.candidates import ChartDataset, TablePayload
from padres_analytics.render.cards import render
from padres_analytics.tweets.models import TweetDraft
from padres_analytics.tweets.verify import (
    VerificationError,
    digit_audit,
    verify_path_a,
    verify_path_b,
)

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# ── Valid state transitions ──────────────────────────────────────────────────
_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"verified", "rejected"},
    "verified": {"approved", "rejected"},
    "approved": {"posted", "rejected"},
    "posted": set(),  # terminal
    "rejected": set(),  # terminal
}


class DraftIngestError(ValueError):
    """Raised when a draft file fails ingest validation."""


class StateTransitionError(ValueError):
    """Raised on illegal draft state transitions."""


def ingest_draft(
    conn: duckdb.DuckDBPyConnection,
    draft_path: Path,
    cards_dir: Path,
) -> str:
    """Validate, digit-audit, render, and verify a draft JSON file.

    State after successful ingest: ``verified``.

    Args:
        conn: A write-mode padres.db connection with hist attached.
        draft_path: Path to the inbox JSON file (TweetDraft schema).
        cards_dir: Output directory for rendered PNG cards.

    Returns:
        The new draft_id.

    Raises:
        DraftIngestError: On validation, digit-audit, or verification failure.
        RenderError: If card rendering fails.
    """
    try:
        raw = json.loads(draft_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise DraftIngestError(f"Cannot read draft file {draft_path}: {exc}") from exc

    # 1 — Parse TweetDraft
    try:
        draft = TweetDraft.model_validate(raw)
    except ValidationError as exc:
        raise DraftIngestError(f"Draft schema validation failed:\n{exc}") from exc

    # 2 — Look up the candidate
    row = conn.execute(
        """
        SELECT facts_json, provenance_json, payload_kind, claim_scope
        FROM stat_candidates
        WHERE candidate_id = ?
        """,
        [draft.candidate_id],
    ).fetchone()
    if row is None:
        raise DraftIngestError(
            f"candidate_id {draft.candidate_id!r} not found in stat_candidates. "
            "Run 'pad detect run' first."
        )

    facts_raw, prov_raw, payload_kind, _claim_scope = row
    facts_json = json.loads(facts_raw) if isinstance(facts_raw, str) else facts_raw
    prov_json = json.loads(prov_raw) if isinstance(prov_raw, str) else prov_raw

    # 3 — Digit audit
    offenders = digit_audit(draft.text, facts_json)
    if offenders:
        raise DraftIngestError(
            f"Digit audit failed — these tokens in caption are not in facts_json: "
            f"{offenders}. Every number must originate from the verified payload."
        )

    # 4 — Render the card
    if payload_kind == "dataset":
        dataset = ChartDataset.model_validate(facts_json)
        card_path = render(dataset, cards_dir, draft.candidate_id)
    elif payload_kind == "table":
        payload = TablePayload.model_validate(facts_json)
        card_path = render(payload, cards_dir, draft.candidate_id)
    else:
        raise DraftIngestError(f"Unsupported payload_kind: {payload_kind!r}")

    # 5 — Verification: Path A for leaderboard candidates, Path B otherwise
    detector = conn.execute(
        "SELECT detector FROM stat_candidates WHERE candidate_id = ?",
        [draft.candidate_id],
    ).fetchone()
    detector_name = detector[0] if detector else ""

    try:
        if detector_name == "leaderboard":
            verification = verify_path_a(conn, draft.candidate_id, facts_json)
        else:
            verification = verify_path_b(conn, draft.candidate_id, facts_json, prov_json)
    except VerificationError as exc:
        raise DraftIngestError(f"Verification failed: {exc}") from exc

    # 6 — Write to tweet_drafts
    draft_id = str(uuid.uuid4())[:8]
    conn.execute(
        """
        INSERT INTO tweet_drafts (
            draft_id, candidate_id, draft_kind, thread_id, thread_order,
            reply_to_url, text, media_path, is_projection,
            model, source, interesting_judgment,
            verification_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'verified')
        """,
        [
            draft_id,
            draft.candidate_id,
            draft.draft_kind,
            draft.thread_id,
            draft.thread_order,
            draft.reply_to_url,
            draft.text,
            str(card_path),
            draft.is_projection,
            draft.model,
            "skill",
            draft.interesting_judgment,
            json.dumps(verification),
        ],
    )

    logger.info("Draft %s ingested and verified (card: %s)", draft_id, card_path)
    return draft_id


def transition(
    conn: duckdb.DuckDBPyConnection,
    draft_id: str,
    new_status: str,
) -> None:
    """Apply a state transition to a draft.

    Args:
        conn: Write-mode padres.db connection.
        draft_id: The draft to transition.
        new_status: Target status.

    Raises:
        StateTransitionError: If draft not found or transition is illegal.
    """
    row = conn.execute("SELECT status FROM tweet_drafts WHERE draft_id = ?", [draft_id]).fetchone()
    if row is None:
        raise StateTransitionError(f"Draft {draft_id!r} not found.")

    current = row[0]
    allowed = _TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise StateTransitionError(
            f"Cannot transition draft {draft_id} from {current!r} to {new_status!r}. "
            f"Allowed from {current!r}: {sorted(allowed) or 'none (terminal)'}"
        )

    conn.execute(
        "UPDATE tweet_drafts SET status = ? WHERE draft_id = ?",
        [new_status, draft_id],
    )
    logger.info("Draft %s: %s → %s", draft_id, current, new_status)
