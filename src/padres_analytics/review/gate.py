"""Adjudication and enforcement — the referee's decision rule and its hard limits.

Two things live here. :func:`adjudicate` turns a panel's verdicts into one
outcome. :func:`assert_no_fact_mutation` enforces the invariant that makes the
whole thing safe: a referee may block a card or rewrite its prose, but may never
change a number. If a referee thinks a number is wrong, the only legal outcome is
BLOCK with the defect named — the fix is a detector change and a re-run.
"""

from __future__ import annotations

import logging

from padres_analytics.review.models import (
    Adjudication,
    FailureMode,
    ReviewPacket,
    ReviewVerdict,
    lens_owns,
)

logger = logging.getLogger(__name__)


class RefereeContractError(ValueError):
    """Raised when a verdict payload violates the referee's contract."""


class NotClearedError(ValueError):
    """Raised when a target is advanced without a matching clearance."""


# A single REVISE on prose is a copy edit; two lenses asking for changes means
# the card itself needs rework.
_REVISE_QUORUM = 2


def adjudicate(packet: ReviewPacket, verdicts: list[ReviewVerdict]) -> Adjudication:
    """Combine panel verdicts into one outcome.

    The rule is deliberately asymmetric:

    - **Any BLOCK blocks.** Not majority vote — one sound refutation is enough,
      because the cost of shipping a false claim outweighs a missed post.
    - **Two or more REVISE** means revise; a lone REVISE is a prose note.
    - Uncertainty resolves to BLOCK for the causal and coverage lenses and to
      the stated verdict elsewhere (see ``ReviewVerdict.effective_verdict``).

    Args:
        packet: The packet the panel judged.
        verdicts: One verdict per lens that ran.

    Returns:
        The adjudication, stamped with the packet hash it applies to.

    Raises:
        RefereeContractError: If a verdict is malformed or reaches outside its lens.
    """
    if not verdicts:
        raise RefereeContractError("No verdicts supplied — an empty panel is not a clearance.")

    seen: set[str] = set()
    for v in verdicts:
        if v.lens in seen:
            raise RefereeContractError(f"Duplicate verdict for lens {v.lens!r}.")
        seen.add(v.lens)
        if v.verdict in ("BLOCK", "REVISE") and v.failure_mode is None:
            raise RefereeContractError(
                f"Lens {v.lens!r} returned {v.verdict} without a failure_mode. "
                "A rejection must name what is wrong."
            )
        if v.failure_mode is not None and not lens_owns(v.lens, v.failure_mode):
            raise RefereeContractError(
                f"Lens {v.lens!r} reported {v.failure_mode!r}, which is outside its remit."
            )

    effective = [(v, v.effective_verdict()) for v in verdicts]
    blocking = [v for v, e in effective if e == "BLOCK"]
    revising = [v for v, e in effective if e == "REVISE"]

    modes: list[FailureMode] = [
        v.failure_mode for v in blocking + revising if v.failure_mode is not None
    ]

    if blocking:
        outcome = "blocked"
        rationale = "; ".join(f"{v.lens}: {v.failure_mode} — {v.evidence}" for v in blocking)
    elif len(revising) >= _REVISE_QUORUM:
        outcome = "revise"
        rationale = "; ".join(f"{v.lens}: {v.failure_mode}" for v in revising)
    elif revising:
        outcome = "revise"
        rationale = f"prose note from {revising[0].lens}: {revising[0].failure_mode}"
    else:
        outcome = "cleared"
        rationale = f"cleared by {len(verdicts)} lenses"

    logger.info(
        "referee: %s target=%s outcome=%s modes=%s",
        packet.target_kind,
        packet.target_id,
        outcome,
        modes or "none",
    )
    return Adjudication(
        outcome=outcome,
        packet_hash=packet.packet_hash(),
        verdicts=verdicts,
        failure_modes=modes,
        rationale=rationale,
    )


def assert_no_fact_mutation(original_facts: dict, submitted_facts: dict | None) -> None:
    """Enforce that a review round-trip did not alter the audited numbers.

    Args:
        original_facts: ``facts_json`` as the engine computed it.
        submitted_facts: Facts echoed back with the verdicts, if any.

    Raises:
        RefereeContractError: If the submitted facts differ in any way.
    """
    if submitted_facts is None:
        return
    if submitted_facts != original_facts:
        raise RefereeContractError(
            "Review payload modified facts_json. The referee may block a claim or "
            "rewrite prose, never change a number — re-run the detector instead."
        )


def assert_caption_digits_unchanged(
    original_caption: str,
    suggested_caption: str,
    facts: dict,
) -> None:
    """Enforce that a suggested caption introduces no new numbers.

    A REVISE may rephrase, soften, or add a qualifier. It may not smuggle in a
    figure, because prose from a referee never went through the digit audit.

    Args:
        original_caption: The caption under review.
        suggested_caption: The referee's replacement text.
        facts: The audited fact payload.

    Raises:
        RefereeContractError: If the suggestion contains a number absent from
            both the original caption and the facts.
    """
    from padres_analytics.tweets.verify import digit_audit

    offenders = digit_audit(suggested_caption, facts)
    if not offenders:
        return

    original_tokens = set(digit_audit(original_caption, facts))
    introduced = [o for o in offenders if o not in original_tokens]
    if introduced:
        raise RefereeContractError(
            f"Suggested caption introduces unaudited numbers: {introduced}. "
            "A referee may rewrite prose but may not add figures."
        )


def require_clearance(adjudication: Adjudication | None, packet: ReviewPacket) -> None:
    """Refuse to advance a target that has no matching clearance.

    Args:
        adjudication: The stored adjudication, if any.
        packet: The packet as it stands *now*.

    Raises:
        NotClearedError: If no adjudication exists, it did not clear, or it was made
            against different content.
    """
    if adjudication is None:
        raise NotClearedError(
            f"{packet.target_kind} {packet.target_id} has no referee adjudication. "
            f"Run 'pad review pack {packet.target_id}' and record verdicts first."
        )
    if adjudication.outcome != "cleared":
        raise NotClearedError(
            f"{packet.target_kind} {packet.target_id} was {adjudication.outcome}: "
            f"{adjudication.rationale}"
        )
    current = packet.packet_hash()
    if adjudication.packet_hash != current:
        raise NotClearedError(
            f"Clearance is stale: it was made against packet {adjudication.packet_hash}, "
            f"but the content now hashes to {current}. Re-review after any re-render "
            "or caption change."
        )
