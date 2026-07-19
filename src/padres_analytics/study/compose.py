"""Turn a frozen dossier into a renderable card.

Composition is selection, not authorship. Which panels appear is decided by
which nodes fired, so the card's shape is a consequence of what the
investigation found rather than a template with numbers poured into it — the
pattern this replaces was a hardcoded roster of five players that never varied.

Nothing here computes. Every value on the resulting card is copied from a node's
facts or its pre-verified finding string, so the dossier remains the audit
corpus for anything downstream.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from padres_analytics.detect.candidates import (
    StatCandidate,
    StoryBlock,
    StoryCard,
    make_candidate_id,
)
from padres_analytics.study.dossier import StudyDossier, StudyNode

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Panels a story card can hold. A study with more fired nodes than this shows
# the earliest ones — the decomposition runs anomaly-first, so truncation drops
# the supporting detail rather than the finding.
_MAX_BLOCKS = 6

# How each node presents when it fires: the fact carrying its percentile, the
# fact carrying its display value, and how to read the direction.
_PANEL_SPEC: dict[str, dict[str, str]] = {
    "gap": {
        "metric": "wOBA vs Expected",
        "value_fact": "gap",
        "percentile_fact": "gap_percentile",
        "value_format": ".3f",
    },
    "components": {
        "metric": "Average vs Power",
        "value_fact": "ba_gap",
        "value_format": ".3f",
    },
    "contact": {
        "metric": "Exit Velocity",
        "value_fact": "avg_exit_velocity",
        "percentile_fact": "exit_velocity_percentile",
        "value_format": ".1f",
    },
    "approach": {
        "metric": "Chase Rate",
        "value_fact": "chase_now",
        "value_format": ".1f",
    },
}


def _tone(node: StudyNode) -> str:
    """Read direction from the node's own facts, never from prose.

    A luck gap in the hitter's favour and one against him look identical in the
    finding string; only the sign of the fact distinguishes them.
    """
    if node.node_id == "gap":
        gap = node.facts.get("gap")
        if isinstance(gap, int | float):
            # A positive gap means results trail contact quality — bad results,
            # but a *good* sign for what comes next. Neutral is the honest tone.
            return "neutral"
    if node.node_id == "contact":
        pct = node.facts.get("exit_velocity_percentile")
        if isinstance(pct, int | float):
            return "good" if pct >= 60 else "bad"
    if node.node_id == "approach":
        z = node.facts.get("cohort_z")
        if isinstance(z, int | float) and abs(z) >= 1.0:
            return "bad" if z > 0 else "good"
    return "neutral"


def _block(node: StudyNode, subject_id: int) -> StoryBlock | None:
    """Build one panel from a fired node, or None when it has no display spec."""
    spec = _PANEL_SPEC.get(node.node_id)
    if spec is None:
        return None

    raw = node.facts.get(spec["value_fact"])
    if not isinstance(raw, int | float):
        return None

    value = f"{float(raw):{spec['value_format']}}"
    percentile = node.facts.get(spec.get("percentile_fact", ""))

    return StoryBlock(
        label=node.question.rstrip("?"),
        metric=spec["metric"],
        value=value,
        percentile=int(percentile) if isinstance(percentile, int | float) else None,
        note=node.finding,
        tone=_tone(node),  # type: ignore[arg-type]
        player_id=subject_id,
    )


def story_card_from_dossier(dossier: StudyDossier) -> StoryCard | None:
    """Compose a story card from the nodes that actually fired.

    Args:
        dossier: A frozen dossier.

    Returns:
        The card, or None when too little fired to be worth rendering — a study
        that answered one question is a fact, not a story.
    """
    blocks = [b for b in (_block(n, dossier.subject_id) for n in dossier.fired()) if b is not None]
    if len(blocks) < 2:
        logger.info(
            "study %s: %d panel(s) — not enough fired to compose a card",
            dossier.study_id,
            len(blocks),
        )
        return None

    gap_node = next((n for n in dossier.nodes if n.node_id == "gap"), None)
    hero = None
    if gap_node is not None and isinstance(gap_node.facts.get("gap"), int | float):
        gap = float(gap_node.facts["gap"])
        pct = gap_node.facts.get("gap_percentile")
        hero = {
            "value": f"{abs(gap):.3f}",
            "label": "wOBA owed" if gap > 0 else "wOBA borrowed",
            "context": (
                f"Wider than {pct}% of qualified hitters"
                if isinstance(pct, int | float)
                else "vs expected"
            ),
        }

    # The closing line names what the study could not settle. A deep dive that
    # hides its open questions is selling a conclusion it didn't reach. It does
    # not restate the finding — the hero and the first panel already carry that,
    # and repeating it wastes the one line the reader is most likely to finish on.
    unknowns = dossier.insufficient()
    if unknowns:
        narrative = f"Still open: {unknowns[0].question.rstrip('?').lower()}."
    else:
        quiet = [n.node_id for n in dossier.nodes if n.verdict == "quiet"]
        narrative = (
            f"Every step of the decomposition was answerable; {len(quiet)} found nothing."
            if quiet
            else "Every step of the decomposition fired."
        )

    scope = gap_node.claim_scope if gap_node else str(dossier.as_of.year)
    return StoryCard(
        title=dossier.subject_name.upper(),
        subtitle=f"{dossier.as_of.year} · what the numbers say and don't",
        as_of=dossier.as_of,
        hero=hero,
        blocks=blocks[:_MAX_BLOCKS],
        narrative=narrative,
        source="Baseball Savant / MLB Stats API",
        headline=dossier.headline,
        claim_scope=scope,
    )


def candidate_from_dossier(dossier: StudyDossier) -> StatCandidate | None:
    """Wrap a composed study card as a candidate so it joins the normal path.

    A study becomes reviewable, rankable and postable through exactly the same
    machinery as any other card — including the referee, which is the point.
    """
    card = story_card_from_dossier(dossier)
    if card is None:
        return None

    from padres_analytics.detect.scoring import novelty_score

    fired = len(dossier.fired())
    score, components = novelty_score(
        {
            # A study earns its rank from how much of the decomposition it could
            # actually answer, not from the extremity of the trigger alone.
            "rarity": min(0.75 + 0.05 * fired, 0.95),
            "magnitude": 0.85,
            "timeliness": 0.80,
            "rootability": 0.92,
            "legibility": 0.80,
        },
        detector="study",
    )

    subject = f"SDP|study|{dossier.tree}|{dossier.subject_id}|{dossier.as_of.year}"
    payload = card.model_dump(mode="json")
    return StatCandidate(
        candidate_id=make_candidate_id("study", subject, payload),
        detector="study",
        subject=subject,
        as_of=dossier.as_of,
        category="season",
        payload_kind="story",
        facts_json=payload,
        provenance_json=[
            {
                "source_table": "study_dossiers",
                "study_id": dossier.study_id,
                "dossier_digest": dossier.digest(),
                "tree": dossier.tree,
                "as_of": str(dossier.as_of),
            }
        ],
        coverage_window=f"{dossier.as_of.year}-{dossier.as_of.year}",
        claim_scope=card.claim_scope,
        novelty_score=score,
        novelty_components=components,
    )
