"""Career-baseline change detection — is this different from who he has been?

Every other lens asks "how does he compare to the league." This asks the
question a fan actually asks about a familiar player: *is this a different
player than last year?* The comparison set is the player's own prior seasons.

Two guards make that honest. A player's own history is a small sample — three or
four seasons — so a raw z-score against his personal standard deviation is wildly
unstable; the deviation is therefore judged against **how much players in general
move season to season**, not against his own noise alone. And the league baseline
shifts underneath everyone (the ball, the rules, the pitching), so a raw
career-vs-now delta is partly the era moving; the league's own drift over the
same span is subtracted before anything is claimed.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

from padres_analytics.detect.sql import fmt_name, resolve_table

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Prior seasons required before a career baseline means anything. Two seasons
# give a standard deviation of one degree of freedom — arithmetic, not evidence.
MIN_PRIOR_SEASONS = 3

# Plate appearances required in both the baseline seasons and the current one.
MIN_SEASON_PA = 150

# A move must clear this many cohort-relative standard deviations. Set against
# the spread of *other players'* year-over-year moves, so it answers "is this a
# big change by the standards of how players actually change."
Z_GATE = 1.8

# Players needed in the comparison cohort before its spread means anything.
#
# This gate is currently what keeps the detector silent, and deliberately so:
# ``player_season_batting`` is sourced per-team (Padres only), so the cohort is
# ~20 players a season and only a handful carry enough prior seasons to
# contribute a year-over-year delta. A standard deviation estimated from three
# players is an accident, not a yardstick, and dividing by it would manufacture
# large z-scores out of nothing. It would also compare a Padre against his own
# teammates, which is the self-comparison the league-control rule exists to
# prevent. The detector activates on its own once league-wide season data is
# ingested; until then it reports why it cannot run.
MIN_COHORT = 30

# Metrics worth asking the question about, with their display config.
_CAREER_METRICS: tuple[tuple[str, str, str], ...] = (
    ("ops", "OPS", ".3f"),
    ("obp", "OBP", ".3f"),
    ("slg", "SLG", ".3f"),
)


@dataclass(frozen=True)
class CareerShift:
    """One player's current season measured against his own prior baseline."""

    player_id: int
    player_name: str
    metric: str
    metric_label: str
    value_format: str
    current: float
    baseline: float
    prior_seasons: int
    league_delta: float
    cohort_sd: float
    season: int
    cohort_moves: tuple[float, ...] = ()

    @property
    def raw_delta(self) -> float:
        """Current minus his own baseline."""
        return self.current - self.baseline

    @property
    def net_delta(self) -> float:
        """His move after removing the league's move over the same span."""
        return self.raw_delta - self.league_delta

    @property
    def z(self) -> float:
        """Net move in units of how much players typically move year to year."""
        if self.cohort_sd <= 0:
            return 0.0
        return self.net_delta / self.cohort_sd

    def framing(self) -> str:
        """Pre-verified claim string. States the baseline, never just the delta."""
        direction = "up" if self.net_delta > 0 else "down"
        fmt = self.value_format
        return (
            f"{self.player_name} is {direction} {abs(self.net_delta):{fmt}} in "
            f"{self.metric_label} against his own {self.prior_seasons}-season baseline "
            f"({self.baseline:{fmt}} to {self.current:{fmt}}, {self.season}) — "
            f"{abs(self.z):.1f}x the typical year-to-year move, after adjusting for "
            f"a league that moved {self.league_delta:+{fmt}}"
        )


def _season_rows(
    conn: duckdb.DuckDBPyConnection,
    metric: str,
    season: int,
) -> tuple[dict[int, tuple[str, float]], dict[int, list[float]], float, float, int, list[float]]:
    """Fetch current-season values, per-player priors, and the league's own move.

    Returns:
        ``(current, priors, league_delta, cohort_sd, cohort_n, cohort_deltas)``.
        ``cohort_sd`` is 0.0 when the cohort is too thin to estimate a spread.
    """
    src = resolve_table(conn, "player_season_batting")

    current_rows = conn.execute(
        f"""
        SELECT player_id, ANY_VALUE(player_name), AVG(TRY_CAST({metric} AS DOUBLE))
        FROM {src}
        WHERE season = ? AND pa >= ? AND TRY_CAST({metric} AS DOUBLE) IS NOT NULL
        GROUP BY player_id
        """,
        [season, MIN_SEASON_PA],
    ).fetchall()
    current = {int(r[0]): (fmt_name(str(r[1])), float(r[2])) for r in current_rows}

    prior_rows = conn.execute(
        f"""
        SELECT player_id, season, AVG(TRY_CAST({metric} AS DOUBLE))
        FROM {src}
        WHERE season < ? AND pa >= ? AND TRY_CAST({metric} AS DOUBLE) IS NOT NULL
        GROUP BY player_id, season
        """,
        [season, MIN_SEASON_PA],
    ).fetchall()
    priors: dict[int, list[float]] = {}
    for pid, _yr, val in prior_rows:
        priors.setdefault(int(pid), []).append(float(val))

    # The league moved too. Comparing a player to his own past without removing
    # that drift credits him for the era — the same control the league-drift
    # detector applies to short windows, applied across seasons.
    deltas: list[float] = []
    for pid, (_name, cur) in current.items():
        past = priors.get(pid, [])
        if len(past) >= MIN_PRIOR_SEASONS:
            deltas.append(cur - statistics.fmean(past))

    league_delta = statistics.fmean(deltas) if deltas else 0.0
    # Spread of how much players actually move — the yardstick for "a big move".
    # Requires a real cohort; see MIN_COHORT for why a thin one is worse than none.
    cohort_sd = statistics.pstdev(deltas) if len(deltas) >= MIN_COHORT else 0.0
    return current, priors, league_delta, cohort_sd, len(deltas), deltas


def detect_career_shifts(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    subjects: set[int],
) -> list[CareerShift]:
    """Find players whose season departs from their own multi-year baseline.

    Args:
        conn: Connection with hist attached.
        season: Current season.
        subjects: Player ids to evaluate (already availability-filtered).

    Returns:
        Shifts clearing the z-gate, strongest first.
    """
    out: list[CareerShift] = []

    for metric, label, value_format in _CAREER_METRICS:
        try:
            current, priors, league_delta, cohort_sd, cohort_n, cohort_deltas = _season_rows(
                conn, metric, season
            )
        except Exception as exc:
            # Loud: a fetch failure here is indistinguishable from "no story
            # today" at the call site, and a silent zero is the worst outcome.
            logger.error("changepoint: %s fetch failed, metric skipped: %s", metric, exc)
            continue

        if cohort_sd <= 0:
            logger.info(
                "changepoint: %s skipped — comparison cohort is %d player(s), below the "
                "%d needed to estimate how much players normally move. This table is "
                "sourced per-team, so a league-wide season ingest is what unlocks it.",
                metric,
                cohort_n,
                MIN_COHORT,
            )
            continue

        for pid in subjects:
            if pid not in current:
                continue
            past = priors.get(pid, [])
            if len(past) < MIN_PRIOR_SEASONS:
                continue

            name, cur = current[pid]
            shift = CareerShift(
                player_id=pid,
                player_name=name,
                metric=metric,
                metric_label=label,
                value_format=value_format,
                current=cur,
                baseline=statistics.fmean(past),
                prior_seasons=len(past),
                league_delta=league_delta,
                cohort_sd=cohort_sd,
                season=season,
                cohort_moves=tuple(cohort_deltas),
            )
            if abs(shift.z) >= Z_GATE:
                out.append(shift)

    out.sort(key=lambda s: abs(s.z), reverse=True)
    logger.info("changepoint: %d career shift(s) cleared the gate", len(out))
    return out


def rarity_from_shift(shift: CareerShift) -> float:
    """Rank a shift against how much players actually moved, distribution-free.

    An earlier version mapped z onto rarity with a fixed linear scale. That
    looked conservative and was in fact disabling: it put every shift below
    z=2.8 under the emit floor, so a detector that had just been unblocked could
    still never surface anything. Worse, the number wasn't comparable to any
    other lens, all of which rank against an empirical distribution.

    So this is the same ECDF the rest of the engine uses — the share of the
    cohort whose year-over-year move was smaller than this one — with the cap
    kept, because "different from his own past" remains a weaker claim than
    "unlike anyone in baseball."
    """
    if not shift.cohort_moves:
        return min(0.95, 0.5 + min(abs(shift.z), 4.0) / 8.0)
    magnitudes = [abs(m - shift.league_delta) for m in shift.cohort_moves]
    focal = abs(shift.net_delta)
    beaten = sum(1 for m in magnitudes if m < focal) / len(magnitudes)
    return min(0.95, beaten)
