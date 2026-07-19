"""The study dossier — a frozen, auditable record of one investigation.

A study is the shape a FanGraphs piece takes: an anomaly, decomposed into its
parts, traced to a mechanism, placed in context, and closed with a falsifiable
prediction. Each step is a node, each node is a SQL fact with an explicit
verdict, and the whole thing is frozen before anyone writes a word about it.

That freezing is what preserves the engine's central rule. The dossier's
canonical dump is the digit-audit corpus, exactly as ``facts_json`` is for a
card, so narrative written over a dossier can be checked digit by digit against
it. Claude explains the investigation; it never performs it.

The third verdict matters as much as the other two. A node that cannot be
answered returns ``insufficient`` and says why, so a study reports the shape of
its own ignorance instead of quietly omitting the step.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

NodeVerdict = Literal["fired", "quiet", "insufficient"]


class StudyNode(BaseModel):
    """One question in a decomposition, with its answer and its evidence."""

    node_id: str
    question: str = Field(description="The question this step asks, in plain language.")
    verdict: NodeVerdict
    finding: str = Field(
        default="",
        description="Pre-verified sentence stating what the numbers show. Never a "
        "conclusion the numbers don't support.",
    )
    facts: dict[str, str | int | float] = Field(default_factory=dict)
    n: int | None = Field(default=None, description="Sample behind this node.")
    claim_scope: str = ""
    reason: str = Field(
        default="",
        description="Why an insufficient node could not be answered. Required when "
        "the verdict is insufficient — 'we didn't check' is not a finding.",
    )

    def is_evidence(self) -> bool:
        """True when this node contributes a finding to the narrative."""
        return self.verdict == "fired"


class StudyComp(BaseModel):
    """A historical comparable and what happened to him next."""

    player_name: str
    season: int
    similarity: float
    then: dict[str, str | int | float] = Field(default_factory=dict)
    after: dict[str, str | int | float] = Field(default_factory=dict)


class StudyDossier(BaseModel):
    """The complete, frozen record of one study.

    Its ``audit_corpus`` is what a caption or article is checked against. Nothing
    downstream may introduce a number that isn't in here.
    """

    study_id: str
    candidate_id: str | None = None
    subject_id: int
    subject_name: str
    tree: str
    as_of: date
    headline: str = ""
    nodes: list[StudyNode] = Field(default_factory=list)
    comps: list[StudyComp] = Field(default_factory=list)
    prediction: dict[str, Any] | None = None
    coverage_notes: list[str] = Field(default_factory=list)

    def fired(self) -> list[StudyNode]:
        """Nodes that found something, in tree order."""
        return [n for n in self.nodes if n.verdict == "fired"]

    def insufficient(self) -> list[StudyNode]:
        """Nodes that could not be answered — the study's stated blind spots."""
        return [n for n in self.nodes if n.verdict == "insufficient"]

    def audit_corpus(self) -> dict:
        """The canonical dump every downstream number must be traceable to."""
        return self.model_dump(mode="json")

    def digest(self) -> str:
        """Stable hash of the frozen dossier, for change detection."""
        blob = json.dumps(self.audit_corpus(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def summary(self) -> str:
        """One-line account of what the study established and what it couldn't."""
        fired, unknown = len(self.fired()), len(self.insufficient())
        parts = [f"{fired} of {len(self.nodes)} steps found something"]
        if unknown:
            parts.append(f"{unknown} could not be answered")
        return "; ".join(parts)
