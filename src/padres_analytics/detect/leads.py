"""Scout for leads — a broad, ranked net that points Claude at what's worth exploring.

This is deliberately NOT the discovery engine. ``discover`` produces finished,
gated, reconciled *stories* from four lenses. ``scout`` casts a wider net at a
lower bar across more dimensions and emits **leads**: short, ranked observations,
each with a suggested exploration prompt. A lead is raw material — the reasoning
and the actual story stay with Claude Code.

The breadth is the point; the discipline keeps it from being noise — leads only
cover **available** players (no IL bats), require an adequate sample, and are
ranked so the strongest float to the top. The digest seeds a session's context
and feeds the app's "Leads" lane (click a lead -> ask Claude to build it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from padres_analytics.detect.angles import available_roster_ids, reliability

_MIN_PA = 100  # adequate sample before a per-player lead is worth raising
_OPS_LEAD = 0.060  # OPS pts off career to raise a "having an unusual year" lead
_LUCK_LEAD = 0.025  # |wOBA - xwOBA| to raise a luck lead
_TAIL = 12  # percentile within this of either tail is an approach/contact lead


@dataclass(frozen=True)
class Lead:
    """One ranked thing worth exploring — a prompt for Claude, not a finished story."""

    subject: str
    kind: str  # "down_year" | "up_year" | "luck" | "approach" | "contact" | "team"
    headline: str  # the observation, one line
    explore: str  # a suggested exploration prompt for Claude Code
    interest: float


def _f(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _ops_career(
    conn: duckdb.DuckDBPyConnection, pid: int, season: int
) -> tuple[float, float] | None:
    """(current-season OPS, PA-weighted career OPS) or None when history is thin."""
    try:
        rows = conn.execute(
            "SELECT season, pa, ops FROM player_season_batting "
            "WHERE player_id = ? AND ops IS NOT NULL",
            [pid],
        ).fetchall()
    except duckdb.CatalogException:
        return None
    current = next((_f(o) for s, _pa, o in rows if s == season), None)
    prior = [(int(pa), _f(o)) for s, pa, o in rows if s < season and pa and _f(o) is not None]
    total = sum(pa for pa, _ in prior)
    if current is None or not prior or not total:
        return None
    return current, sum(pa * o for pa, o in prior if o is not None) / total


def scout(conn: duckdb.DuckDBPyConnection, season: int, *, as_of: date | None = None) -> list[Lead]:
    """Cast the net: ranked leads across OPS-vs-career, luck, approach, and contact."""
    as_of = as_of or date.today()
    ids = available_roster_ids(conn)
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    leads: list[Lead] = []

    # One pass over the available, adequately-sampled hitters.
    batters = conn.execute(
        f"SELECT player_id, player_name, woba, est_woba, pa FROM statcast_batting_expected "
        f"WHERE player_id IN ({ph}) AND pa >= {_MIN_PA}",
        ids,
    ).fetchall()
    pa_by = {int(b[0]): int(b[4]) for b in batters}

    for pid, pname, woba_raw, xwoba_raw, pa_raw in batters:
        pid, name = int(pid), _short(pname)
        woba, xwoba, pa = float(woba_raw), float(xwoba_raw), int(pa_raw)
        r = reliability(pa)
        gap = woba - xwoba
        if abs(gap) >= _LUCK_LEAD:
            owed = round(gap * 1000)
            leads.append(
                Lead(
                    name,
                    "luck",
                    f"{name}: {'+' if owed > 0 else ''}{owed} pts wOBA vs expected "
                    f"({_avg(woba)} / {_avg(xwoba)})",
                    f"Build a luck story on {name} — is the gap real or noise?",
                    abs(owed) * r,
                )
            )
        oc = _ops_career(conn, pid, season)
        if oc:
            cur, career = oc
            d = cur - career
            if abs(d) >= _OPS_LEAD:
                kind = "down_year" if d < 0 else "up_year"
                leads.append(
                    Lead(
                        name,
                        kind,
                        f"{name}: {cur:.3f} OPS, {abs(d) * 1000:.0f} pts "
                        f"{'below' if d < 0 else 'above'} his {career:.3f} career",
                        f"Why is {name} {'struggling' if d < 0 else 'breaking out'} "
                        f"relative to his norm?",
                        abs(d) * 300,
                    )
                )

    # Percentile extremes (approach + contact).
    try:
        pct_rows = conn.execute(
            f"SELECT player_id, player_name, chase_percent, brl_percent "
            f"FROM statcast_batter_percentile_ranks WHERE player_id IN ({ph}) AND year = ?",
            [*ids, season],
        ).fetchall()
    except duckdb.CatalogException:
        pct_rows = []
    for pid, pname, chase, brl in pct_rows:
        if pid not in pa_by:
            continue
        name = _short(pname)
        for label, val, kind, verb in (
            ("chase", chase, "approach", "chasing" if (chase or 50) <= 50 else "controlling"),
            ("barrel", brl, "contact", "barreling"),
        ):
            if val is None:
                continue
            dist = abs(50 - float(val))
            if dist >= 50 - _TAIL:
                leads.append(
                    Lead(
                        name,
                        kind,
                        f"{name}: {int(val)}th-percentile {label} rate",
                        f"Explore {name}'s {label} — is this {verb} unusual for him?",
                        dist * reliability(pa_by[pid]),
                    )
                )

    # Team luck.
    team = conn.execute(
        f"SELECT SUM(woba*pa)/SUM(pa), SUM(est_woba*pa)/SUM(pa) FROM statcast_batting_expected "
        f"WHERE player_id IN ({ph}) AND pa >= 50",
        ids,
    ).fetchone()
    if team and team[0] is not None:
        gap = round((float(team[0]) - float(team[1])) * 1000)
        if abs(gap) >= 8:
            leads.append(
                Lead(
                    "Padres",
                    "team",
                    f"Team: {'+' if gap > 0 else ''}{gap} pts wOBA vs expected across the lineup",
                    "Build the team luck story / state-of-the-lineup card.",
                    abs(gap) * 1.0,
                )
            )

    return sorted(leads, key=lambda x: x.interest, reverse=True)


def digest(leads: list[Lead], as_of: date) -> str:
    """Render leads as a markdown digest — threads to pull, NOT stories.

    Each line is a flag for Claude to investigate with a deep dive (the
    `xfriars-deep-dive` skill): trends, splits, correlations, cross-validation.
    Never post a lead; post only what survives the dive.
    """
    head = (
        f"# xFriars leads — {as_of}\n\n"
        "_Ranked flags worth a deep dive. **These are starting points, not "
        "stories** — hand each to `xfriars-deep-dive` (trends, splits, "
        "correlations, sample discipline), and post only what survives. Available "
        "(non-IL) players only; sample-gated._\n\n"
    )
    if not leads:
        return head + "_Nothing clears the bar right now._\n"
    lines = [
        f"{i}. **{lead.headline}.**\n   -> dig: {lead.explore}" for i, lead in enumerate(leads, 1)
    ]
    return head + "\n".join(lines) + "\n"


def _short(name: str | None) -> str:
    if not name:
        return ""
    return name.split(",", 1)[0].strip() if ", " in (name or "") else name


def _avg(v: float) -> str:
    return f"{v:.3f}".lstrip("0")
