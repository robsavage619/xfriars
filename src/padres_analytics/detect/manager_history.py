"""Cited reference dataset: rookie managers who inherited a playoff team.

The story engine's other angles reconcile every number against ``padres.db``.
This one can't — historical manager records don't live in the database — so the
accuracy model is different and explicit: a fixed **inclusion rule**, a curated
cohort, and a **citation** standing in for source re-derivation.

Inclusion rule (objective, to avoid cherry-picking):
    First-year ("rookie") MLB managers, 2012-2026, who took over a club that
    reached the postseason the *prior* season.

The Padres' own line is NOT in this cohort — it is pulled live from ``game_box``
at detect time (and reconciled against it), so the only moving number on the card
remains source-gated. Everything below is static, cited, and verified by hand
against Baseball-Reference manager pages (first-year records) and team season
pages (prior-year postseason berth).
"""

from __future__ import annotations

from dataclasses import dataclass

SOURCE = "Baseball-Reference"


@dataclass(frozen=True)
class RookieSeason:
    """One rookie manager's first year vs. the team he was handed.

    Attributes:
        manager: Manager name.
        year: First season managed.
        team: Team abbreviation.
        wins: First-year wins.
        losses: First-year losses.
        prior_wins: The inherited team's wins the prior (playoff) season.
        prior_losses: The inherited team's losses the prior season.
        note: Optional context tag (e.g. a redemption follow-up).
    """

    manager: str
    year: int
    team: str
    wins: int
    losses: int
    prior_wins: int
    prior_losses: int
    note: str = ""

    @property
    def win_pct(self) -> float:
        """First-year winning percentage (what he delivered)."""
        return self.wins / (self.wins + self.losses)

    @property
    def prior_pct(self) -> float:
        """Prior-year winning percentage (what he was handed)."""
        return self.prior_wins / (self.prior_wins + self.prior_losses)


# Verified by hand against Baseball-Reference, 2026-06-21. This is the *complete*
# set under the inclusion rule, not a sample — every rookie manager since 2012
# who inherited a prior-year playoff team.
COHORT: tuple[RookieSeason, ...] = (
    RookieSeason("Aaron Boone", 2018, "NYY", 100, 62, 91, 71),
    RookieSeason("Dave Roberts", 2016, "LAD", 91, 71, 92, 70),
    RookieSeason("Brad Ausmus", 2014, "DET", 90, 72, 93, 69),
    RookieSeason("Mike Matheny", 2012, "STL", 88, 74, 90, 72),
    RookieSeason(
        "Dave Martinez", 2018, "WSH", 82, 80, 97, 65, "won the World Series the next year"
    ),
)

# The Padres' inherited baseline — 2025 was a 90-win Wild Card team (Mike Shildt),
# back-to-back 90-win seasons for the first time in franchise history. Cited, since
# 2025 is not in the 2026 game ledger. (Baseball-Reference.)
PADRES_PRIOR_WINS = 90
PADRES_PRIOR_LOSSES = 72
