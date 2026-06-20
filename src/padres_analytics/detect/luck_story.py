"""Compose a 'luck story' — a multi-module analytical infographic on contact vs. results.

The story separates *skill* from *luck* the way the sabermetric canon does:

- Contact quality (xwOBA, exit velocity) is the persistent, skill-bearing signal;
  the wOBA-minus-xwOBA gap and short hot/cold streaks are mostly luck that does
  not repeat (Bradbury 2007, the DIPS argument; Cohen 2019 on small samples).
- "How much is owed" is quantified by regression to the mean with a 220-PA prior
  (Tango/Lichtman/Dolphin, *The Book*, 2007): a batter's true-talent estimate is
  ``(N*observed + 220*league)/(N + 220)``. The 220-PA break-even is where the
  observation starts to outweigh the league prior.

Every number is pulled live from the ingested Statcast/standings tables; the panel
selection and framing are the editorial layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import duckdb

# Tango/Lichtman/Dolphin regression-to-the-mean break-even for wOBA (The Book, 2007).
REGRESSION_PA_PRIOR = 220

# Minimum PA for a hitter to appear in the per-player "owed" panel.
_REGULAR_PA = 100

# Minimum batted-ball events for the contact-quality panel.
_CONTACT_BBE = 80


@dataclass(frozen=True)
class LadderPoint:
    """One marker on the regression number line."""

    label: str
    value: float
    tone: str  # "bad" | "good" | "neutral"


@dataclass(frozen=True)
class LuckStory:
    """Render-ready data for the contact-vs-results infographic.

    All fields are computed from live tables; the renderer adds no numbers.
    """

    season: int
    as_of: date
    # macro hook
    record: tuple[int, int]  # (wins, losses)
    games_back: float
    leader: str
    # team luck gauge
    team_woba: float
    team_xwoba: float
    team_pa: int
    # per-player dumbbell: (short_name, woba, xwoba)
    dumbbell: list[tuple[str, float, float]]
    # volatility sparkline
    daily_avg: list[float]
    spark_span: tuple[str, str]  # (first_date, last_date)
    # contact strip: (short_name, exit_velo)
    contact: list[tuple[str, float]]
    league_ev: float
    # regression counterpoint
    league_woba: float
    league_xwoba: float
    true_talent: float
    ladder: list[LadderPoint] = field(default_factory=list)
    # editorial
    headline: str = ""
    source: str = "Baseball Savant (xwOBA, EV) · MLB Stats API"

    @property
    def luck_gap_pts(self) -> int:
        """Team wOBA minus xwOBA, in points of wOBA (negative = unlucky)."""
        return round((self.team_woba - self.team_xwoba) * 1000)

    @property
    def owed_pts(self) -> int:
        """Regressed true talent minus actual wOBA, in points (positive = owed a bounce)."""
        return round((self.true_talent - self.team_woba) * 1000)


def _short(name: str | None, fallback: str = "") -> str:
    """Last name only, from a 'Last, First' Statcast name."""
    if not name:
        return fallback
    return name.split(",", 1)[0].strip()


def _regress(observed: float, n: int, prior: float) -> float:
    """Regress an observed rate toward a prior using the 220-PA break-even.

    Args:
        observed: The skill signal (here, xwOBA).
        n: Plate appearances behind the observation.
        prior: League-average anchor to regress toward.

    Returns:
        The true-talent estimate.
    """
    return (n * observed + REGRESSION_PA_PRIOR * prior) / (n + REGRESSION_PA_PRIOR)


def build_luck_story(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    *,
    as_of: date | None = None,
    team_name: str = "Padres",
    daily_window: int = 12,
) -> LuckStory | None:
    """Compose the contact-vs-results luck story from live tables.

    Args:
        conn: Read connection to padres.db.
        season: Season year.
        as_of: Card date; defaults to today.
        team_name: Standings team name to anchor the macro hook.
        daily_window: Number of recent game-dates for the volatility sparkline.

    Returns:
        A populated ``LuckStory``, or ``None`` if the expected-stats or standings
        data needed for the gauge and hook isn't ingested.
    """
    as_of = as_of or date.today()

    ids = [r[0] for r in conn.execute("SELECT player_id FROM team_rosters").fetchall()]
    if not ids:
        return None
    placeholders = ",".join("?" * len(ids))

    # League anchors (every hitter in the expected table with a real sample).
    lg = conn.execute(
        """
        SELECT SUM(woba * pa) / SUM(pa), SUM(est_woba * pa) / SUM(pa)
        FROM statcast_batting_expected WHERE pa >= 50
        """
    ).fetchone()
    if lg is None or lg[0] is None:
        return None
    league_woba, league_xwoba = float(lg[0]), float(lg[1])

    # Team gauge — PA-weighted wOBA vs xwOBA across the roster.
    team = conn.execute(
        f"""
        SELECT SUM(woba * pa) / SUM(pa), SUM(est_woba * pa) / SUM(pa), SUM(pa)
        FROM statcast_batting_expected
        WHERE player_id IN ({placeholders}) AND pa >= 50
        """,
        ids,
    ).fetchone()
    if team is None or team[0] is None:
        return None
    team_woba, team_xwoba, team_pa = float(team[0]), float(team[1]), int(team[2])

    # Per-player dumbbell + PA-weighted regressed true talent.
    rows = conn.execute(
        f"""
        SELECT player_name, pa, woba, est_woba
        FROM statcast_batting_expected
        WHERE player_id IN ({placeholders}) AND pa >= {_REGULAR_PA}
        ORDER BY est_woba DESC
        """,
        ids,
    ).fetchall()
    dumbbell: list[tuple[str, float, float]] = []
    owed_num = owed_den = 0.0
    for name, pa, woba, xwoba in rows:
        dumbbell.append((_short(name), float(woba), float(xwoba)))
        true = _regress(float(xwoba), int(pa), league_xwoba)
        owed_num += true * int(pa)
        owed_den += int(pa)
    if not dumbbell:
        return None
    true_talent = owed_num / owed_den

    # Macro hook — record + games back + division leader.
    record, games_back, leader = _standings_hook(conn, season, team_name)

    # Volatility sparkline — team batting average by game date.
    daily = conn.execute(
        f"""
        SELECT game_date, SUM(hits) * 1.0 / NULLIF(SUM(ab), 0) AS avg
        FROM player_game_batting
        WHERE player_id IN ({placeholders}) AND season = ?
        GROUP BY game_date HAVING SUM(ab) > 0 ORDER BY game_date
        """,
        [*ids, season],
    ).fetchall()
    daily = daily[-daily_window:]
    daily_avg = [float(r[1]) for r in daily]
    spark_span = (str(daily[0][0]), str(daily[-1][0])) if daily else ("", "")

    # Contact strip — top exit-velocity bats + league EV anchor.
    league_ev_row = conn.execute(
        "SELECT AVG(avg_hit_speed) FROM statcast_batter_exitvelo_barrels WHERE attempts >= 50"
    ).fetchone()
    league_ev = float(league_ev_row[0]) if league_ev_row and league_ev_row[0] else 88.5
    contact_rows = conn.execute(
        f"""
        SELECT player_name, avg_hit_speed
        FROM statcast_batter_exitvelo_barrels
        WHERE player_id IN ({placeholders}) AND attempts >= {_CONTACT_BBE}
        ORDER BY avg_hit_speed DESC LIMIT 5
        """,
        ids,
    ).fetchall()
    contact = [(_short(n), float(ev)) for n, ev in contact_rows]

    ladder = [
        LadderPoint("actual", round(team_woba, 3), "bad"),
        LadderPoint("true talent", round(true_talent, 3), "good"),
        LadderPoint("league avg", round(league_xwoba, 3), "neutral"),
    ]

    wins, losses = record
    headline = (
        f"{team_name} {wins}-{losses}: the bats are {round((true_talent - team_woba) * 1000):+d} "
        f"points of wOBA unlucky — but regression only lifts them to league average."
    )

    return LuckStory(
        season=season,
        as_of=as_of,
        record=record,
        games_back=games_back,
        leader=leader,
        team_woba=team_woba,
        team_xwoba=team_xwoba,
        team_pa=team_pa,
        dumbbell=dumbbell,
        daily_avg=daily_avg,
        spark_span=spark_span,
        contact=contact,
        league_ev=league_ev,
        league_woba=league_woba,
        league_xwoba=league_xwoba,
        true_talent=true_talent,
        ladder=ladder,
        headline=headline,
    )


def _standings_hook(
    conn: duckdb.DuckDBPyConnection, season: int, team_name: str
) -> tuple[tuple[int, int], float, str]:
    """Return ((wins, losses), games_back, leader_name) from the standings table.

    Falls back to zeros / 'the division' when standings aren't ingested.
    """
    try:
        pad = conn.execute(
            """
            SELECT wins, losses, games_back, division_id
            FROM standings WHERE team_name = ? AND season = ?
            """,
            [team_name, season],
        ).fetchone()
    except duckdb.CatalogException:
        return (0, 0), 0.0, "the division"
    if pad is None:
        return (0, 0), 0.0, "the division"
    wins, losses, gb_raw, division_id = pad
    try:
        gb = float(gb_raw)
    except (TypeError, ValueError):
        gb = 0.0
    leader = conn.execute(
        """
        SELECT team_name FROM standings
        WHERE division_id = ? AND season = ? ORDER BY win_pct DESC LIMIT 1
        """,
        [division_id, season],
    ).fetchone()
    return (int(wins), int(losses)), gb, (leader[0] if leader else "the division")
