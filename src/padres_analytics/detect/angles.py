"""Story-discovery engine: scan the data for the strongest *defensible* angle.

The unit of work is a :class:`StoryAngle` — a candidate narrative with an effect
size, a reliability (how much the sample can be trusted), and direction-aware
copy. Detectors return ``None`` when the signal does not clear a significance
gate, so the engine never manufactures a story out of noise.

Defensibility rests on three things baked in here:

1. **Reliability before assertion.** Every talent claim carries a reliability
   ``r = n / (n + k)`` (Tango/Lichtman/Dolphin, *The Book*: ``k = 220`` PA for
   wOBA). Small samples are surfaced as low confidence and softened, or not at
   all. We never state a claim more confident than the sample supports.
2. **Significance gates.** A divergence must exceed a threshold *and* a minimum
   sample to become a story. A 5-point wOBA wobble over 80 PA is not a story.
3. **A traceable corpus.** Each angle carries every number it asserts as a
   :class:`Stat`; the renderer/verifier checks that nothing reaches the card
   that is not in this corpus (parity with the repo's digit-audit discipline).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from datetime import date

import duckdb

# Regression-to-the-mean break-even for wOBA (The Book, 2007).
REGRESSION_PA_PRIOR = 220

# Significance gates.
_TEAM_GATE_PTS = 8  # min |regressed shift| in points of wOBA for a team story
_PLAYER_GATE_PTS = 22  # min |regressed shift| for an individual luck story
_PLAYER_MIN_PA = 150  # min PA before an individual talent claim is allowed
_APPROACH_EXTREME = 12  # percentile distance from the tails (<=12 or >=88)
_POWER_EXTREME = 88  # percentile at/above which a power signal is "elite"


@dataclass(frozen=True)
class Stat:
    """One asserted number with its provenance — the audit unit.

    Attributes:
        key: Stable identifier (used to bind panels and to audit the SVG).
        value: The number as rendered.
        unit: "woba" | "pts" | "pct" | "mph" | "count" | "record".
        label: Human label.
        n: Sample size behind the number (PA, BBE, games). 0 if not applicable.
        source: Where it came from.
    """

    key: str
    value: float
    unit: str
    label: str
    n: int = 0
    source: str = "Baseball Savant"
    shown: bool = True  # whether this number is rendered on the card (audited if so)


@dataclass(frozen=True)
class PanelSpec:
    """A declarative request for one visual module; the renderer switches on kind."""

    kind: str  # "dumbbell" | "gauge" | "sparkline" | "contact" | "ladder" | "pctbars"
    data: dict[str, object]


@dataclass(frozen=True)
class StoryAngle:
    """A ranked, defensible candidate story."""

    key: str
    subject: str
    title: str  # short display title, e.g. "HIT INTO HARD LUCK"
    headline: str
    thesis: str
    direction: str  # "up" | "down" | "flat"
    effect: float  # raw effect size, detector-native units
    reliability: float  # 0..1, sample sufficiency
    interest: float  # ranking score (effect x reliability x weight)
    confidence: str  # "high" | "moderate" | "low"
    as_of: date
    panels: list[PanelSpec] = field(default_factory=list)
    stats: list[Stat] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    source: str = "Baseball Savant · MLB Stats API"
    headshot: str | None = None  # optional player headshot as a data: URI
    subject_id: int | None = None  # MLBAM id of the subject player, for reconciliation
    rank_note: str = ""  # why it ranked where it did (surprise / novelty basis)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def reliability(n: int, k: int = REGRESSION_PA_PRIOR) -> float:
    """Weight on the observation vs. the league prior: ``n / (n + k)``."""
    return n / (n + k) if n > 0 else 0.0


def confidence_tier(r: float) -> str:
    """Map a reliability to a confidence label."""
    if r >= 0.70:
        return "high"
    if r >= 0.45:
        return "moderate"
    return "low"


def regress(observed: float, n: int, prior: float, k: int = REGRESSION_PA_PRIOR) -> float:
    """Regress an observed rate toward a prior using the 220-PA break-even."""
    return (n * observed + k * prior) / (n + k)


def _short(name: str | None, fallback: str = "") -> str:
    if not name:
        return fallback
    return name.split(",", 1)[0].strip()


def _full(name: str | None, fallback: str = "") -> str:
    if not name:
        return fallback
    if ", " in name:
        last, first = name.split(", ", 1)
        return f"{first} {last}"
    return name


def _pts(woba_delta: float) -> int:
    return round(woba_delta * 1000)


@dataclass(frozen=True)
class _Ctx:
    """Shared inputs handed to every detector (computed once)."""

    conn: duckdb.DuckDBPyConnection
    season: int
    as_of: date
    ids: list[int]
    league_woba: float
    league_xwoba: float
    league_ev: float


def available_roster_ids(conn: duckdb.DuckDBPyConnection) -> list[int]:
    """Roster player ids that are currently AVAILABLE — never feature a player who's out.

    Filters on ``team_rosters.status`` to drop the injured list, minors
    reassignments, etc. (a 60-day-IL bat shouldn't headline a "current" story).
    Degrades to the full roster when the status column isn't present (test fixtures).
    """
    try:
        rows = conn.execute(
            "SELECT player_id FROM team_rosters WHERE status IS NULL OR status ILIKE 'Active'"
        ).fetchall()
    except duckdb.BinderException:
        rows = conn.execute("SELECT player_id FROM team_rosters").fetchall()
    except duckdb.CatalogException:
        return []  # no roster table at all
    return [r[0] for r in rows]


def _context(conn: duckdb.DuckDBPyConnection, season: int, as_of: date) -> _Ctx | None:
    ids = available_roster_ids(conn)
    if not ids:
        return None
    lg = conn.execute(
        """
        SELECT SUM(woba * pa) / SUM(pa), SUM(est_woba * pa) / SUM(pa)
        FROM statcast_batting_expected WHERE pa >= 50
        """
    ).fetchone()
    if lg is None or lg[0] is None:
        return None
    ev_row = conn.execute(
        "SELECT AVG(avg_hit_speed) FROM statcast_batter_exitvelo_barrels WHERE attempts >= 50"
    ).fetchone()
    league_ev = float(ev_row[0]) if ev_row and ev_row[0] else 88.5
    return _Ctx(conn, season, as_of, ids, float(lg[0]), float(lg[1]), league_ev)


# --------------------------------------------------------------------------- #
# detectors  (each: _Ctx -> StoryAngle | None)
# --------------------------------------------------------------------------- #
def detect_team_luck(ctx: _Ctx) -> StoryAngle | None:
    """Team offense out- or under-performing its expected wOBA (direction-aware)."""
    ph = ",".join("?" * len(ctx.ids))
    team = ctx.conn.execute(
        f"""
        SELECT SUM(woba * pa) / SUM(pa), SUM(est_woba * pa) / SUM(pa), SUM(pa)
        FROM statcast_batting_expected WHERE player_id IN ({ph}) AND pa >= 50
        """,
        ctx.ids,
    ).fetchone()
    if team is None or team[0] is None:
        return None
    woba, xwoba, pa = float(team[0]), float(team[1]), int(team[2])

    rows = ctx.conn.execute(
        f"""
        SELECT player_name, pa, woba, est_woba FROM statcast_batting_expected
        WHERE player_id IN ({ph}) AND pa >= 100 ORDER BY est_woba DESC
        """,
        ctx.ids,
    ).fetchall()
    if not rows:
        return None
    dumb = [(_short(n), float(w), float(x)) for n, p, w, x in rows]
    owed_num = sum(regress(float(x), int(p), ctx.league_xwoba) * int(p) for _, p, _, x in rows)
    owed_den = sum(int(p) for _, p, _, _ in rows)
    true_talent = owed_num / owed_den
    owed = _pts(true_talent - woba)
    if abs(owed) < _TEAM_GATE_PTS:
        return None

    r = reliability(pa)
    up = owed > 0  # owed a bounce up
    headline = (
        f"The Padres bats have been {abs(owed)} points of wOBA unlucky, not bad."
        if up
        else f"The Padres are {abs(owed)} points of wOBA ahead of their expected output."
    )
    thesis = (
        f"A .{round(woba * 1000):03d} wOBA against a .{round(xwoba * 1000):03d} "
        "expected mark — the batted-ball quality says better days are coming."
        if up
        else "Results have outrun the contact quality. Some cooling is the honest forecast."
    )
    daily = _daily_avg(ctx)
    stats = [
        Stat("team_woba", round(woba, 3), "woba", "team wOBA", pa, "Baseball Savant"),
        Stat("team_xwoba", round(xwoba, 3), "woba", "team xwOBA", pa, "Baseball Savant"),
        Stat("true_talent", round(true_talent, 3), "woba", "regressed true talent", pa),
        Stat("league_xwoba", round(ctx.league_xwoba, 3), "woba", "league xwOBA", 0),
        Stat("owed", owed, "pts", "points of wOBA owed", pa),
    ]
    panels = [
        PanelSpec("dumbbell", {"rows": dumb, "league_xwoba": ctx.league_xwoba}),
        PanelSpec("gauge", {"woba": woba, "xwoba": xwoba, "pa": pa, "owed": owed}),
        PanelSpec("sparkline", {"values": daily[0], "span": daily[1]}),
        PanelSpec("contact", {"rows": _team_contact(ctx), "league_ev": ctx.league_ev}),
        PanelSpec(
            "ladder",
            {"actual": woba, "true_talent": true_talent, "league": ctx.league_xwoba, "owed": owed},
        ),
    ]
    return StoryAngle(
        key="team_luck",
        subject="Padres offense",
        title="DUE FOR A BOUNCE" if up else "OUTRUNNING THE BAT",
        headline=headline,
        thesis=thesis,
        direction="up" if up else "down",
        effect=abs(owed),
        reliability=r,
        interest=abs(owed) * r,  # points of wOBA, sample-weighted
        confidence=confidence_tier(r),
        as_of=ctx.as_of,
        panels=panels,
        stats=stats,
        caveats=[f"{ctx.season} season, {pa:,} PA through {ctx.as_of}"],
    )


def detect_player_luck(ctx: _Ctx) -> StoryAngle | None:
    """The single biggest individual over/under-performer vs. expected wOBA."""
    ph = ",".join("?" * len(ctx.ids))
    rows = ctx.conn.execute(
        f"""
        SELECT player_id, player_name, pa, woba, est_woba
        FROM statcast_batting_expected
        WHERE player_id IN ({ph}) AND pa >= {_PLAYER_MIN_PA}
        """,
        ctx.ids,
    ).fetchall()
    best: tuple[float, tuple] | None = None
    for pid, name, pa, woba, xwoba in rows:
        true = regress(float(xwoba), int(pa), ctx.league_xwoba)
        owed = _pts(true - float(woba))
        score = abs(owed) * reliability(int(pa))
        if abs(owed) >= _PLAYER_GATE_PTS and (best is None or score > best[0]):
            best = (score, (pid, name, int(pa), float(woba), float(xwoba), true, owed))
    if best is None:
        return None

    _, (pid, name, pa, woba, xwoba, true, owed) = best
    r = reliability(pa)
    up = owed > 0
    full = _full(name)
    headline = (
        f"{abs(owed)} points of wOBA separate {full}'s results from his contact."
        if up
        else f"{full} is outproducing his contact by {abs(owed)} points of wOBA."
    )
    thesis = (
        f"The underlying contact points to a better hitter than the .{round(woba * 1000):03d} "
        "line. Regression should be kind."
        if up
        else "He's beating his expected output. Variance is doing some of the work."
    )
    bars = _player_percentiles(ctx, pid)
    stats = [
        Stat("p_woba", round(woba, 3), "woba", f"{full} wOBA", pa, "Baseball Savant"),
        Stat("p_xwoba", round(xwoba, 3), "woba", f"{full} xwOBA", pa, shown=False),
        Stat("p_true", round(true, 3), "woba", "regressed true talent", pa),
        Stat("p_owed", owed, "pts", "points owed", pa),
    ]
    panels = [
        PanelSpec(
            "ladder",
            {
                "actual": woba,
                "true_talent": true,
                "league": ctx.league_xwoba,
                "owed": owed,
                "subject": full,
            },
        ),
        PanelSpec("pctbars", {"rows": bars, "subject": full}),
    ]
    return StoryAngle(
        key="player_luck",
        subject=full,
        title="BETTER THAN THE LINE" if up else "AHEAD OF THE CONTACT",
        headline=headline,
        thesis=thesis,
        direction="up" if up else "down",
        effect=abs(owed),
        reliability=r,
        interest=abs(owed) * r * 1.05,  # individual stories pop slightly
        confidence=confidence_tier(r),
        as_of=ctx.as_of,
        subject_id=pid,
        panels=panels,
        stats=stats,
        caveats=[f"{pa} PA — {confidence_tier(r)} confidence" if r < 0.7 else f"{pa} PA"],
    )


def detect_approach_outlier(ctx: _Ctx) -> StoryAngle | None:
    """A real (not luck) approach signal: an extreme chase or whiff percentile.

    Unlike the luck angles, this is a *skill* read — a hole or a strength that
    persists. Gated to the tails and to an adequate PA sample.
    """
    ph = ",".join("?" * len(ctx.ids))
    rows = ctx.conn.execute(
        f"""
        SELECT r.player_id, r.player_name, r.chase_percent, r.whiff_percent, e.pa
        FROM statcast_batter_percentile_ranks r
        JOIN statcast_batting_expected e
          ON r.player_id = e.player_id AND r.year = e.year
        WHERE r.player_id IN ({ph}) AND r.year = ? AND e.pa >= {_PLAYER_MIN_PA}
          AND r.chase_percent IS NOT NULL
        """,
        [*ctx.ids, ctx.season],
    ).fetchall()
    best: tuple[float, tuple] | None = None
    for pid, name, chase, whiff, pa in rows:
        # Savant percentile: high = good (low chase). Distance from 50 = extremity.
        dist = abs(50 - float(chase))
        whiff_v = float(whiff) if whiff is not None else 0.0
        if dist >= (50 - _APPROACH_EXTREME) and (best is None or dist > best[0]):
            best = (dist, (pid, name, float(chase), whiff_v, int(pa)))
    if best is None:
        return None

    _, (pid, name, chase, whiff, pa) = best
    full = _full(name)
    weak = chase <= 50  # low percentile = chases a lot = a hole
    r = reliability(pa)
    headline = (
        f"{full} is chasing at a {int(chase)}th-percentile rate — this one's real, not luck."
        if weak
        else f"{full} runs a {int(chase)}th-percentile chase rate."
    )
    thesis = (
        "Pitchers have found the edge of the zone and he's expanding. Fixable, "
        "but it won't regress on its own."
        if weak
        else "Elite plate discipline — a repeatable strength to build the lineup around."
    )
    bars = _player_percentiles(ctx, pid)
    stats = [
        Stat("chase_pct", round(chase), "pct", f"{full} chase percentile", pa, "Baseball Savant"),
        Stat("whiff_pct", round(whiff), "pct", f"{full} whiff pct", pa, shown=False),
    ]
    return StoryAngle(
        key="approach_outlier",
        subject=full,
        title="A REAL HOLE" if weak else "CONTROLS THE ZONE",
        headline=headline,
        thesis=thesis,
        direction="down" if weak else "up",
        effect=abs(50 - chase),
        reliability=r,
        # percentile-distance scaled to ~points so it competes with luck angles
        interest=abs(50 - chase) * 0.6 * r,
        confidence=confidence_tier(r),
        as_of=ctx.as_of,
        subject_id=pid,
        panels=[PanelSpec("pctbars", {"rows": bars, "subject": full})],
        stats=stats,
        caveats=[f"{pa} PA, {ctx.season}"],
    )


def detect_power_outlier(ctx: _Ctx) -> StoryAngle | None:
    """An elite individual contact-quality signal — top-percentile barrels/hard-hit."""
    ph = ",".join("?" * len(ctx.ids))
    row = ctx.conn.execute(
        f"""
        SELECT r.player_id, r.player_name, r.brl_percent, r.hard_hit_percent, e.attempts
        FROM statcast_batter_percentile_ranks r
        JOIN statcast_batter_exitvelo_barrels e
          ON r.player_id = e.player_id AND r.year = e.year
        WHERE r.player_id IN ({ph}) AND r.year = ? AND e.attempts >= 100
          AND r.brl_percent IS NOT NULL
        ORDER BY r.brl_percent DESC LIMIT 1
        """,
        [*ctx.ids, ctx.season],
    ).fetchone()
    if row is None:
        return None
    pid, name, brl_rank, hh_rank, bbe = row
    if float(brl_rank) < _POWER_EXTREME:
        return None  # only an *elite* signal is a story
    full = _full(name)
    r = reliability(int(bbe), k=120)
    bars = _player_percentiles(ctx, pid)
    stats = [
        Stat("brl_rank", round(float(brl_rank)), "pct", f"{full} barrel percentile", int(bbe)),
        Stat("hh_rank", round(float(hh_rank)), "pct", f"{full} hard-hit percentile", int(bbe)),
    ]
    return StoryAngle(
        key="power_outlier",
        subject=full,
        title="ELITE CONTACT",
        headline=f"{full}'s barrel rate sits in the {round(float(brl_rank))}th percentile.",
        thesis=(
            f"Top-shelf batted-ball quality on {int(bbe)} balls — a skill that should sustain."
        ),
        direction="up",
        effect=float(brl_rank),
        reliability=r,
        interest=(float(brl_rank) - 50) * 0.6 * r,
        confidence=confidence_tier(r),
        as_of=ctx.as_of,
        subject_id=int(pid),
        panels=[PanelSpec("pctbars", {"rows": bars, "subject": full})],
        stats=stats,
        caveats=[f"{int(bbe)} batted balls, {ctx.season}"],
    )


# Change-detection gates. A before/after split is the noisiest story we tell, so
# the bar is a *statistical* one: the two windows must be distinguishable, not
# merely different. The split is pre-registered (recent N games vs the prior N) —
# we never search for the split point that maximizes separation, which would
# inflate significance through multiple comparisons (the classic streak fallacy).
_CHANGE_WINDOW_GAMES = 15  # games per window in the fixed, pre-registered split
_CHANGE_MIN_PA_PER_WINDOW = 35  # below this a window can't support a rate claim
_CHANGE_GATE_PTS = 60  # min |OBP swing| in points to be worth telling
_CHANGE_MIN_PREAL = 0.80  # min P(real) from the two-proportion test


def _normal_cdf(z: float) -> float:
    """Standard-normal CDF via the error function (no SciPy dependency)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _two_proportion(reaches1: int, pa1: int, reaches2: int, pa2: int) -> tuple[float, float]:
    """Pooled two-proportion z-test on two on-base rates.

    Returns:
        ``(z, p_real)`` where ``p_real`` is the two-sided confidence that the
        two rates differ (``2·Phi(|z|) - 1``). ``(0.0, 0.0)`` when a window is
        empty or the pooled rate is degenerate.
    """
    if pa1 <= 0 or pa2 <= 0:
        return 0.0, 0.0
    p1, p2 = reaches1 / pa1, reaches2 / pa2
    pooled = (reaches1 + reaches2) / (pa1 + pa2)
    se = math.sqrt(pooled * (1.0 - pooled) * (1.0 / pa1 + 1.0 / pa2))
    if se == 0.0:
        return 0.0, 0.0
    z = (p2 - p1) / se
    return z, 2.0 * _normal_cdf(abs(z)) - 1.0


def _change_windows(
    ctx: _Ctx, pid: int
) -> tuple[tuple[int, int], tuple[int, int], list[float], tuple[str, str]] | None:
    """Split a batter's game log into a pre-registered prior/recent window pair.

    Returns ``((reaches_prior, pa_prior), (reaches_recent, pa_recent), obp_series,
    (split_date, last_date))`` or ``None`` when there aren't two full windows.
    On-base reaches use ``H + BB + HBP`` over ``AB + BB + HBP`` (SF/SH absent from
    the game log, so this is OBP-class, not exact OBP — surfaced as a caveat).
    """
    rows = ctx.conn.execute(
        """
        SELECT game_date, ab, hits, bb, hbp FROM player_game_batting
        WHERE player_id = ? AND season = ? AND (ab + bb + hbp) > 0
        ORDER BY game_date
        """,
        [pid, ctx.season],
    ).fetchall()
    if len(rows) < 2 * _CHANGE_WINDOW_GAMES:
        return None
    prior, recent = (
        rows[-2 * _CHANGE_WINDOW_GAMES : -_CHANGE_WINDOW_GAMES],
        rows[-_CHANGE_WINDOW_GAMES:],
    )

    def _tally(window: list) -> tuple[int, int, list[float]]:
        reaches = pa = 0
        series: list[float] = []
        for _, ab, hits, bb, hbp in window:
            r, p = hits + bb + hbp, ab + bb + hbp
            reaches += r
            pa += p
            series.append(r / p if p else 0.0)
        return reaches, pa, series

    r0, pa0, s0 = _tally(prior)
    r1, pa1, s1 = _tally(recent)
    if min(pa0, pa1) < _CHANGE_MIN_PA_PER_WINDOW:
        return None
    span = (str(recent[0][0]), str(recent[-1][0]))
    return (r0, pa0), (r1, pa1), s0 + s1, span


def detect_change(ctx: _Ctx) -> StoryAngle | None:
    """A batter whose recent on-base results break from his prior form.

    The honest claim is "results have changed," not "talent has changed": OBP over
    a dozen games is far short of the ~460-PA OBP stabilization point, so the story
    is gated on a two-proportion test (the windows must be statistically separable)
    and framed and caveated as a results split, never a verdict on true talent.
    """
    best: tuple[float, tuple] | None = None
    for pid, name in zip(ctx.ids, _names(ctx), strict=False):
        win = _change_windows(ctx, pid)
        if win is None:
            continue
        (r0, pa0), (r1, pa1), series, span = win
        obp0, obp1 = r0 / pa0, r1 / pa1
        delta = _pts(obp1 - obp0)
        _, p_real = _two_proportion(r0, pa0, r1, pa1)
        if abs(delta) < _CHANGE_GATE_PTS or p_real < _CHANGE_MIN_PREAL:
            continue
        score = abs(delta) * p_real
        if best is None or score > best[0]:
            best = (score, (pid, name, obp0, obp1, delta, p_real, pa0, pa1, series, span))
    if best is None:
        return None

    _, (pid, name, obp0, obp1, delta, p_real, pa0, pa1, series, span) = best
    up = delta > 0
    full = _full(name)
    headline = (
        f"{full} has flipped a switch: {abs(delta)} points of on-base over his recent stretch."
        if up
        else f"{full} has cooled hard — {abs(delta)} points of on-base off his prior form."
    )
    swung = "stepped up" if up else "fallen off"
    thesis = (
        f"The on-base line has {swung} sharply over his recent games — a split the "
        "two-window test calls real. Whether it's a new level or variance needs more games."
    )
    # prior/recent/P(real) are provenance until the dedicated before/after panel
    # draws them; the headline delta is the one shown, audited claim for now.
    stats = [
        Stat(
            "chg_prior",
            round(obp0, 3),
            "woba",
            "prior-window OBP",
            pa0,
            "MLB Stats API",
            shown=False,
        ),
        Stat(
            "chg_recent",
            round(obp1, 3),
            "woba",
            "recent-window OBP",
            pa1,
            "MLB Stats API",
            shown=False,
        ),
        Stat("chg_delta", abs(delta), "pts", "points of OBP change", pa0 + pa1),
        Stat(
            "chg_preal",
            round(p_real * 100),
            "pct",
            "confidence the split is real",
            pa0 + pa1,
            shown=False,
        ),
    ]
    panels = [PanelSpec("sparkline", {"values": series, "span": span})]
    return StoryAngle(
        key="change",
        subject=full,
        title="FLIPPED A SWITCH" if up else "HIT A WALL",
        headline=headline,
        thesis=thesis,
        direction="up" if up else "down",
        effect=abs(delta),
        reliability=p_real,
        interest=abs(delta) * p_real,
        confidence=confidence_tier(p_real),
        as_of=ctx.as_of,
        subject_id=pid,
        panels=panels,
        stats=stats,
        caveats=[
            f"{_CHANGE_WINDOW_GAMES}-game windows, {pa0 + pa1} PA — a results split, "
            "not a talent verdict",
            "OBP-class rate (SF/SH not in the game log); recent through " + span[1],
        ],
    )


# Pitcher luck gates. FIP (ERA scaled to the league via the season constant)
# strips out everything but K/BB/HBP/HR, so a wide ERA-minus-FIP gap is the pitching
# analogue of the hitter's wOBA-minus-xwOBA luck signal. Symmetric to detect_player_luck.
_PITCHER_MIN_OUTS = 90  # 30 IP floor — below this the estimator is too noisy to tell
_PITCHER_GATE_RUNS = 0.50  # min |ERA - FIP| in runs to be worth telling
_PITCHER_BF_PRIOR = 300  # batters-faced prior for the reliability weight on the gap


def detect_pitcher_luck(ctx: _Ctx) -> StoryAngle | None:
    """The Padres pitcher whose ERA most diverges from his FIP (luck, not skill).

    FIP = ``(13*HR + 3*(BB+HBP) - 2*K)/IP + C`` with ``C`` the league constant
    that scales FIP onto the ERA baseline (read from ``league_pitching_constants``,
    derived from real league totals — never hardcoded). ERA above FIP = unlucky
    (bound to improve); below = outrunning the peripherals.
    """
    from padres_analytics.ingest.mlb_api import innings_to_outs

    const_row = ctx.conn.execute(
        "SELECT fip_const FROM league_pitching_constants WHERE season = ?", [ctx.season]
    ).fetchone()
    if const_row is None or const_row[0] is None:
        return None
    const = float(const_row[0])
    ph = ",".join("?" * len(ctx.ids))
    rows = ctx.conn.execute(
        f"""
        SELECT player_id, player_name, ip, era, so, bb, hr, hbp, tbf
        FROM player_season_pitching
        WHERE player_id IN ({ph}) AND season = ? AND hr IS NOT NULL
        """,
        [*ctx.ids, ctx.season],
    ).fetchall()
    best: tuple[float, tuple] | None = None
    for pid, name, ip, era, so, bb, hr, hbp, tbf in rows:
        outs = innings_to_outs(str(ip))
        if outs < _PITCHER_MIN_OUTS or era in (None, ""):
            continue
        innings = outs / 3.0
        fip = (13 * (hr or 0) + 3 * ((bb or 0) + (hbp or 0)) - 2 * (so or 0)) / innings + const
        gap = float(era) - fip  # positive = unlucky (ERA should fall toward FIP)
        bf = int(tbf or 0)
        r = reliability(bf, k=_PITCHER_BF_PRIOR)
        score = abs(gap) * r
        if abs(gap) >= _PITCHER_GATE_RUNS and (best is None or score > best[0]):
            best = (score, (pid, name, float(era), fip, gap, bf))
    if best is None:
        return None

    _, (pid, name, era, fip, gap, bf) = best
    unlucky = gap > 0
    full = _full(name)
    r = reliability(bf, k=_PITCHER_BF_PRIOR)
    headline = (
        f"{full}'s {era:.2f} ERA hides a {fip:.2f} FIP - {abs(gap):.2f} runs of hard luck."
        if unlucky
        else f"{full}'s {era:.2f} ERA is outrunning a {fip:.2f} FIP by {abs(gap):.2f} runs."
    )
    thesis = (
        "Strip out the balls in play and the strikeouts, walks and homers say a "
        "better pitcher than the ERA. Regression should help."
        if unlucky
        else "The peripherals haven't earned the ERA yet — some give-back is the honest call."
    )
    stats = [
        Stat("pit_era", round(era, 2), "count", f"{full} ERA", bf, "MLB Stats API"),
        Stat("pit_fip", round(fip, 2), "count", f"{full} FIP", bf, "MLB Stats API"),
        Stat("pit_gap", round(abs(gap), 2), "count", "runs of ERA-minus-FIP gap", bf),
        Stat("pit_const", round(const, 2), "count", "league FIP constant", 0, shown=False),
    ]
    return StoryAngle(
        key="pitcher_luck",
        subject=full,
        title="HARD LUCK ON THE MOUND" if unlucky else "OUTRUNNING THE ARM",
        headline=headline,
        thesis=thesis,
        direction="up" if unlucky else "down",
        effect=abs(gap),
        reliability=r,
        interest=abs(gap) * 100 * r,  # runs scaled to sit alongside points-of-wOBA stories
        confidence=confidence_tier(r),
        as_of=ctx.as_of,
        subject_id=pid,
        panels=[PanelSpec("ladder", {"actual": era, "true_talent": fip, "league": fip, "owed": 0})],
        stats=stats,
        caveats=[
            f"{bf} batters faced, {ctx.season} — FIP on the league ERA scale (C={const:.2f})",
            "FIP credits only K/BB/HBP/HR; batted-ball luck and defense live in the gap",
        ],
    )


# League-control gates. The differentiator: a player's change is only *his* if it
# clears league-wide drift over the same calendar window. We control subject Δ
# against a NON-team cohort's Δ (per feedback_league_control_causation) and ask
# whether the residual is large versus normal player-to-player drift (its spread).
_LEAGUE_CTRL_MIN_PA = 30  # PA floor per window, for both the subject and cohort members
_LEAGUE_CTRL_Z_GATE = 1.5  # |residual / cohort drift SD| to clear (~0.87 two-sided)
_LEAGUE_CTRL_MIN_PTS = 40  # min |controlled change| in OBP points to be worth telling


def _obp_class(reaches: int, pa: int) -> float | None:
    return reaches / pa if pa else None


def _cohort_drift(
    ctx: _Ctx,
) -> tuple[float, float, int, tuple[tuple[str, str], tuple[str, str]]] | None:
    """Mean and SD of the non-team cohort's window-over-window OBP drift.

    Reads the two stored calendar windows from ``league_window_batting``, keeps
    league hitters who are NOT Padres and cleared the PA floor in *both* windows,
    and returns ``(mean_delta, sd_delta, n_cohort, (prior_dates, recent_dates))``
    — the secular drift to subtract and the spread to judge a residual against.
    """
    rows = ctx.conn.execute(
        """
        SELECT window_label, start_date, end_date, player_id, ab, hits, bb, hbp
        FROM league_window_batting WHERE season = ?
        """,
        [ctx.season],
    ).fetchall()
    if not rows:
        return None
    team = set(ctx.ids)
    prior: dict[int, float] = {}
    recent: dict[int, float] = {}
    bounds: dict[str, tuple[str, str]] = {}
    for win, start, end, pid, ab, hits, bb, hbp in rows:
        bounds[win] = (str(start), str(end))
        if int(pid) in team:
            continue
        pa = (ab or 0) + (bb or 0) + (hbp or 0)
        if pa < _LEAGUE_CTRL_MIN_PA:
            continue
        obp = _obp_class((hits or 0) + (bb or 0) + (hbp or 0), pa)
        if obp is not None:
            (prior if win == "prior" else recent)[int(pid)] = obp
    deltas = [recent[pid] - prior[pid] for pid in prior.keys() & recent.keys()]
    if len(deltas) < 20 or "prior" not in bounds or "recent" not in bounds:
        return None
    mean = sum(deltas) / len(deltas)
    var = sum((d - mean) ** 2 for d in deltas) / (len(deltas) - 1)
    sd = math.sqrt(var)
    if sd == 0.0:
        return None
    return mean, sd, len(deltas), (bounds["prior"], bounds["recent"])


def _subject_window_obp(ctx: _Ctx, pid: int, start: str, end: str) -> tuple[float | None, int]:
    """A Padres batter's OBP-class rate over a calendar window (inclusive)."""
    row = ctx.conn.execute(
        """
        SELECT SUM(hits + bb + hbp), SUM(ab + bb + hbp)
        FROM player_game_batting
        WHERE player_id = ? AND season = ? AND game_date BETWEEN ? AND ?
        """,
        [pid, ctx.season, start, end],
    ).fetchone()
    if row is None or row[1] is None:
        return None, 0
    pa = int(row[1])
    return _obp_class(int(row[0]), pa), pa


def detect_league_control(ctx: _Ctx) -> StoryAngle | None:
    """The Padre whose change is most *his own* once league drift is removed.

    Controls each hitter's window-over-window OBP change against a non-team league
    cohort's drift over the same calendar dates, then asks whether the residual is
    large versus normal player-to-player variation. This separates a real
    individual swing from "the whole league is hot/cold right now".
    """
    drift = _cohort_drift(ctx)
    if drift is None:
        return None
    lg_mean, lg_sd, n_cohort, ((p_start, p_end), (r_start, r_end)) = drift
    best: tuple[float, tuple] | None = None
    for pid, name in zip(ctx.ids, _names(ctx), strict=False):
        obp0, pa0 = _subject_window_obp(ctx, pid, p_start, p_end)
        obp1, pa1 = _subject_window_obp(ctx, pid, r_start, r_end)
        if obp0 is None or obp1 is None or min(pa0, pa1) < _LEAGUE_CTRL_MIN_PA:
            continue
        residual = (obp1 - obp0) - lg_mean  # change net of league drift
        z = residual / lg_sd
        if abs(_pts(residual)) < _LEAGUE_CTRL_MIN_PTS or abs(z) < _LEAGUE_CTRL_Z_GATE:
            continue
        score = abs(z)
        if best is None or score > best[0]:
            best = (score, (pid, name, obp0, obp1, residual, z, pa0 + pa1))
    if best is None:
        return None

    _, (pid, name, obp0, obp1, residual, z, pa) = best
    up = residual > 0
    full = _full(name)
    res_pts = abs(_pts(residual))
    raw_pts = abs(_pts(obp1 - obp0))
    lg_pts = _pts(lg_mean)
    r = 2.0 * _normal_cdf(abs(z)) - 1.0  # confidence the residual is real
    headline = (
        f"Even with the league hitting, {full} is {res_pts} points of on-base above the field."
        if up
        else f"This isn't the league cooling: {full} is {res_pts} points of on-base below it."
    )
    thesis = (
        f"His on-base moved {raw_pts} points while the league drifted {lg_pts:+d}. "
        f"Net of that, the swing is {abs(z):.1f} SDs past normal player-to-player drift — it's him."
    )
    stats = [
        Stat("lc_residual", res_pts, "pts", "points net of league drift", pa),
        Stat("lc_raw", raw_pts, "pts", "raw OBP change", pa, shown=False),
        Stat("lc_league", abs(lg_pts), "pts", "league drift", n_cohort, shown=False),
        Stat("lc_z", round(abs(z), 1), "count", "SDs past cohort drift", pa, shown=False),
    ]
    return StoryAngle(
        key="league_control",
        subject=full,
        title="NOT THE LEAGUE — HIM" if up else "NOT THE LEAGUE, JUST HIM",
        headline=headline,
        thesis=thesis,
        direction="up" if up else "down",
        effect=res_pts,
        reliability=r,
        interest=res_pts * r * 1.1,  # the causal-control angle is the differentiator, boosted
        confidence=confidence_tier(r),
        as_of=ctx.as_of,
        subject_id=pid,
        panels=[
            PanelSpec(
                "ladder",
                {
                    "actual": obp1,
                    "true_talent": obp0,
                    "league": obp0 + lg_mean,
                    "owed": _pts(residual),
                },
            )
        ],
        stats=stats,
        caveats=[
            f"{r_start}..{r_end} vs {p_start}..{p_end}; "
            f"controlled vs {n_cohort} non-Padres hitters",
            "OBP-class rate; calendar-matched windows; a results swing, not a talent verdict",
        ],
    )


def _names(ctx: _Ctx) -> list[str]:
    """Roster player names aligned to ``ctx.ids`` order (best-effort)."""
    ph = ",".join("?" * len(ctx.ids))
    rows = ctx.conn.execute(
        f"SELECT player_id, player_name FROM team_rosters WHERE player_id IN ({ph})",
        ctx.ids,
    ).fetchall()
    lookup = {int(pid): name for pid, name in rows}
    return [lookup.get(int(pid), "") for pid in ctx.ids]


_DETECTORS = (
    detect_team_luck,
    detect_player_luck,
    detect_approach_outlier,
    detect_power_outlier,
    detect_change,
    detect_pitcher_luck,
    detect_league_control,
)


# --------------------------------------------------------------------------- #
# shared data pulls
# --------------------------------------------------------------------------- #
def _daily_avg(ctx: _Ctx, window: int = 12) -> tuple[list[float], tuple[str, str]]:
    ph = ",".join("?" * len(ctx.ids))
    rows = ctx.conn.execute(
        f"""
        SELECT game_date, SUM(hits) * 1.0 / NULLIF(SUM(ab), 0)
        FROM player_game_batting WHERE player_id IN ({ph}) AND season = ?
        GROUP BY game_date HAVING SUM(ab) > 0 ORDER BY game_date
        """,
        [*ctx.ids, ctx.season],
    ).fetchall()
    rows = rows[-window:]
    vals = [float(r[1]) for r in rows]
    span = (str(rows[0][0]), str(rows[-1][0])) if rows else ("", "")
    return vals, span


def _team_contact(ctx: _Ctx) -> list[tuple[str, float]]:
    ph = ",".join("?" * len(ctx.ids))
    rows = ctx.conn.execute(
        f"""
        SELECT player_name, avg_hit_speed FROM statcast_batter_exitvelo_barrels
        WHERE player_id IN ({ph}) AND attempts >= 80 ORDER BY avg_hit_speed DESC LIMIT 5
        """,
        ctx.ids,
    ).fetchall()
    return [(_short(n), float(ev)) for n, ev in rows]


def _player_percentiles(ctx: _Ctx, pid: int) -> list[tuple[str, int]]:
    row = ctx.conn.execute(
        """
        SELECT xwoba, hard_hit_percent, brl_percent, chase_percent, k_percent
        FROM statcast_batter_percentile_ranks WHERE player_id = ? AND year = ?
        """,
        [pid, ctx.season],
    ).fetchone()
    if row is None:
        return []
    labels = ["xwOBA", "Hard-Hit%", "Barrel%", "Chase%", "K%"]
    return [(lab, round(float(v))) for lab, v in zip(labels, row, strict=True) if v is not None]


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def discover(
    conn: duckdb.DuckDBPyConnection, season: int, *, as_of: date | None = None
) -> list[StoryAngle]:
    """Run every detector and return surviving angles, best-first.

    Args:
        conn: Read connection to padres.db.
        season: Season year.
        as_of: Card date; defaults to today.

    Returns:
        Angles that cleared their significance gates, sorted by interest
        descending. Empty when nothing rises above noise.
    """
    ctx = _context(conn, season, as_of or date.today())
    if ctx is None:
        return []
    found: list[StoryAngle] = []
    for detector in _DETECTORS:
        try:
            angle = detector(ctx)
        except duckdb.Error:
            continue  # a missing table for one lens never kills the rest
        if angle is not None:
            found.append(angle)
    return _rerank(conn, found, ctx)


def _rerank(
    conn: duckdb.DuckDBPyConnection, angles: list[StoryAngle], ctx: _Ctx
) -> list[StoryAngle]:
    """Reweight raw interest by surprise (unusual for the subject) + novelty."""
    from padres_analytics.detect.surprise import novelty, subject_surprise

    ranked: list[StoryAngle] = []
    for angle in angles:
        surprise = subject_surprise(conn, angle, ctx.season)
        nov_mult, nov_note = novelty(conn, angle, ctx.as_of)
        note = " · ".join(n for n in (surprise.note, nov_note) if n)
        ranked.append(
            replace(angle, interest=angle.interest * surprise.multiplier * nov_mult, rank_note=note)
        )
    return sorted(ranked, key=lambda a: a.interest, reverse=True)


def best_story(
    conn: duckdb.DuckDBPyConnection, season: int, *, as_of: date | None = None
) -> StoryAngle | None:
    """The single strongest defensible angle, or ``None`` if nothing clears the gates."""
    angles = discover(conn, season, as_of=as_of)
    return angles[0] if angles else None


def _stat_tokens(st: Stat) -> set[str]:
    """The string forms a stat's value can legitimately appear as."""
    toks = {str(round(st.value))}
    if st.unit == "woba":
        toks.add(f"{st.value:.3f}".lstrip("0"))
    toks.add(f"{st.value:.1f}")
    toks.add(f"{st.value:.2f}")  # ERA/FIP and other rate stats render to 2 places
    return toks


def audit_angle(angle: StoryAngle) -> list[str]:
    """Self-consistency audit on the corpus, before rendering.

    Guards credibility independent of layout:

    1. Confidence label matches the reliability it was derived from.
    2. Reliability is a probability; effect is finite.
    3. Every number in the headline is backed by a :class:`Stat` in the corpus.
    4. The corpus and a coverage caveat are non-empty.

    Returns:
        Human-readable violations; empty means the angle is internally sound.
    """
    out: list[str] = []
    if angle.confidence != confidence_tier(angle.reliability):
        out.append(f"confidence {angle.confidence!r} != tier({angle.reliability:.2f})")
    if not 0.0 <= angle.reliability <= 1.0:
        out.append(f"reliability {angle.reliability} out of range")
    if not angle.stats:
        out.append("empty stat corpus")
    if not angle.caveats:
        out.append("no coverage caveat")
    backed = {tok for st in angle.stats for tok in _stat_tokens(st)}
    for num in re.findall(r"\d+\.?\d*", angle.headline):
        if num not in backed:
            out.append(f"headline number {num!r} not backed by any stat")
    return out
