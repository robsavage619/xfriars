"""Card-type selection — the visual emerges from a dataset's column roles.

The detector/scanner never names a visual. It emits a role-typed ``ChartDataset``;
this module maps the multiset of column roles to the best card type. ``card_hint``
(set by an editor or the registry) always wins when present.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from padres_analytics.detect.candidates import ChartDataset

# Card types with a template wired today; grows per phase.
IMPLEMENTED_CARDS: frozenset[str] = frozenset({"hero", "slider", "scatter"})

# Full vocabulary the selector may name (templates land across P1-P5).
KNOWN_CARDS: tuple[str, ...] = (
    "hero",
    "slider",
    "beeswarm",
    "scatter",
    "bump",
    "spray",
    "radial",
    "bar",
    "table",
)


def _role_counts(dataset: ChartDataset) -> Counter[str]:
    return Counter(col.role for col in dataset.columns)


def _is_percentile_profile(dataset: ChartDataset) -> bool:
    """True for a Savant-style profile: one metric-per-row, a single 0-100 measure.

    Long format — ``columns = [dimension(metric), measure(percentile, domain 0..100)]``
    with one row per metric. Distinguishes a percentile slider from a plain bar.
    """
    measures = [c for c in dataset.columns if c.role == "measure"]
    dims = [c for c in dataset.columns if c.role == "dimension"]
    return (
        len(measures) == 1
        and len(dims) >= 1
        and measures[0].domain == (0.0, 100.0)
        and len(dataset.rows) >= 3
    )


def select_card(dataset: ChartDataset) -> str:
    """Pick a card type from a dataset's shape.

    Resolution order: explicit ``card_hint`` → spatial → percentile profile →
    distribution backdrop → single hero number → temporal rank → bar/table.

    Args:
        dataset: A validated ChartDataset.

    Returns:
        A card-type name from :data:`KNOWN_CARDS`. Callers should check
        :data:`IMPLEMENTED_CARDS` before rendering.
    """
    if dataset.card_hint:
        return dataset.card_hint

    roles = _role_counts(dataset)

    if roles["spatial_x"] and roles["spatial_y"]:
        return "scatter"

    # Savant-style percentile profile (one 0-100 measure, one row per metric).
    if _is_percentile_profile(dataset):
        return "slider"

    if roles["distribution"]:
        return "beeswarm"

    # A single big number — the lower-third default.
    if dataset.hero is not None and roles["measure"] <= 1 and len(dataset.rows) <= 3:
        return "hero"

    if roles["temporal"] and roles["rank"]:
        return "bump"

    return "bar" if roles["measure"] == 1 and roles["dimension"] else "table"
