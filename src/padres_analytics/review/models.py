"""Referee data model: packets, verdicts, adjudications.

The mechanical gates (digit audit, scope guard, coverage contract, availability)
catch *wrong numbers*. Nothing catches a *wrong argument* — an arbitrary endpoint,
a confounded comparison, a survivorship-filtered population presented as "the
league", a causal claim with no control, or a number that is extreme and means
nothing. The referee is the reasoning pass that has to sign off before a draft
can be approved.

The hard invariant: a referee returns verdicts and critique, never numbers. It
may block, it may rewrite caption prose, it may never touch ``facts_json``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["PASS", "REVISE", "BLOCK"]

LensName = Literal["statistician", "causal", "coverage", "editor", "voice"]

# Controlled vocabulary. A free-text reason can't be counted, so it can't be
# learned from; these keys become features in the editorial prior (Phase 2) and
# summary stats in the hypothesis context pack (Phase 4c).
FailureMode = Literal[
    # statistician
    "arbitrary_endpoint",
    "cherry_picked_window",
    "wrong_denominator",
    "survivorship_population",
    "sample_too_small",
    "multiplicity_unaccounted",
    "correlated_conjunction",
    # causal
    "causal_no_control",
    "confounded_comparison",
    # coverage
    "scope_overreach",
    "stale_source",
    "coverage_mismatch",
    "padres_only_as_league",
    # editor
    "trivial",
    "tautological",
    "filter_artifact",
    # voice
    "voice_tell",
    "register_mismatch",
    "jargon_ungloss",
]

_LENS_MODES: dict[LensName, set[str]] = {
    "statistician": {
        "arbitrary_endpoint",
        "cherry_picked_window",
        "wrong_denominator",
        "survivorship_population",
        "sample_too_small",
        "multiplicity_unaccounted",
        "correlated_conjunction",
    },
    "causal": {"causal_no_control", "confounded_comparison"},
    "coverage": {
        "scope_overreach",
        "stale_source",
        "coverage_mismatch",
        "padres_only_as_league",
    },
    "editor": {"trivial", "tautological", "filter_artifact"},
    "voice": {"voice_tell", "register_mismatch", "jargon_ungloss"},
}

# Claims where an unsure referee should block rather than wave through: a wrong
# "first ever" or an unearned causal story costs more than a missed post.
_BLOCK_ON_UNCERTAIN: frozenset[LensName] = frozenset({"causal", "coverage"})

# Below this, a verdict counts as uncertain.
CONFIDENCE_FLOOR = 0.6


class ReviewVerdict(BaseModel):
    """One lens's judgment of one packet."""

    lens: LensName
    verdict: Verdict
    failure_mode: FailureMode | None = None
    evidence: str = Field(
        default="",
        description="Why, in one or two sentences, citing the packet. Not a restatement.",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    suggested_caption: str | None = Field(
        default=None,
        description="Replacement caption text for a REVISE. Prose only — a referee "
        "that wants a different number must BLOCK instead.",
    )

    def is_uncertain(self) -> bool:
        """True when this verdict is too low-confidence to trust as stated."""
        return self.confidence < CONFIDENCE_FLOOR

    def effective_verdict(self) -> Verdict:
        """The verdict after applying the asymmetric uncertainty rule.

        An unsure statistician or editor is not enough to kill a card; an unsure
        causal or coverage lens is, because those failures ship a false claim.
        """
        if self.verdict == "PASS" and self.is_uncertain() and self.lens in _BLOCK_ON_UNCERTAIN:
            return "BLOCK"
        return self.verdict


class Adjudication(BaseModel):
    """The panel's combined decision for one packet."""

    outcome: Literal["cleared", "revise", "blocked"]
    packet_hash: str
    verdicts: list[ReviewVerdict]
    failure_modes: list[FailureMode] = Field(default_factory=list)
    rationale: str = ""

    def blocking_lenses(self) -> list[LensName]:
        """Lenses whose effective verdict was BLOCK."""
        return [v.lens for v in self.verdicts if v.effective_verdict() == "BLOCK"]


class ReviewPacket(BaseModel):
    """Everything a referee needs to judge the reasoning — not just the digits.

    Deliberately includes ``not_checked``: a referee can only catch what the
    packet shows, so the gaps are stated rather than left to be inferred from
    silence.
    """

    target_kind: Literal["draft", "candidate", "study"]
    target_id: str
    as_of: date

    claim: str = Field(description="The engine-selected framing/headline being asserted.")
    caption: str = ""
    first_reply: str = ""

    facts: dict = Field(default_factory=dict)
    claim_scope: str = ""
    coverage_window: str = ""
    population_label: str = ""
    population_size: int | None = None

    detector: str = ""
    lens: str = ""
    rarity: float | None = None
    battery_size: int | None = Field(
        default=None,
        description="Comparisons run the day this surfaced — the multiplicity context.",
    )

    provenance: list[dict] = Field(default_factory=list)
    coverage_status: list[dict] = Field(default_factory=list)
    not_checked: list[str] = Field(default_factory=list)

    def packet_hash(self) -> str:
        """Stable hash over everything a verdict depends on.

        Re-rendering or re-captioning changes this, which invalidates any prior
        clearance — an approval must never ride along on content it never saw.
        """
        material = {
            "target_id": self.target_id,
            "claim": self.claim,
            "caption": self.caption,
            "first_reply": self.first_reply,
            "facts": self.facts,
            "claim_scope": self.claim_scope,
        }
        blob = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def lens_owns(lens: LensName, mode: FailureMode) -> bool:
    """True when ``mode`` is in ``lens``'s remit.

    Keeps a lens from reaching outside its brief — a voice reviewer calling
    ``sample_too_small`` means the panel isn't doing what it was told.
    """
    return mode in _LENS_MODES[lens]
