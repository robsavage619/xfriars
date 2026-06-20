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


def _context(conn: duckdb.DuckDBPyConnection, season: int, as_of: date) -> _Ctx | None:
    ids = [r[0] for r in conn.execute("SELECT player_id FROM team_rosters").fetchall()]
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


_DETECTORS = (
    detect_team_luck,
    detect_player_luck,
    detect_approach_outlier,
    detect_power_outlier,
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
