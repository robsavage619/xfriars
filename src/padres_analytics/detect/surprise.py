"""Surprise + novelty — rank by what's *unusual for the subject*, not just extreme.

The bare interest score (effect x reliability) treats a career free-swinger's
5th-percentile chase the same as a disciplined hitter suddenly chasing — but only
the second is a story. This layer reweights interest by:

- **Surprise:** how far the subject deviates from *their own* baseline. For a
  player, current-season OPS vs their PA-weighted career OPS (multi-season history
  in ``player_season_batting``). A season in line with their norm is *down*-weighted;
  a genuine departure is boosted. For the team, the luck gap standardized against
  the league's distribution of luck gaps.
- **Novelty:** down-weight a subject we've featured in the last week.

Both degrade gracefully to neutral (1.0) when the baseline data isn't there.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from padres_analytics.detect.angles import StoryAngle

_OPS_FULL_SURPRISE = 0.150  # OPS points from career that earns the full boost
_OPS_NEUTRAL_BAND = 0.030  # within this of career = "normal for them"
_TEAM_FULL_SURPRISE_Z = 2.5  # std devs from the league luck distribution for full boost
_NOVELTY_WINDOW_DAYS = 7


@dataclass(frozen=True)
class Surprise:
    """A ranking multiplier (~0.8-1.5) and the human basis for it."""

    multiplier: float
    note: str


def _f(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):  # OPS is stored like ".796"
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _player_ops_surprise(conn: duckdb.DuckDBPyConnection, pid: int, season: int) -> Surprise | None:
    try:
        rows = conn.execute(
            "SELECT season, pa, ops FROM player_season_batting "
            "WHERE player_id = ? AND ops IS NOT NULL",
            [pid],
        ).fetchall()
    except duckdb.CatalogException:
        return None
    current = next((_f(o) for s, _pa, o in rows if s == season), None)
    prior: list[tuple[int, float]] = []
    for s, pa, o in rows:
        ops = _f(o)
        if s < season and pa and ops is not None:
            prior.append((int(pa), ops))
    total_pa = sum(pa for pa, _ in prior)
    if current is None or not prior or not total_pa:
        return None
    career = sum(pa * o for pa, o in prior) / total_pa
    delta = current - career
    multiplier = 0.8 + min(0.7, abs(delta) / _OPS_FULL_SURPRISE * 0.7)
    career_s = f"{career:.3f}".lstrip("0")
    if delta <= -_OPS_NEUTRAL_BAND:
        note = f"OPS {abs(delta) * 1000:.0f} pts below his {career_s} career — unusual for him"
    elif delta >= _OPS_NEUTRAL_BAND:
        note = f"OPS {delta * 1000:.0f} pts above his {career_s} career — a real step up"
    else:
        note = "in line with his career norm"
    return Surprise(multiplier, note)


def _team_gap_surprise(conn: duckdb.DuckDBPyConnection, sm: dict[str, float]) -> Surprise | None:
    rows = conn.execute(
        "SELECT woba, est_woba FROM statcast_batting_expected WHERE pa >= 50"
    ).fetchall()
    gaps = [float(w) - float(x) for w, x in rows if w is not None and x is not None]
    if len(gaps) < 10 or "team_woba" not in sm or "team_xwoba" not in sm:
        return None
    mean = statistics.mean(gaps)
    sd = statistics.pstdev(gaps) or 0.001
    z = (sm["team_woba"] - sm["team_xwoba"] - mean) / sd
    multiplier = 0.8 + min(0.7, abs(z) / _TEAM_FULL_SURPRISE_Z * 0.7)
    return Surprise(multiplier, f"{abs(z):.1f} SD from the league's luck distribution")


def subject_surprise(conn: duckdb.DuckDBPyConnection, angle: StoryAngle, season: int) -> Surprise:
    """How unusual this story is for its subject (multiplier ~0.8-1.5)."""
    sm = {s.key: s.value for s in angle.stats}
    out: Surprise | None = None
    if angle.key == "team_luck":
        out = _team_gap_surprise(conn, sm)
    elif angle.subject_id is not None:
        out = _player_ops_surprise(conn, angle.subject_id, season)
    return out or Surprise(1.0, "no baseline")


def novelty(conn: duckdb.DuckDBPyConnection, angle: StoryAngle, as_of: date) -> tuple[float, str]:
    """Down-weight a subject featured within the last week (recency dedup)."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM stat_candidates WHERE subject = ? AND as_of >= ?",
            [angle.subject, as_of - timedelta(days=_NOVELTY_WINDOW_DAYS)],
        ).fetchone()
    except duckdb.Error:
        return 1.0, ""
    if row and row[0]:
        return 0.7, "recently featured"
    return 1.0, ""
