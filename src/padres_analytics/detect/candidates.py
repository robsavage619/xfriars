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


# ── ChartDataset — typed dataset whose shape selects the visual ─────────────────

SemanticRole = Literal[
    "dimension",  # categorical key per row (player, team, season-label)
    "measure",  # a numeric value to encode
    "distribution",  # population values forming a backdrop (beeswarm/ridgeline)
    "spatial_x",  # horizontal spatial coord (launch angle, field x)
    "spatial_y",  # vertical spatial coord (exit velo, field y)
    "temporal",  # ordered time axis (season, game date, PA index)
    "categorical",  # secondary grouping / facet
    "rank",  # integer rank
    "label",  # display-only string; never numeric-audited
]


class Column(BaseModel):
    """One column of a ChartDataset, tagged with the role that drives encoding."""

    key: str
    label: str  # axis/header label rendered on the card
    role: SemanticRole
    unit: str | None = None  # "ft/s", "%", "mph"
    format: str | None = None  # python format spec: ".3f", "d", "pct1"
    higher_is_better: bool | None = None
    domain: tuple[float, float] | None = None  # explicit scale → deterministic render


class Mark(BaseModel):
    """A protagonist callout — the row the card should emphasize (the Padre)."""

    row_index: int  # index into ChartDataset.rows
    label: str  # "Tatis Jr."
    note: str | None = None  # "95th pct"


class ChartDataset(BaseModel):
    """A verified, role-typed dataset. The card type emerges from its column roles.

    ``model_dump(mode="json")`` is the digit-audit corpus: every renderable number
    in ``rows``, ``hero``, ``domain``, and ``facts`` lands in the dumped string, so
    the caption can only cite numbers that originate here. ``framing`` is the
    engine-selected, already-verified claim string; the caption-writer may use it
    verbatim but may never upgrade its scope.
    """

    kind: Literal["dataset"] = "dataset"
    title: str
    subtitle: str | None = None
    as_of: date
    columns: Annotated[list[Column], Field(min_length=1)]
    rows: list[list[str | int | float | None]]  # row-major, aligned to columns
    highlight: list[Mark] = []
    hero: dict | None = None  # {value, label, context} — the one big number
    framing: str = ""  # engine-selected, pre-verified claim string
    population_label: str = ""  # "Qualified MLB hitters, 2026" — for distribution cards
    n: int | None = None  # sample size, printed when a distribution is shown
    source: str
    headline: str  # one-sentence hook for Claude; never rendered on card
    claim_scope: str
    card_hint: str | None = None  # selector override ("slider", "beeswarm", ...)
    facts: dict[str, str | int | float] = {}  # flat audited scalars


class SpatialPoint(BaseModel):
    """One plotted event in a spatial visual — a batted ball or a pitch."""

    x: float  # transformed field-feet (spray), plate-feet (zone), or inches (movement)
    y: float
    kind: str | None = None  # outcome class → drives fill (out/single/.../home_run)
    value: float | None = None  # optional magnitude (exit velo, xwOBA, distance)
    label: str | None = None  # in-situ annotation, used sparingly


class SpatialDataset(BaseModel):
    """Event-level spatial visual with a mandatory rigor harness.

    Unlike :class:`ChartDataset` (column-role driven), spatial cards plot many
    raw coordinate points. The rigor fields (``n``/``coverage``/``handedness``/
    ``pov``/``park``) are REQUIRED by construction — a card that cannot state its
    denominators must not render. This enforces accuracy-first at the type level.
    """

    kind: Literal["spatial"] = "spatial"
    card: Literal[
        "spray",
        "zone",
        "movement",
        "hr",
        "launch",
        "rolling",
        "hotcold",
        "release",
        "swingtake",
        "batspeed",
    ]
    title: str
    subtitle: str | None = None
    as_of: date
    points: Annotated[list[SpatialPoint], Field(min_length=1)]
    hero: dict | None = None  # optional {value, label, context} callout
    # ── rigor harness (required — printed on the card face) ──
    n: int  # sample size (BBE / pitches / HR)
    coverage: str  # "Since May 1" / "2026 season"
    handedness: str  # "vs RHP" | "vs LHP" | "All"
    park: str  # "Petco Park" | "All parks"
    pov: str = ""  # "Catcher's POV" for zones/movement; "" for spray
    note: str = ""  # small-sample / shift-era caveat line
    source: str
    headline: str  # one-sentence hook for Claude; never rendered on card
    claim_scope: str


class StoryBlock(BaseModel):
    """One panel in a story card — a player + a single percentile callout."""

    label: str  # player or topic name
    metric: str  # "xwOBA" / "Hard-Hit %"
    value: str  # display string, e.g. "34th"
    percentile: int | None = None  # 0-100, drives the mini-bar
    note: str = ""  # one-line read, e.g. "the captain's cooled"
    tone: Literal["good", "bad", "neutral"] = "neutral"
    player_id: int | None = None  # resolves a headshot when present


class StoryCard(BaseModel):
    """A composed infographic — a hero number plus several panels telling a story.

    Unlike a single chart, a story card narrates a situation: the macro hook, a
    handful of player callouts (each with a percentile bar + read), and a closing
    line. Every number must originate from verified data; ``model_dump`` is the
    digit-audit corpus like the other payloads.
    """

    kind: Literal["story"] = "story"
    title: str
    kicker: str = "San Diego Padres"
    subtitle: str | None = None
    as_of: date
    hero: dict | None = None  # {value, label, context} — the macro hook
    blocks: Annotated[list[StoryBlock], Field(min_length=1, max_length=6)]
    narrative: str = ""  # closing one-liner
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


# Fields that describe *when the card was drawn* rather than what it claims.
# ``as_of`` is the render date and ``subtitle`` is derived from it ("Career WAR
# as a Padre · through 2026-07-19"), so leaving them in the hash made the id a
# function of the calendar: an unchanged stat minted a fresh candidate every
# run. Tatis sat 0.1 WAR from third all-time for five weeks and produced two
# top-of-board candidates whose baseball facts were byte-identical.
#
# ``metric_year`` deliberately stays in the hash — a 2025 mark and a 2026 mark
# are genuinely different claims.
_VOLATILE_RENDER_FIELDS = ("as_of", "subtitle")


def make_candidate_id(detector: str, subject: str | None, facts: dict) -> str:
    """Compute a deterministic 16-char hex ID from detector + subject + claim.

    The id identifies the *claim*, not the rendering of it, so restating an
    unchanged stat tomorrow collides with today's id and is deduped by
    :func:`~padres_analytics.detect.base.emit`.

    Args:
        detector: Detector name (e.g. "on_this_day").
        subject: Optional subject label (e.g. "SDP|Jun 9").
        facts: The facts_json dict (will be JSON-serialized with sorted keys).

    Returns:
        16-char hex string (first 8 bytes of SHA-256).
    """
    claim = {k: v for k, v in facts.items() if k not in _VOLATILE_RENDER_FIELDS}
    payload = f"{detector}|{subject or ''}|{json.dumps(claim, sort_keys=True, default=str)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def audit_corpus(payload: TablePayload | SeriesPayload | ChartDataset | SpatialDataset) -> str:
    """Return the canonical JSON string the digit-audit greps against.

    Every renderable number in the payload must appear in this string. Used so the
    table path and the dataset path share one definition of "the audited corpus".

    Args:
        payload: A validated payload object.

    Returns:
        Deterministic JSON string (sorted keys) of the payload dump.
    """
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, default=str)
