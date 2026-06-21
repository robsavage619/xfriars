"""The daily briefing — the engine's heartbeat.

One run that strings the pieces into a routine instead of a kit: grade any
predictions that have come due, discover today's strongest defensible story,
verify every number against source, render the card, draft a caption a casual fan
can follow, log the call as a falsifiable prediction, and queue it on the Board for
a one-tap human approval. Nothing posts — the human gate stays.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from padres_analytics.board import add_card
from padres_analytics.detect.angles import StoryAngle, discover, lead_gloss
from padres_analytics.detect.reconcile import ReconcileError, verify_angle
from padres_analytics.predict import grade_predictions, log_predictions, scorecard
from padres_analytics.render.story_infographic import render_angle

if TYPE_CHECKING:
    import duckdb

RenderFn = Callable[[StoryAngle, Path, str], Path]


@dataclass(frozen=True)
class Briefing:
    """The result of one daily run — what to show the human."""

    story: StoryAngle | None
    caption: str | None
    image_path: str | None
    logged: int  # predictions logged this run
    graded: dict[str, int]  # matured predictions resolved this run
    scorecard: dict[str, object]
    notes: list[str] = field(default_factory=list)


def build_caption(angle: StoryAngle) -> str:
    """A starting caption: the verdict, a plain-language gloss, the confidence.

    Uses only the angle's own (audited) numbers plus a jargon gloss with no numbers
    of its own, so it stays digit-audit-safe. A draft for the human to sharpen, not
    a finished post — the value voice lives in the caption skill.
    """
    parts = [angle.headline]
    gloss = lead_gloss(angle)
    if gloss:
        parts.append(f"In plain terms: {gloss}")
    coverage = angle.caveats[0] if angle.caveats else ""
    tail = f"{angle.confidence.capitalize()} confidence"
    parts.append(f"{tail} — {coverage}." if coverage else f"{tail}.")
    return " ".join(parts)


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

    angles = discover(conn, season, as_of=today)
    if not angles:
        notes.append("No story cleared the significance gates today.")
        return Briefing(None, None, None, 0, graded, card, notes)

    chosen = angles[0]
    try:
        verify_angle(conn, chosen)
    except ReconcileError as exc:
        notes.append(f"Top story ({chosen.key}) failed source verification: {exc}")
        return Briefing(None, None, None, 0, graded, card, notes)

    image = render_fn(chosen, out_dir, f"daily_{chosen.key}_{season}")
    caption = build_caption(chosen)
    logged = log_predictions(conn, [chosen], season, as_of=today)
    add_card(conn, chosen, str(image), kind="season_story", reconciled=True, caption=caption)
    notes.append(f"Queued {chosen.key} on the Board for approval.")
    return Briefing(chosen, caption, str(image), logged, graded, card, notes)
