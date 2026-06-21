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
_TOL_RUNS = 0.02  # ERA/FIP round to 2 decimals
_TOL_PTS = 1  # integer points-of-rate deltas


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


def _reconcile_pitcher(
    conn: duckdb.DuckDBPyConnection, season: int, pid: int, sm: dict[str, float]
) -> list[str]:
    """Independently recompute ERA (from the season row) and FIP (from the formula)."""
    from padres_analytics.ingest.mlb_api import innings_to_outs

    row = conn.execute(
        "SELECT ip, era, so, bb, hr, hbp FROM player_season_pitching "
        "WHERE player_id = ? AND season = ?",
        [pid, season],
    ).fetchone()
    const_row = conn.execute(
        "SELECT fip_const FROM league_pitching_constants WHERE season = ?", [season]
    ).fetchone()
    if row is None or row[1] in (None, "") or const_row is None or const_row[0] is None:
        return [f"pitcher_luck: no source row/constant for player {pid}"]
    ip, era, so, bb, hr, hbp = row
    outs = innings_to_outs(str(ip))
    if outs == 0:
        return [f"pitcher_luck: zero innings for player {pid}"]
    innings = outs / 3.0
    fip = (13 * (hr or 0) + 3 * ((bb or 0) + (hbp or 0)) - 2 * (so or 0)) / innings + float(
        const_row[0]
    )
    out: list[str] = []
    if "pit_era" in sm and not _close(round(float(era), 2), sm["pit_era"], _TOL_RUNS):
        out.append(f"pitcher_luck pit_era: card={sm['pit_era']} source={round(float(era), 2)}")
    if "pit_fip" in sm and not _close(round(fip, 2), sm["pit_fip"], _TOL_RUNS):
        out.append(f"pitcher_luck pit_fip: card={sm['pit_fip']} recomputed={round(fip, 2)}")
    return out


def _reconcile_windowed(
    conn: duckdb.DuckDBPyConnection, angle: StoryAngle, sm: dict[str, float]
) -> list[str]:
    """Re-run the detector's own source derivation for the subject and match the card.

    Window detectors (change / contact / league control) read game-level source
    directly; re-running their derivation against current data catches staleness
    and tampering between discovery and post (Path B — re-run provenance).
    """
    from padres_analytics.detect.angles import (
        _change_windows,
        _cohort_drift,
        _contact_windows,
        _context,
        _pts,
        _subject_window_obp,
    )

    pid = angle.subject_id
    if pid is None:
        return [f"{angle.key}: no subject_id to reconcile against"]
    ctx = _context(conn, angle.as_of.year, angle.as_of)
    if ctx is None:
        return [f"{angle.key}: no context to reconcile against"]

    def _rate(k: str, label: str, calc: float) -> str | None:
        if k in sm and not _close(round(calc, 3), sm[k], _TOL_WOBA):
            return f"{label} {k}: card={sm[k]} recomputed={round(calc, 3)}"
        return None

    def _delta(k: str, label: str, calc: int) -> str | None:
        if k in sm and not _close(calc, sm[k], _TOL_PTS):
            return f"{label} {k}: card={sm[k]} recomputed={calc}"
        return None

    out: list[str] = []
    if angle.key == "change":
        win = _change_windows(ctx, pid)
        if win is None:
            return ["change: windows no longer derive from source"]
        (r0, pa0), (r1, pa1), _, _ = win
        o0, o1 = r0 / pa0, r1 / pa1
        checks = [_rate("chg_prior", "change", o0), _rate("chg_recent", "change", o1)]
        checks.append(_delta("chg_delta", "change", abs(_pts(o1 - o0))))
        out = [c for c in checks if c]
    elif angle.key == "contact_change":
        win = _contact_windows(ctx, pid)
        if win is None:
            return ["contact_change: windows no longer derive from source"]
        prior, recent, _ = win
        m0, m1 = sum(prior) / len(prior), sum(recent) / len(recent)
        checks = [_rate("cc_prior", "contact_change", m0), _rate("cc_recent", "contact_change", m1)]
        checks.append(_delta("cc_delta", "contact_change", abs(_pts(m1 - m0))))
        out = [c for c in checks if c]
    elif angle.key == "league_control":
        drift = _cohort_drift(ctx)
        if drift is None:
            return ["league_control: cohort drift no longer derives from source"]
        lg_mean, _, _, ((ps, pe), (rs, re)) = drift
        obp0, _ = _subject_window_obp(ctx, pid, ps, pe)
        obp1, _ = _subject_window_obp(ctx, pid, rs, re)
        if obp0 is None or obp1 is None:
            return ["league_control: subject windows no longer derive from source"]
        residual = abs(_pts((obp1 - obp0) - lg_mean))
        out = [c for c in [_delta("lc_residual", "league_control", residual)] if c]
    return out


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
    if angle.key == "pitcher_luck":
        if angle.subject_id is None:
            return ["pitcher_luck: no subject_id to reconcile against"]
        return _reconcile_pitcher(conn, season, angle.subject_id, sm)
    if angle.key in ("change", "contact_change", "league_control"):
        return _reconcile_windowed(conn, angle, sm)
    return []  # live/unofficial cards are not reconciled against season tables


def verify_angle(conn: duckdb.DuckDBPyConnection, angle: StoryAngle) -> None:
    """Reconcile and raise :class:`ReconcileError` on any mismatch (a post gate)."""
    problems = reconcile(conn, angle)
    if problems:
        raise ReconcileError("source reconciliation failed:\n  " + "\n  ".join(problems))
