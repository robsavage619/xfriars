"""Paste-back — Claude's deliverable re-entering the deterministic pipeline.

The Studio hands a prompt out and takes a JSON deliverable back. This module is
the door it comes through, and the rule at that door is simple: the pasted text
is DATA, never instructions. It is parsed, classified by shape, and routed to the
one gate path that shape is allowed to enter. Nothing in the payload can choose a
different path, skip a gate, or reach anything the shape's handler does not touch.

The gates themselves are unchanged and live where they always have — digit audit,
scope guard, render, and verification in ``tweets.draft.ingest_draft``; the
referee contract in ``review.gate.adjudicate``; spec validation at scan time for
hypotheses. What this module adds is the routing and an honest report of which
gate accepted or refused the work.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class ResultError(ValueError):
    """Raised when pasted text cannot be accepted. The message is shown to the user."""


@dataclass
class GateResult:
    """Outcome of routing one pasted deliverable."""

    kind: str
    accepted: bool
    summary: str
    gates: list[dict[str, Any]] = field(default_factory=list)
    draft_id: str | None = None
    saved_to: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the results endpoint."""
        return {
            "kind": self.kind,
            "accepted": self.accepted,
            "summary": self.summary,
            "gates": self.gates,
            "draft_id": self.draft_id,
            "saved_to": self.saved_to,
        }


def extract_json(raw: str) -> dict[str, Any]:
    """Pull one JSON object out of pasted text.

    Tolerates a code fence and surrounding prose, because that is what actually
    arrives from a chat window. Does not tolerate ambiguity: if there is no
    object, or it does not parse, that is an error rather than a guess.
    """
    text = raw.strip()
    if not text:
        raise ResultError("Nothing pasted.")

    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ResultError(
                "No JSON object found. Paste the deliverable itself — the whole "
                "{...} object the prompt asked for."
            )
        text = text[start : end + 1]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResultError(f"That isn't valid JSON: {exc.msg} (line {exc.lineno}).") from exc

    if not isinstance(payload, dict):
        raise ResultError("Expected a JSON object at the top level.")
    return payload


def classify(payload: dict[str, Any]) -> str:
    """Name the deliverable's shape. Unrecognized shapes are refused, not guessed."""
    if payload.get("verdict") == "no_story":
        return "no_story"
    if "verdicts" in payload and isinstance(payload.get("verdicts"), list):
        return "review"
    if "specs" in payload and isinstance(payload.get("specs"), list):
        return "hypothesis"
    if "text" in payload and "candidate_id" in payload:
        return "draft"
    raise ResultError(
        "Could not tell what this is. A draft needs 'candidate_id' and 'text'; a "
        "review needs 'verdicts'; hypotheses need 'specs'; an honest exit is "
        '{"verdict": "no_story", "why": "..."}.'
    )


def _archive(payload: dict[str, Any], kind: str, inbox: Path) -> Path:
    """Keep the raw deliverable. Provenance outlives whatever the gates decide."""
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path = inbox / f"result_{kind}_{stamp}_{uuid.uuid4().hex[:6]}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _ingest_draft_payload(
    conn: duckdb.DuckDBPyConnection, payload: dict[str, Any], inbox: Path, cards_dir: Path
) -> GateResult:
    """Run a pasted TweetDraft through the existing ingest gates."""
    from padres_analytics.tweets.draft import DraftIngestError, ingest_draft

    payload.setdefault("model", "claude-studio-handoff")
    saved = _archive(payload, "draft", inbox)

    gates: list[dict[str, Any]] = []
    try:
        draft_id = ingest_draft(conn, saved, cards_dir)
    except DraftIngestError as exc:
        message = str(exc)
        # ingest_draft runs schema → candidate lookup → digit audit → scope guard
        # → render → verify in order, and names the one that refused.
        gate = (
            "schema"
            if "schema validation" in message
            else "candidate"
            if "not found in stat_candidates" in message
            else "digit_audit"
            if "Digit audit" in message
            else "scope_guard"
            if "Scope upgrade" in message
            else "verification"
            if "Verification failed" in message
            else "ingest"
        )
        gates.append({"name": gate, "ok": False, "detail": message})
        return GateResult(
            kind="draft",
            accepted=False,
            summary=f"Rejected at the {gate.replace('_', ' ')} gate.",
            gates=gates,
            saved_to=str(saved),
        )
    except Exception as exc:
        gates.append({"name": "render", "ok": False, "detail": str(exc)})
        return GateResult(
            kind="draft",
            accepted=False,
            summary="Could not render the card.",
            gates=gates,
            saved_to=str(saved),
        )

    for name in ("schema", "digit_audit", "scope_guard", "render", "verification"):
        gates.append({"name": name, "ok": True, "detail": "passed"})
    return GateResult(
        kind="draft",
        accepted=True,
        summary=f"Draft {draft_id} verified and waiting on the referee.",
        gates=gates,
        draft_id=draft_id,
        saved_to=str(saved),
    )


def _record_review_payload(
    conn: duckdb.DuckDBPyConnection, payload: dict[str, Any], inbox: Path
) -> GateResult:
    """Adjudicate pasted verdicts and record them against the packet they judged."""
    from pydantic import ValidationError

    from padres_analytics.review.gate import RefereeContractError, adjudicate
    from padres_analytics.review.models import ReviewVerdict
    from padres_analytics.review.packet import build_packet
    from padres_analytics.review.store import record

    saved = _archive(payload, "review", inbox)
    target_id = payload.get("draft_id") or payload.get("target_id")
    packet_hash = payload.get("packet_hash")

    if not target_id:
        # The hash identifies which draft was judged when the payload omits it.
        if not packet_hash:
            raise ResultError(
                "This review doesn't say what it reviewed. Include the 'packet_hash' "
                "from the prompt (or a 'draft_id')."
            )
        row = conn.execute(
            "SELECT draft_id FROM tweet_drafts WHERE status IN ('pending','verified')"
        ).fetchall()
        for (candidate_draft,) in row:
            try:
                if build_packet(conn, draft_id=candidate_draft).packet_hash() == packet_hash:
                    target_id = candidate_draft
                    break
            except ValueError:
                continue
        if not target_id:
            raise ResultError(
                "No open draft matches that packet_hash. The caption may have changed "
                "since the packet was built — rebuild the review pack and re-run it."
            )

    try:
        packet = build_packet(conn, draft_id=str(target_id))
    except ValueError as exc:
        raise ResultError(str(exc)) from exc

    if packet_hash and packet_hash != packet.packet_hash():
        raise ResultError(
            "This review judged different content than the draft now holds — the "
            "caption changed after the packet was built. Rebuild the review pack so "
            "the clearance covers what would actually be posted."
        )

    try:
        verdicts = [ReviewVerdict.model_validate(v) for v in payload["verdicts"]]
    except ValidationError as exc:
        raise ResultError(f"Verdict schema validation failed:\n{exc}") from exc

    try:
        adjudication = adjudicate(packet, verdicts)
    except RefereeContractError as exc:
        return GateResult(
            kind="review",
            accepted=False,
            summary=f"Referee contract violation: {exc}",
            gates=[{"name": "referee_contract", "ok": False, "detail": str(exc)}],
            saved_to=str(saved),
        )

    record(conn, "draft", str(target_id), adjudication)
    modes = ", ".join(adjudication.failure_modes) or "none"
    return GateResult(
        kind="review",
        accepted=True,
        summary=f"Panel {adjudication.outcome} — failure modes: {modes}.",
        gates=[
            {
                "name": "referee",
                "ok": adjudication.outcome == "cleared",
                "detail": adjudication.rationale,
            }
        ],
        draft_id=str(target_id),
        saved_to=str(saved),
    )


def _enqueue_hypotheses(
    conn: duckdb.DuckDBPyConnection, payload: dict[str, Any], inbox: Path
) -> GateResult:
    """Queue pasted metric proposals. The scanner, not this door, decides their fate."""
    from pydantic import ValidationError

    from padres_analytics.detect.hypothesis.spec import HypothesisSpec
    from padres_analytics.detect.hypothesis.store import enqueue

    saved = _archive(payload, "hypothesis", inbox)
    try:
        specs = [HypothesisSpec.model_validate(s) for s in payload["specs"]]
    except ValidationError as exc:
        raise ResultError(f"Hypothesis schema validation failed:\n{exc}") from exc

    n = enqueue(conn, specs, date.today())
    skipped = len(specs) - n
    detail = f"{n} queued" + (f", {skipped} already explored" if skipped else "")
    return GateResult(
        kind="hypothesis",
        accepted=n > 0,
        summary=f"{detail}. Run discovery to scan them.",
        gates=[{"name": "spec_schema", "ok": True, "detail": detail}],
        saved_to=str(saved),
    )


def land(
    conn: duckdb.DuckDBPyConnection,
    raw: str,
    *,
    inbox: Path,
    cards_dir: Path,
) -> GateResult:
    """Parse, classify, and route a pasted deliverable to its gate path.

    Args:
        conn: Write-mode padres.db connection.
        raw: The pasted text, as typed.
        inbox: Directory the raw deliverable is archived to.
        cards_dir: Output directory for rendered cards.

    Returns:
        The gate-by-gate outcome.

    Raises:
        ResultError: If the text cannot be parsed or classified.
    """
    payload = extract_json(raw)
    kind = classify(payload)

    if kind == "no_story":
        why = str(payload.get("why", "")).strip() or "no reason given"
        _archive(payload, "no_story", inbox)
        return GateResult(
            kind="no_story",
            accepted=True,
            summary=f"Recorded as no story: {why}",
            gates=[{"name": "honest_exit", "ok": True, "detail": why}],
        )
    if kind == "draft":
        return _ingest_draft_payload(conn, payload, inbox, cards_dir)
    if kind == "review":
        return _record_review_payload(conn, payload, inbox)
    return _enqueue_hypotheses(conn, payload, inbox)
