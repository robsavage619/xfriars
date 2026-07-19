"""The daily briefing — the engine's heartbeat.

One run that strings the pieces into a routine instead of a kit: grade any
predictions that have come due, discover today's strongest defensible story,
verify every number against source, render the card, draft a caption a casual fan
can follow, log the call as a falsifiable prediction, and queue it on the Board for
a one-tap human approval. Nothing posts — the human gate stays.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from padres_analytics.board import add_card
from padres_analytics.caption import build_caption, caption_audit, first_reply
from padres_analytics.detect.angles import StoryAngle, discover
from padres_analytics.detect.reconcile import ReconcileError, verify_angle
from padres_analytics.predict import grade_predictions, log_predictions, scorecard
from padres_analytics.render.story_infographic import render_angle

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import duckdb

RenderFn = Callable[[StoryAngle, Path, str], Path]


@dataclass(frozen=True)
class Briefing:
    """The result of one daily run — what to show the human."""

    story: StoryAngle | None
    caption: str | None  # the main post (hook + reply-driving question)
    reply: str | None  # the author's first reply (gloss + where the link goes)
    image_path: str | None
    logged: int  # predictions logged this run
    graded: dict[str, int]  # matured predictions resolved this run
    scorecard: dict[str, object]
    warnings: list[str] = field(default_factory=list)  # caption_audit on the post
    notes: list[str] = field(default_factory=list)


def _run_hypothesis_cycle(conn: duckdb.DuckDBPyConnection, today: date) -> list[str]:
    """Drain yesterday's queued hypotheses, then stage tomorrow's context pack.

    The proposal step stays agent-mediated — Claude reads the pack in a session
    and enqueues specs — so this runs the two ends the engine owns: scanning
    whatever is queued, and leaving the next pack ready. Without both halves
    running daily the machinery is complete and never fires, which is exactly
    what it did for its first months.

    Failures are reported, never raised: discovery is additive to the briefing,
    and a bad spec must not cost the day its story.
    """
    notes: list[str] = []

    try:
        # Detectors register as an import side-effect, and nothing else on the
        # daily path imports this one — without the import it is simply absent.
        import padres_analytics.detect.hypothesis.detector  # noqa: F401
        from padres_analytics.detect.base import emit, get_detector

        detector = get_detector("hypothesis")
        candidates = detector.run(conn, today)
        if candidates:
            emit(conn, candidates)
            notes.append(f"Hypothesis scan: {len(candidates)} candidate(s) from queued proposals.")
    except Exception as exc:
        logger.warning("daily: hypothesis scan failed: %s", exc)
        notes.append(f"Hypothesis scan failed: {exc}")

    try:
        from padres_analytics.detect.hypothesis.context import build_context_pack

        pack = build_context_pack(conn, today)
        out = Path("inbox") / f"hypothesis_context_{today.isoformat()}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(pack, indent=2, default=str), encoding="utf-8")
        explored = len(pack.get("explored", []) or [])
        notes.append(
            f"Context pack for tomorrow written to {out} "
            f"({explored} explored spec(s) on the ledger)."
        )
    except Exception as exc:
        logger.warning("daily: context pack failed: %s", exc)
        notes.append(f"Context pack failed: {exc}")

    return notes


def run_briefing(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    *,
    as_of: date | None = None,
    out_dir: Path,
    render_fn: RenderFn = render_angle,
) -> Briefing:
    """Run the daily routine and return what's ready for approval.

    Grading and the scorecard always run. A story is rendered and queued only when
    one clears the gates *and* its numbers verify against source — otherwise the
    briefing reports the quiet day honestly rather than forcing a card.
    """
    today = as_of or date.today()
    notes: list[str] = []

    graded = grade_predictions(conn, as_of=today)
    card = scorecard(conn)

    # Recompute priors before discovery so today's ranking reflects yesterday's
    # verdicts. Cheap (pure SQL) and stateless, so it's safe to run every day.
    try:
        from padres_analytics.learn.run import learn

        learned = learn(conn, today)
        moved = len(learned.informative())
        if moved:
            notes.append(f"Priors updated from {learned.observations} verdict(s); {moved} moved.")
        elif learned.notes:
            notes.append(learned.notes[0])
    except Exception as exc:  # a learning failure must never block the briefing
        logger.warning("daily: learning pass failed: %s", exc)
        notes.append(f"Learning pass failed (ranking unchanged): {exc}")

    notes.extend(_run_hypothesis_cycle(conn, today))

    angles = discover(conn, season, as_of=today)
    if not angles:
        notes.append("No story cleared the significance gates today.")
        return Briefing(None, None, None, None, 0, graded, card, [], notes)

    chosen = angles[0]
    try:
        verify_angle(conn, chosen)
    except ReconcileError as exc:
        notes.append(f"Top story ({chosen.key}) failed source verification: {exc}")
        return Briefing(None, None, None, None, 0, graded, card, [], notes)

    image = render_fn(chosen, out_dir, f"daily_{chosen.key}_{season}")
    caption = build_caption(chosen)
    reply = first_reply(chosen)
    warnings = caption_audit(caption)
    logged = log_predictions(conn, [chosen], season, as_of=today)
    add_card(conn, chosen, str(image), kind="season_story", reconciled=True, caption=caption)
    notes.append(f"Queued {chosen.key} on the Board for approval.")
    return Briefing(chosen, caption, reply, str(image), logged, graded, card, warnings, notes)
