"""Editorial interest scoring — how much a fan cares, measured in bits.

This is deliberately *not* the statistical-validity layer. ECDF extremeness,
empirical-Bayes shrinkage and Benjamini-Hochberg FDR already answer "is this
claim real." This module answers the separate question "is this claim worth
posting," and the two disagree constantly: a rock-solid finding that Merrill
leads the Padres in swing rate is true and dull.

Why a rewrite rather than more weights on ``scoring.novelty_score``:

  * ``extremeness_lens`` returns None below rarity 0.80, so every surviving
    candidate carries rarity in [0.80, 1.0]. Rarity is doing double duty as a
    gate and as a score, and after gating there is no range left to rank with.
  * ``magnitude`` is, in most detectors, ``min(rarity, 0.95)`` — the same
    variable twice.
  * ``timeliness``, ``rootability`` and ``legibility`` are hardcoded constants
    in every detector, contributing a fixed 0.42-0.64 floor. That floor sits
    above ``min_novelty = 0.25``, so the emission gate is unreachable: a
    candidate with rarity exactly 0.0 still scores 0.42 and is emitted.

The fix is to score in **bits of surprisal** off the candidate's own facts,
which detectors cannot flatter, and to subtract the bits explained by the size
of the search that found the claim. A top-10% mark is 3.3 bits; if you searched
36 family-pairs to find it, 5.2 bits of that is the search, not the player.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Net bits (after the search subtraction) that earn full marks on surprise.
# Calibrated against the three claims the board actually produces:
#   top 10% of 500  -> 3.3 raw - 3.2 search = 0.2 net -> ~0.03  (correctly dull)
#   top 3%  of 200  -> 5.1 raw - 3.2 search = 1.9 net -> ~0.32  (thin on its own)
#   best in MLB     -> 9.0 raw - 3.2 search = 5.8 net -> ~0.97  (a real card)
BITS_FULL = 6.0

# Distinct metric families the conjunction grouper can draw from
# (see detect/conjunction.py::_METRIC_FAMILIES). One member per family, so the
# search space for a k-metric conjunction is C(FAMILY_COUNT, k), not C(metrics, k).
FAMILY_COUNT = 9

# A "first since" gap shorter than this describes a recurring event, not a
# milestone. Three years between Padres hitting a mark means roughly one every
# three years — the framing implies rarity the recurrence interval denies.
MIN_MEANINGFUL_GAP_YEARS = 5

# Below this the claim is not worth a card.
BORING_CUTOFF = 0.35


@dataclass(frozen=True)
class Interest:
    """An interest verdict with the reasoning kept attached."""

    score: float
    surprise_bits: float
    search_bits: float
    components: dict[str, float] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """Coarse editorial band for display and filtering."""
        if self.score >= 0.70:
            return "strong"
        if self.score >= 0.50:
            return "ok"
        if self.score >= BORING_CUTOFF:
            return "thin"
        return "boring"


def _bits(p: float) -> float:
    """Surprisal in bits for a tail probability, guarded against p<=0."""
    return -math.log2(max(p, 1e-9))


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _search_bits_for_conjunction(n_metrics: int) -> float:
    """Bits of the claim explained purely by how many family-pairs were tried.

    A player who is top-10% in two *specific, pre-named* metrics is a 6.6-bit
    claim. A player who is top-10% in *some* two of nine families is not: there
    are C(9,2)=36 ways to be that player, and mining all of them costs 5.2 bits
    of the surprise. Subtracting this is what separates "elite at two things"
    from "we kept looking until two things lined up."

    Args:
        n_metrics: Number of conjunction members (families).

    Returns:
        log2 of the number of family subsets of that size.
    """
    k = max(1, min(n_metrics, FAMILY_COUNT))
    return math.log2(math.comb(FAMILY_COUNT, k))


def _score_conjunction(facts: dict[str, Any]) -> tuple[float, float, list[str]]:
    """Surprisal for a 'top X% in both/all of ...' claim.

    Scores the *empirical* count the engine already computes
    (``players_meeting_all`` out of ``population_size``) rather than the
    geometric mean of member rarities. The geometric mean of two numbers each
    >= 0.80 is >= 0.80 by construction — it is an average of gated values, not
    a joint probability, and it cannot fall low enough to reject anything.
    """
    flags: list[str] = []
    n_metrics = int(facts.get("n_metrics") or 2)
    qualifying = facts.get("players_meeting_all")
    population = facts.get("population_size")

    if qualifying is None or not population:
        # Without a denominator "one of N" is not a verifiable claim, so the
        # conjunction earns nothing on rarity.
        flags.append("no-denominator: conjunction cannot support a uniqueness claim")
        return 0.0, _search_bits_for_conjunction(n_metrics), flags

    q, pop = int(qualifying), int(population)
    raw = _bits(q / pop)
    search = _search_bits_for_conjunction(n_metrics)

    if q > 1:
        share = q / pop
        if share > 0.02:
            flags.append(
                f"crowded: {q} of {pop} players ({share:.1%}) clear the same bar — "
                "'one of N' reads as rare but is not"
            )
    if n_metrics >= 3:
        flags.append(
            f"{n_metrics}-way conjunction: {math.comb(FAMILY_COUNT, n_metrics)} family "
            "combinations were available, so most of the rarity is the search"
        )
    return raw, search, flags


def _score_extremeness(facts: dict[str, Any]) -> tuple[float, float, list[str]]:
    """Surprisal for a single-metric percentile claim."""
    flags: list[str] = []
    # Percentile lands under three different keys depending on the detector:
    # the scanner writes padre_percentile, statcast_profile writes
    # best_percentile, and the weakness detector writes a bare percentile
    # (where low is the point of the card).
    pctl = facts.get("padre_percentile")
    if pctl is None:
        pctl = facts.get("best_percentile")
    if pctl is None and facts.get("percentile") is not None:
        raw_pctl = float(facts["percentile"])
        # A weakness card is a claim about the bottom tail, so its distance from
        # the floor is what makes it notable.
        pctl = max(raw_pctl, 100.0 - raw_pctl)
    population = facts.get("population_size")
    if pctl is None:
        return 0.0, 0.0, ["shape not recognised — surface only, not a verdict"]

    pop = int(population) if population else 200
    tail = max((100.0 - float(pctl)) / 100.0, 1.0 / pop)
    raw = _bits(tail)

    # One metric picked from nine families is a mild search, not a free lunch.
    search = math.log2(FAMILY_COUNT)

    if float(pctl) < 95:
        flags.append(
            f"shallow tail: top {100 - float(pctl):.0f}% is ~{tail * pop:.0f} of {pop} players"
        )
    return raw, search, flags


def _score_contrast(facts: dict[str, Any], headline: str) -> tuple[float, float, list[str]]:
    """Surprisal for a two-context gap claim (in-zone vs out, current vs baseline).

    Contrast claims compare one player against himself in two situations, so the
    pair is pre-specified rather than mined — the search penalty is a single
    family choice, not a subset. These also tend to carry a mechanism, which is
    rewarded separately in :func:`_tension`.
    """
    import re

    flags: list[str] = []
    z = facts.get("z")
    if z is not None:
        # A self-baseline shift scored against the league's own year-to-year
        # spread: how many sigma beyond typical drift.
        raw = _bits(max(1e-9, math.erfc(abs(float(z)) / math.sqrt(2)) / 2))
        if abs(float(z)) < 1.5:
            flags.append(f"within normal drift: z={float(z):.2f}")
        return raw, math.log2(FAMILY_COUNT), flags

    pop = int(facts.get("population_size") or 150)

    # The gap's rank is stated in the headline ("wider than 98% of 350 MLB
    # hitters") but never written to facts, so it has to be read back out of the
    # prose. The scanner should store this as ``gap_percentile``; until it does,
    # treating every gap as maximally rare rated an 86th-percentile split
    # identically to a 98th-percentile one.
    m = re.search(r"wider than (\d+)%", headline)
    if m:
        tail = max((100.0 - float(m.group(1))) / 100.0, 1.0 / pop)
        if float(m.group(1)) < 95:
            flags.append(f"gap is only wider than {m.group(1)}% of peers")
    else:
        flags.append("no stated gap percentile — scored as median")
        tail = 0.5
    return _bits(tail), math.log2(FAMILY_COUNT), flags


def _score_roster_max(facts: dict[str, Any]) -> tuple[float, float, list[str]]:
    """Surprisal for a 'leads the Padres in X' claim.

    On a 26-man roster somebody leads every metric every season. The claim
    carries log2(n) bits of "which Padre" but costs log2(families) bits of
    "which metric" — net, close to nothing.
    """
    n = int(facts.get("n_padres") or 26)
    raw = math.log2(max(n, 2))
    search = math.log2(FAMILY_COUNT)
    return (
        raw,
        search,
        [f"roster-max: someone leads all {n} Padres in this metric every season"],
    )


def _stakes(detector: str, facts: dict[str, Any]) -> tuple[float, list[str]]:
    """Consequence: records, imminent milestones, races, deadlines.

    Stakes are why a low-rarity claim can still be the best card on the board.
    Tatis sitting 0.1 WAR from third all-time is not statistically surprising —
    it is a countdown, and countdowns are the most postable thing the engine
    produces.
    """
    flags: list[str] = []
    s = 0.0

    if facts.get("tier") == "franchise_record" or facts.get("franchise_rank") == 1:
        s = max(s, 0.95)
        flags.append("franchise record")

    # ``gap`` means "distance to a milestone" on chase cards and "percentage
    # points between two contexts" on contrast cards. Only read it as the former
    # when the payload actually carries a milestone, or a 12-point swing-rate
    # split gets scored as an imminent record.
    gap_keys: list[tuple[str, float]] = [("gap_war", 1.0)]
    if facts.get("milestone") is not None or facts.get("club_size") is not None:
        gap_keys.append(("gap", 25.0))

    for gap_key, scale in gap_keys:
        gap = facts.get(gap_key)
        if gap is not None:
            try:
                closeness = _clamp(1.0 - abs(float(gap)) / scale)
            except (TypeError, ValueError):
                continue
            if closeness > 0.5:
                s = max(s, 0.55 + 0.4 * closeness)
                flags.append(f"imminent: {gap_key}={gap}")

    if facts.get("club_size") is not None and int(facts["club_size"]) <= 3:
        s = max(s, 0.75)
        flags.append(f"exclusive club (n={facts['club_size']})")

    if detector == "nl_west_race":
        gb = facts.get("games_back")
        if gb is not None:
            s = max(s, _clamp(1.0 - float(gb) / 15.0))
            flags.append(f"race: {gb} back")

    return s, flags


def _tension(facts: dict[str, Any]) -> tuple[float, list[str]]:
    """Contradiction: the number disagreeing with itself or with the eye test.

    Expected-vs-actual gaps and two-context splits are the shapes that make a
    reader stop, because they contain their own argument. A flat superlative
    does not.
    """
    flags: list[str] = []
    keys = set(facts)

    if any(k.startswith("gap_woba") or k in {"net_delta", "league_delta"} for k in keys):
        flags.append("expected-vs-actual gap")
        return 0.85, flags
    if {"a_value", "b_value"} <= keys:
        flags.append("two-context split")
        return 0.80, flags
    if {"baseline", "current"} <= keys:
        flags.append("self-baseline shift")
        return 0.75, flags
    return 0.0, flags


def _legibility(facts: dict[str, Any], headline: str) -> tuple[float, list[str]]:
    """Can a casual fan parse the claim in one read.

    Computed, not asserted. Every detector currently hardcodes this in
    [0.85, 0.95], which is how a three-way conjunction of swing rate, sprint
    speed and arm strength ends up rated as readable as a home-run record.
    """
    flags: list[str] = []
    score = 1.0

    n_metrics = int(facts.get("n_metrics") or 1)
    if n_metrics >= 2:
        score -= 0.22 * (n_metrics - 1)
        if n_metrics >= 3:
            flags.append("three clauses to hold at once")

    # Each "and" in a headline is another thing the reader must carry.
    ands = headline.lower().count(" and ")
    score -= 0.08 * max(0, ands - 1)

    if len(headline) > 120:
        score -= 0.15
        flags.append("headline over 120 chars")

    return _clamp(score, 0.1, 1.0), flags


def _first_since_penalty(headline: str, year: int | None) -> tuple[float, list[str]]:
    """Discount 'first Padre since X (YYYY)' when the recurrence interval is short."""
    import re

    m = re.search(r"first Padre since .+?\((\d{4})\)", headline)
    if not m or year is None:
        return 1.0, []
    gap = year - int(m.group(1))
    if gap >= MIN_MEANINGFUL_GAP_YEARS:
        return 1.0, []
    return (
        _clamp(gap / MIN_MEANINGFUL_GAP_YEARS, 0.15, 1.0),
        [f"'first since' gap is only {gap}y — that is a recurring event, not a milestone"],
    )


def score_candidate(
    detector: str,
    facts_json: dict[str, Any],
    *,
    unchanged: bool = False,
) -> Interest:
    """Score a candidate's editorial interest from its own stored facts.

    Detector-agnostic by design: it reads the dataset payload rather than
    trusting per-detector self-reported components, so no detector can inflate
    its own ranking.

    Args:
        detector: Emitting detector name.
        facts_json: The stored dataset payload (the outer dict, containing
            ``headline``, ``card_hint`` and an inner ``facts`` dict).
        unchanged: True when the underlying measure has not moved since this
            claim was last emitted. Repeats of a stat that has not moved are
            the single largest source of board clutter.

    Returns:
        An :class:`Interest` carrying the score, the bit decomposition, and the
        flags explaining the verdict.
    """
    inner: dict[str, Any] = facts_json.get("facts") or {}
    headline: str = facts_json.get("headline") or facts_json.get("framing") or ""
    hint: str = facts_json.get("card_hint") or ""
    year = inner.get("metric_year") or inner.get("season")

    flags: list[str] = []

    if hint == "conjunction":
        raw, search, f = _score_conjunction(inner)
    elif hint == "contrast":
        raw, search, f = _score_contrast(inner, headline)
    elif "leads the Padres" in headline or "is the fastest Padre" in headline:
        raw, search, f = _score_roster_max(inner)
    elif any(
        inner.get(k) is not None for k in ("padre_percentile", "best_percentile", "percentile")
    ):
        raw, search, f = _score_extremeness(inner)
    else:
        # No recognised rarity shape. Stakes and tension can still carry the
        # claim, but the surprise term is absent rather than measured — a zero
        # here means "not scored", not "not surprising".
        raw, search, f = 0.0, 0.0, ["no rarity shape — scored on stakes/tension only"]
    flags += f

    net = max(0.0, raw - search)
    surprise = _clamp(net / BITS_FULL)

    stakes, f = _stakes(detector, inner)
    flags += f
    tension, f = _tension(inner)
    flags += f
    legibility, f = _legibility(inner, headline)
    flags += f

    since_mult, f = _first_since_penalty(headline, int(year) if year else None)
    flags += f

    # Surprise, stakes and tension are alternative routes to the same place — a
    # claim needs to be *one* of surprising, consequential or contradictory, not
    # all three. A weighted sum would bury the 0.1-WAR countdown (zero surprise,
    # zero tension) that is the best card on the board.
    #
    # But a pure max discards the others entirely, which rated a 98th-percentile
    # whiff split identically to an 86th-percentile one: both are "a split", and
    # tension alone decided it. The runner-up therefore contributes a discounted
    # share of the headroom the leader leaves behind.
    best, second, _ = sorted((surprise, stakes, tension), reverse=True)
    core = best + (1.0 - best) * 0.35 * second

    # Legibility gates rather than adds: an unreadable card is worth nothing
    # regardless of how deep in the tail it sits.
    score = core * (0.55 + 0.45 * legibility) * since_mult

    if unchanged:
        score *= 0.25
        flags.append("unchanged since last emission")

    return Interest(
        score=_clamp(score),
        surprise_bits=raw,
        search_bits=search,
        components={
            "surprise": round(surprise, 3),
            "stakes": round(stakes, 3),
            "tension": round(tension, 3),
            "legibility": round(legibility, 3),
        },
        flags=flags,
    )


def claim_key(detector: str, facts_json: dict[str, Any]) -> tuple[str, str, str]:
    """Identity of the *claim*, ignoring when it was rendered.

    ``make_candidate_id`` hashes the whole facts payload, and detectors write
    ``as_of`` into that payload along with a date-derived subtitle. The date
    therefore enters the hash by the back door, so an unchanged stat mints a
    fresh id every run: the same "Tatis is 0.1 WAR from passing Machado" landed
    on 2026-06-14 and 2026-07-19 as two separate top-of-board candidates with
    byte-identical baseball facts.

    This key strips the render layer and keeps only the substance, so callers
    can tell a genuinely new claim from the same claim restated tomorrow.

    Args:
        detector: Emitting detector name.
        facts_json: The stored dataset payload.

    Returns:
        A hashable key identifying the underlying claim.
    """
    inner = dict(facts_json.get("facts") or {})
    for volatile in ("as_of", "metric_year", "season", "statcast_year", "bwar_year"):
        inner.pop(volatile, None)
    subject = str(inner.get("player_name") or inner.get("lead_player") or "")
    payload = repr(sorted((k, repr(v)) for k, v in inner.items()))
    return (detector, subject, payload)


def rank_board(
    rows: list[tuple[str, dict[str, Any]]],
    *,
    limit: int = 12,
    max_per_detector: int = 3,
    max_per_subject: int = 2,
) -> list[tuple[str, Interest]]:
    """Rank candidates by interest, collapsing repeats and enforcing variety.

    Scoring alone does not produce a good board. Six "X is N stolen bases from
    150" cards can each be individually strong and collectively unreadable, so
    selection caps how much of the board any one detector or player may own.

    Args:
        rows: ``(candidate_id, facts_json)`` pairs, paired with their detector
            via ``facts_json['_detector']``.
        limit: Maximum candidates to return.
        max_per_detector: Cap on cards from a single detector.
        max_per_subject: Cap on cards about a single player.

    Returns:
        ``(candidate_id, Interest)`` pairs, best first.
    """
    seen_claims: set[tuple[str, str, str]] = set()
    scored: list[tuple[str, Interest, str, str]] = []

    for cid, payload in rows:
        detector = str(payload.get("_detector") or "")
        key = claim_key(detector, payload)
        unchanged = key in seen_claims
        seen_claims.add(key)

        interest = score_candidate(detector, payload, unchanged=unchanged)
        subject = key[1]
        scored.append((cid, interest, detector, subject))

    scored.sort(key=lambda r: r[1].score, reverse=True)

    out: list[tuple[str, Interest]] = []
    per_detector: dict[str, int] = {}
    per_subject: dict[str, int] = {}
    for cid, interest, detector, subject in scored:
        if interest.verdict == "boring":
            continue
        if per_detector.get(detector, 0) >= max_per_detector:
            continue
        if subject and per_subject.get(subject, 0) >= max_per_subject:
            continue
        per_detector[detector] = per_detector.get(detector, 0) + 1
        if subject:
            per_subject[subject] = per_subject.get(subject, 0) + 1
        out.append((cid, interest))
        if len(out) >= limit:
            break
    return out
