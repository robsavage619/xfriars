"""Reconcile a rendered story's numbers against the source — verify, don't just show.

The render audit only confirms a number is *drawn*. This module confirms it is
*correct*: every marquee :class:`Stat` is independently re-derived from the
source tables and compared within tolerance, mirroring the tweet pipeline's
Path-A (cross-source) / Path-B (re-run provenance) discipline in
``tweets.verify``.

- **Path B (re-compute):** team aggregates are recomputed in pure Python from the
  per-player rows (a different code path than the detector's SQL), and player
  values are re-read from the authoritative Savant row keyed on ``subject_id`` —
  this catches aggregation bugs, tampering, and wrong-player attribution.
- **Path A (cross-source):** a player's barrel percentile is independently
  re-ranked from the raw exit-velo file and checked against the percentile file.

``verify_angle`` raises on any mismatch so a wrong number can't reach a post.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from padres_analytics.detect.angles import StoryAngle, available_roster_ids, regress

if TYPE_CHECKING:
    import duckdb

# Tolerances.
_TOL_WOBA = 0.002  # wOBA points (stats round to 3 decimals)
_TOL_PCT = 1  # re-read percentile: within 1 (rounding)
_TOL_XRANK = 18  # cross-source percentile re-rank is coarse on a small league sample


class ReconcileError(ValueError):
    """Raised when a rendered number does not match the source within tolerance."""


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _league_xwoba(conn: duckdb.DuckDBPyConnection) -> float | None:
    row = conn.execute(
        "SELECT SUM(est_woba * pa) / SUM(pa) FROM statcast_batting_expected WHERE pa >= 50"
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _reconcile_team(
    conn: duckdb.DuckDBPyConnection, season: int, sm: dict[str, float]
) -> list[str]:
    ids = available_roster_ids(conn)
    if not ids:
        return ["team_luck: no roster to reconcile against"]
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT pa, woba, est_woba FROM statcast_batting_expected "
        f"WHERE player_id IN ({ph}) AND pa >= 50",
        ids,
    ).fetchall()
    if not rows:
        return ["team_luck: no expected-stats rows to reconcile"]
    league = _league_xwoba(conn)
    if league is None:
        return ["team_luck: no league anchor"]

    # Pure-Python re-derivation (independent of the detector's SQL).
    tot_pa = sum(int(pa) for pa, _, _ in rows)
    woba = sum(float(w) * int(pa) for pa, w, _ in rows) / tot_pa
    xwoba = sum(float(x) * int(pa) for pa, _, x in rows) / tot_pa
    true = sum(regress(float(x), int(pa), league) * int(pa) for pa, _, x in rows) / tot_pa

    out: list[str] = []
    for key, calc in (
        ("team_woba", woba),
        ("team_xwoba", xwoba),
        ("true_talent", true),
        ("league_xwoba", league),
    ):
        if key in sm and not _close(round(calc, 3), sm[key], _TOL_WOBA):
            out.append(f"team_luck {key}: card={sm[key]} recomputed={round(calc, 3)}")
    return out


def _reconcile_player(
    conn: duckdb.DuckDBPyConnection, season: int, key: str, pid: int, sm: dict[str, float]
) -> list[str]:
    out: list[str] = []
    if key == "player_luck":
        row = conn.execute(
            "SELECT pa, woba, est_woba FROM statcast_batting_expected "
            "WHERE player_id = ? AND year = ?",
            [pid, season],
        ).fetchone()
        league = _league_xwoba(conn)
        if row is None or row[0] is None or league is None:
            return [f"player_luck: no source row for player {pid}"]
        pa, woba, xwoba = int(row[0]), float(row[1]), float(row[2])
        if "p_woba" in sm and not _close(round(woba, 3), sm["p_woba"], _TOL_WOBA):
            out.append(f"player_luck p_woba: card={sm['p_woba']} source={round(woba, 3)}")
        true = regress(xwoba, pa, league)
        if "p_true" in sm and not _close(round(true, 3), sm["p_true"], _TOL_WOBA):
            out.append(f"player_luck p_true: card={sm['p_true']} recomputed={round(true, 3)}")
        return out

    # approach_outlier / power_outlier: re-read the authoritative percentile.
    col, stat_key = (
        ("chase_percent", "chase_pct") if key == "approach_outlier" else ("brl_percent", "brl_rank")
    )
    row = conn.execute(
        f"SELECT {col} FROM statcast_batter_percentile_ranks WHERE player_id = ? AND year = ?",
        [pid, season],
    ).fetchone()
    if row is None or row[0] is None:
        return [f"{key}: no percentile row for player {pid}"]
    source_pct = round(float(row[0]))
    if stat_key in sm and not _close(source_pct, sm[stat_key], _TOL_PCT):
        out.append(f"{key} {stat_key}: card={sm[stat_key]} source={source_pct}")

    if key == "power_outlier":
        out += _crosscheck_barrel_rank(conn, season, pid, sm.get("brl_rank"))
    return out


def _crosscheck_barrel_rank(
    conn: duckdb.DuckDBPyConnection, season: int, pid: int, asserted: float | None
) -> list[str]:
    """Path A: independently re-rank the player's raw barrel rate vs the league."""
    if asserted is None:
        return []
    me = conn.execute(
        "SELECT brl_percent FROM statcast_batter_exitvelo_barrels "
        "WHERE player_id = ? AND year = ? AND attempts >= 50",
        [pid, season],
    ).fetchone()
    if me is None or me[0] is None:
        return []
    league = [
        float(r[0])
        for r in conn.execute(
            "SELECT brl_percent FROM statcast_batter_exitvelo_barrels "
            "WHERE year = ? AND attempts >= 50 AND brl_percent IS NOT NULL",
            [season],
        ).fetchall()
    ]
    if len(league) < 20:
        return []  # too small a sample to re-rank meaningfully
    below = sum(1 for v in league if v < float(me[0]))
    computed = round(100 * below / len(league))
    if abs(computed - asserted) > _TOL_XRANK:
        return [
            f"power_outlier brl_rank cross-check: card={asserted:.0f} "
            f"re-ranked={computed} (raw exit-velo file vs percentile file)"
        ]
    return []


def reconcile(conn: duckdb.DuckDBPyConnection, angle: StoryAngle) -> list[str]:
    """Re-derive the angle's numbers from source; return human-readable mismatches.

    Args:
        conn: Read connection to padres.db.
        angle: A rendered/ranked story angle.

    Returns:
        Violations (empty means every checked number traces to the source).
    """
    season = angle.as_of.year
    sm = {s.key: s.value for s in angle.stats}
    if angle.key == "team_luck":
        return _reconcile_team(conn, season, sm)
    if angle.key in ("player_luck", "approach_outlier", "power_outlier"):
        if angle.subject_id is None:
            return [f"{angle.key}: no subject_id to reconcile against"]
        return _reconcile_player(conn, season, angle.key, angle.subject_id, sm)
    return []  # live/unofficial cards are not reconciled against season tables


def verify_angle(conn: duckdb.DuckDBPyConnection, angle: StoryAngle) -> None:
    """Reconcile and raise :class:`ReconcileError` on any mismatch (a post gate)."""
    problems = reconcile(conn, angle)
    if problems:
        raise ReconcileError("source reconciliation failed:\n  " + "\n  ".join(problems))
