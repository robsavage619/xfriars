"""Categorical splits — the axis that makes baseball questions interesting.

Every split a fan actually argues about is categorical: vs lefties, against
breaking balls, in the shadow zone. The hypothesis validator bans quote
characters outright (a good rule — it's the SQL trust boundary), which made all
of them inexpressible.

The way through is not to relax that rule. Here the **engine** renders the
predicate from a curated allowlist: a caller names a column and a value, and if
the pair isn't in :data:`ENUM_COLUMNS` nothing is rendered. String literals
originate in this file, never in LLM-authored text, so ``validate.py`` stays
byte-identical and the injection surface stays closed.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# Statcast pitch classes. Grouping matters more than the raw code: "vs breaking
# balls" is a story, "vs SV" is a rounding error.
PITCH_CLASSES: dict[str, tuple[str, ...]] = {
    "fastball": ("FF", "SI", "FC"),
    "breaking": ("SL", "CU", "ST", "KC", "SV"),
    "offspeed": ("CH", "FS", "FO"),
}

# Attack zones. 1-9 are the strike-zone grid; 11-14 are the four outside
# quadrants. "Heart" and "chase" carry real meaning; a bare zone number doesn't.
ZONE_BUCKETS: dict[str, tuple[int, ...]] = {
    "heart": (1, 2, 3, 4, 5, 6, 7, 8, 9),
    "chase": (11, 12, 13, 14),
}

# Every categorical value the engine will render, keyed by column. A pair absent
# from this map cannot be turned into SQL by any caller, LLM or otherwise.
ENUM_COLUMNS: dict[str, frozenset[str]] = {
    "stand": frozenset({"L", "R"}),
    "p_throws": frozenset({"L", "R"}),
    "type": frozenset({"B", "S", "X"}),
    "pitch_type": frozenset(
        {"FF", "SI", "FC", "SL", "CU", "ST", "KC", "SV", "CH", "FS", "FO", "EP"}
    ),
    "pitch_class": frozenset(PITCH_CLASSES),
    "zone_bucket": frozenset(ZONE_BUCKETS),
    "bb_type": frozenset({"ground_ball", "line_drive", "fly_ball", "popup"}),
    "events": frozenset(
        {"single", "double", "triple", "home_run", "strikeout", "walk", "field_out"}
    ),
}

# Human labels, so framing reads like baseball rather than like a column dump.
_LABELS: dict[tuple[str, str], str] = {
    ("p_throws", "L"): "vs LHP",
    ("p_throws", "R"): "vs RHP",
    ("stand", "L"): "as a lefty",
    ("stand", "R"): "as a righty",
    ("pitch_class", "fastball"): "vs fastballs",
    ("pitch_class", "breaking"): "vs breaking balls",
    ("pitch_class", "offspeed"): "vs offspeed",
    ("zone_bucket", "heart"): "in the zone",
    ("zone_bucket", "chase"): "out of the zone",
}


class SplitError(ValueError):
    """Raised when a split names a column or value outside the allowlist."""


class SplitSpec(BaseModel):
    """One categorical condition, restricted to the curated allowlist."""

    column: str
    value: str
    label: str = Field(default="", description="Display text; derived when omitted.")

    @field_validator("column")
    @classmethod
    def _known_column(cls, v: str) -> str:
        if v not in ENUM_COLUMNS:
            raise ValueError(f"Unknown split column {v!r}. Allowed: {sorted(ENUM_COLUMNS)}")
        return v

    def model_post_init(self, _context: object) -> None:
        """Reject a value outside its column's allowlist."""
        if self.value not in ENUM_COLUMNS[self.column]:
            raise ValueError(
                f"Value {self.value!r} not allowed for split column {self.column!r}. "
                f"Allowed: {sorted(ENUM_COLUMNS[self.column])}"
            )

    def display(self) -> str:
        """Human-readable label for framing and claim scope."""
        return self.label or _LABELS.get((self.column, self.value), f"{self.column}={self.value}")

    def key(self) -> str:
        """Stable identifier for candidate subjects and dedup."""
        return f"{self.column}:{self.value}"


def render_predicate(split: SplitSpec) -> str:
    """Render a validated split as a SQL boolean expression.

    The literal is written here, from the allowlist — never interpolated from
    caller-supplied text. Derived families (``pitch_class``, ``zone_bucket``)
    expand to an ``IN`` list over their real underlying column.

    Args:
        split: A validated split.

    Returns:
        A SQL predicate string.

    Raises:
        SplitError: If the pair is not in the allowlist.
    """
    allowed = ENUM_COLUMNS.get(split.column)
    if allowed is None or split.value not in allowed:
        raise SplitError(f"Split {split.column}={split.value!r} is not in the allowlist.")

    if split.column == "pitch_class":
        codes = PITCH_CLASSES[split.value]
        rendered = ", ".join(f"'{c}'" for c in codes)
        return f"pitch_type IN ({rendered})"

    if split.column == "zone_bucket":
        zones = ZONE_BUCKETS[split.value]
        rendered = ", ".join(str(z) for z in zones)
        return f"zone IN ({rendered})"

    return f"{split.column} = '{split.value}'"


def parse(column: str, value: str, label: str = "") -> SplitSpec:
    """Build a SplitSpec, raising SplitError rather than a pydantic error.

    Args:
        column: Split column.
        value: Split value.
        label: Optional display override.

    Returns:
        The validated split.

    Raises:
        SplitError: If the column or value is outside the allowlist.
    """
    try:
        return SplitSpec(column=column, value=value, label=label)
    except ValueError as exc:
        raise SplitError(str(exc)) from exc


# Contrast pairs worth scanning by default — each is a question people argue
# about, not just two arbitrary slices of the same column.
ContrastName = Literal["platoon", "pitch_class_fb_bb", "zone_discipline"]

CONTRAST_PAIRS: dict[str, tuple[SplitSpec, SplitSpec]] = {
    "platoon": (
        SplitSpec(column="p_throws", value="L"),
        SplitSpec(column="p_throws", value="R"),
    ),
    "pitch_class_fb_bb": (
        SplitSpec(column="pitch_class", value="breaking"),
        SplitSpec(column="pitch_class", value="fastball"),
    ),
    "zone_discipline": (
        SplitSpec(column="zone_bucket", value="chase"),
        SplitSpec(column="zone_bucket", value="heart"),
    ),
}
