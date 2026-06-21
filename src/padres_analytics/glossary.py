"""Plain-language glossary — make sabermetrics approachable for casual fans.

Every number the engine surfaces (xwOBA, FIP, xwOBACON, Barrel%) is a wall to a
fan who doesn't already speak the language. This module is the single source of
truth that turns any stat into *what it means* plus *where this value ranks*, so
accessibility is enforced by construction rather than re-improvised per card.

The benchmark anchors are standard sabermetric rules of thumb (FanGraphs-style
reference scales), not tuned thresholds — they describe the league, not the
account's taste. Where the engine has a live league value (e.g. league xwOBA), a
caller should prefer that; these are the static fallbacks a casual gloss needs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    """One stat, explained for someone who has never heard of it.

    Attributes:
        name: Display name (e.g. "xwOBA").
        plain: One sentence a casual fan understands — no jargon inside it.
        average: League-average reference value.
        elite: Roughly the value that reads as elite.
        higher_is_better: Direction of "good".
        scale: How the value is written ("rate3" = .371, "pct" = 12%, "runs" = 3.20).
    """

    name: str
    plain: str
    average: float
    elite: float
    higher_is_better: bool
    scale: str = "rate3"


# Keyed by a stable metric slug. Anchors are league rules of thumb, not gates.
GLOSSARY: dict[str, Term] = {
    "woba": Term(
        "wOBA",
        "one number for everything a hitter does at the plate, weighted by how much "
        "each outcome actually helps a team score",
        0.320,
        0.370,
        True,
    ),
    "xwoba": Term(
        "xwOBA",
        "what a hitter's contact *should* be worth based on how hard and at what angle "
        "he hits the ball — stripping out luck and where the fielders happened to stand",
        0.320,
        0.370,
        True,
    ),
    "xwobacon": Term(
        "xwOBA on contact",
        "the same expected value, but only counting balls he put in play — a read on "
        "how well he's squaring the ball up, ignoring walks and strikeouts",
        0.370,
        0.450,
        True,
    ),
    "obp": Term(
        "on-base percentage",
        "how often he reaches base — hits, walks, and hit-by-pitches all count",
        0.320,
        0.370,
        True,
    ),
    "era": Term(
        "ERA",
        "earned runs a pitcher allows per nine innings — the classic run-prevention number",
        4.20,
        3.20,
        False,
        scale="runs",
    ),
    "fip": Term(
        "FIP",
        "what a pitcher's ERA *should* be if you judge only what he controls — strikeouts, "
        "walks, and home runs — and take luck and his defense out of it",
        4.20,
        3.20,
        False,
        scale="runs",
    ),
    "barrel_pct": Term(
        "Barrel%",
        "how often he hits a ball in the perfect speed-and-angle window that almost always "
        "goes for extra bases",
        8.0,
        14.0,
        True,
        scale="pct",
    ),
    "hard_hit_pct": Term(
        "Hard-Hit%",
        "the share of his batted balls hit at 95 mph or harder",
        40.0,
        50.0,
        True,
        scale="pct",
    ),
    "chase_pct": Term(
        "chase rate",
        "how often he swings at pitches outside the strike zone — lower is more disciplined",
        28.0,
        22.0,
        False,
        scale="pct",
    ),
    "k_pct": Term(
        "strikeout rate",
        "the share of his plate appearances that end in a strikeout — lower is better",
        22.0,
        16.0,
        False,
        scale="pct",
    ),
}


def _fmt(value: float, scale: str) -> str:
    if scale == "rate3":
        return f"{value:.3f}".lstrip("0") or "0"
    if scale == "pct":
        return f"{value:.0f}%"
    return f"{value:.2f}"  # runs


def tier(metric: str, value: float) -> str | None:
    """Where a value sits on the league scale: elite / above / below average / poor.

    Returns ``None`` for an unknown metric. Direction-aware (lower is better for
    ERA, FIP, chase, K%).
    """
    term = GLOSSARY.get(metric)
    if term is None:
        return None
    midpoint = (term.average + term.elite) / 2
    if term.higher_is_better:
        if value >= term.elite:
            return "elite"
        if value >= midpoint:
            return "above average"
        if value >= term.average:
            return "roughly average"
        return "below average"
    if value <= term.elite:
        return "elite"
    if value <= midpoint:
        return "above average"
    if value <= term.average:
        return "roughly average"
    return "below average"


def explain(metric: str, value: float | None = None) -> str | None:
    """A casual-fan gloss: what the stat means, and where this value ranks.

    With ``value`` omitted, returns just the plain definition. Returns ``None`` for
    an unknown metric so callers can fall back to the raw label.
    """
    term = GLOSSARY.get(metric)
    if term is None:
        return None
    if value is None:
        return f"{term.name} is {term.plain}."
    rank = tier(metric, value)
    avg = _fmt(term.average, term.scale)
    return (
        f"{term.name} is {term.plain}. At {_fmt(value, term.scale)} that's {rank} "
        f"(league average is about {avg})."
    )
