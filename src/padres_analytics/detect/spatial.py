"""Build spatial visual datasets (spray, …) from stored event-level Statcast.

Reads ``statcast_batted_balls`` and applies the canonical coordinate transform
and the rigor harness (n / coverage / handedness / park / caveat) the card face
requires. Keeps the transform in one place so every spatial card agrees.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.candidates import SpatialDataset, SpatialPoint

if TYPE_CHECKING:
    import duckdb

# Statcast Gameday pixel → field-feet, home plate at origin, +y toward center.
_HC_X0 = 125.42
_HC_Y0 = 198.27
_HC_SCALE = 2.5

# Spray sample-size floor (below this, label as illustrative, not predictive).
_SPRAY_FLOOR = 50

_EXTRA_BASE = {"double", "triple"}


def _kind(events: str | None) -> str:
    """Map a Statcast ``events`` outcome to a spray fill class."""
    if events == "home_run":
        return "home_run"
    if events in _EXTRA_BASE:
        return events  # "double" / "triple" → gold (XBH)
    if events == "single":
        return "single"
    return "out"


def _display_name(raw: str | None, fallback: str) -> str:
    """Turn Statcast's ``"Last, First"`` into ``"First Last"``."""
    if not raw:
        return fallback
    if ", " in raw:
        last, first = raw.split(", ", 1)
        return f"{first} {last}"
    return raw


def build_spray(
    conn: duckdb.DuckDBPyConnection,
    player_id: int,
    season: int,
    *,
    vs_hand: str | None = None,
    as_of: date | None = None,
) -> SpatialDataset | None:
    """Assemble a spray-chart ``SpatialDataset`` for one hitter from stored events.

    Args:
        conn: Read connection to padres.db.
        player_id: MLBAM batter id.
        season: Season year.
        vs_hand: Filter to pitcher handedness ``"R"``/``"L"``; ``None`` = all.
        as_of: Card date; defaults to today.

    Returns:
        A validated ``SpatialDataset`` (card="spray"), or ``None`` when no
        plottable batted balls exist (missing hit coordinates included).
    """
    # Regular season only — "2024 season" must not silently include spring training
    # (game_type 'S') or postseason ('F'/'D'/'L'/'W'), which would inflate the totals.
    sql = """
        SELECT player_name, events, stand, hc_x, hc_y
        FROM statcast_batted_balls
        WHERE player_id = ? AND season = ? AND game_type = 'R'
          AND hc_x IS NOT NULL AND hc_y IS NOT NULL
    """
    params: list[object] = [player_id, season]
    if vs_hand in ("R", "L"):
        sql += " AND p_throws = ?"
        params.append(vs_hand)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return None

    name_raw = rows[0][0]
    stand = rows[0][2]
    points: list[SpatialPoint] = []
    pull = 0
    for _name, events, _stand, hc_x, hc_y in rows:
        x = (hc_x - _HC_X0) * _HC_SCALE
        y = (_HC_Y0 - hc_y) * _HC_SCALE
        points.append(SpatialPoint(x=round(x, 1), y=round(y, 1), kind=_kind(events)))
        # Pull side: RH batter pulls to left field (x<0); LH pulls to right (x>0).
        if (stand == "R" and x < 0) or (stand == "L" and x > 0):
            pull += 1

    n = len(points)
    pull_rate = pull / n if n else 0.0
    hand = {"R": "vs RHP", "L": "vs LHP"}.get(vs_hand or "", "All")
    note = f"Pull rate {pull_rate:.0%} · shift-era (post-2023); spray shows tendency, not outcomes"
    if n < _SPRAY_FLOOR:
        note = f"Small sample ({n} BBE) — illustrative, not predictive · {note}"

    name = _display_name(name_raw, str(player_id))
    return SpatialDataset(
        card="spray",
        title=name,
        subtitle=f"Batted-ball spray · {season}",
        as_of=as_of or date.today(),
        points=points,
        n=n,
        coverage=f"{season} season",
        handedness=hand,
        park="All parks",
        note=note,
        source="Baseball Savant",
        headline=f"{name} {season} spray ({n} BBE)",
        claim_scope=f"{season} season",
    )


_FASTBALL = {"FF", "FA", "FT", "SI", "FC"}
_BREAKING = {"SL", "ST", "SV", "CU", "KC", "CS", "SC", "Sla", "KN"}
_OFFSPEED = {"CH", "FS", "FO", "EP"}


def _pitch_family(pt: str | None) -> str:
    if pt in _FASTBALL:
        return "fastball"
    if pt in _BREAKING:
        return "breaking"
    if pt in _OFFSPEED:
        return "offspeed"
    return "other"


def build_arsenal(
    conn: duckdb.DuckDBPyConnection,
    pitcher_id: int,
    season: int,
    *,
    as_of: date | None = None,
) -> SpatialDataset | None:
    """Assemble a pitch-movement (arsenal) ``SpatialDataset`` for one pitcher.

    Each point is one pitch: ``x`` = horizontal break, ``y`` = induced vertical
    break, both in INCHES (``pfx`` is feet → x12). Plotted from the catcher's POV
    for a single pitcher — no LHP/RHP mirroring, which would only matter when
    overlaying pitchers of opposite hands. Points carry their ``pitch_type`` (label)
    and ``release_speed`` (value) so the template can label clusters in-situ.

    Args:
        conn: Read connection to padres.db.
        pitcher_id: MLBAM pitcher id.
        season: Season year.
        as_of: Card date; defaults to today.

    Returns:
        A validated ``SpatialDataset`` (card="movement"), or ``None`` when the
        pitcher has no regular-season pitches with movement data.
    """
    rows = conn.execute(
        """
        SELECT pitcher_name, pitch_type, pfx_x, pfx_z, release_speed
        FROM statcast_pitches
        WHERE pitcher_id = ? AND season = ? AND game_type = 'R'
          AND pfx_x IS NOT NULL AND pfx_z IS NOT NULL AND pitch_type IS NOT NULL
        """,
        [pitcher_id, season],
    ).fetchall()
    if not rows:
        return None

    name_raw = rows[0][0]
    points: list[SpatialPoint] = []
    counts: dict[str, int] = {}
    fb_velo: list[float] = []
    for _name, pt, pfx_x, pfx_z, velo in rows:
        points.append(
            SpatialPoint(
                x=round(pfx_x * 12.0, 1),  # feet → inches
                y=round(pfx_z * 12.0, 1),
                kind=_pitch_family(pt),
                label=pt,
                value=round(velo, 1) if velo is not None else None,
            )
        )
        counts[pt] = counts.get(pt, 0) + 1
        if _pitch_family(pt) == "fastball" and velo is not None:
            fb_velo.append(velo)

    n = len(points)
    primary = max(counts, key=lambda k: counts[k])
    primary_pct = counts[primary] / n
    n_types = len([pt for pt, c in counts.items() if c / n >= 0.02])  # ignore <2% noise
    avg_fb = sum(fb_velo) / len(fb_velo) if fb_velo else None

    if avg_fb is not None:
        hero = {
            "value": f"{avg_fb:.0f}",
            "label": "Avg Fastball (mph)",
            "context": f"{n_types} pitches · {primary} {primary_pct:.0%}",
        }
    else:
        hero = {
            "value": str(n_types),
            "label": "Pitch Types",
            "context": f"{primary} {primary_pct:.0%} primary",
        }

    note = "Horizontal & induced vertical break (in) · catcher's POV"
    if n < 200:
        note = f"Small sample ({n} pitches) — illustrative · {note}"

    name = _display_name(name_raw, str(pitcher_id))
    return SpatialDataset(
        card="movement",
        title=name,
        subtitle=f"Pitch arsenal · {season}",
        as_of=as_of or date.today(),
        points=points,
        hero=hero,
        n=n,
        coverage=f"{season} season",
        handedness="All",
        park="All parks",
        pov="Catcher's POV",
        note=note,
        source="Baseball Savant",
        headline=f"{name} {season} arsenal ({n} pitches)",
        claim_scope=f"{season} season",
    )


_ZONE_X = (-0.83, 0.83)  # rulebook zone half-width (ft)
_ZONE_Z = (1.5, 3.5)  # league-average zone vertical bounds (ft)
_CELL_FLOOR = 5  # batted balls per cell below which a cell is suppressed


def _cell(plate_x: float, plate_z: float) -> tuple[int, int] | None:
    """Map a pitch location to a 3x3 zone cell (col, row), catcher's POV.

    Columns run left→right in screen space, i.e. high→low ``plate_x`` (catcher's
    POV flips horizontal). Returns None for pitches outside the rulebook zone.
    """
    if not (_ZONE_X[0] <= plate_x <= _ZONE_X[1] and _ZONE_Z[0] <= plate_z <= _ZONE_Z[1]):
        return None
    span_x = (_ZONE_X[1] - _ZONE_X[0]) / 3
    span_z = (_ZONE_Z[1] - _ZONE_Z[0]) / 3
    # Screen column: positive plate_x plots left, so flip before bucketing.
    col = min(2, int((_ZONE_X[1] - plate_x) / span_x))
    row = min(2, int((_ZONE_Z[1] - plate_z) / span_z))  # row 0 = top (high z)
    return col, row


def build_hot_cold(
    conn: duckdb.DuckDBPyConnection,
    player_id: int,
    season: int,
    *,
    as_of: date | None = None,
) -> SpatialDataset | None:
    """Assemble a hot/cold zone ``SpatialDataset`` — xwOBA on contact by zone cell.

    Shades a 3x3 in-zone grid by mean ``estimated_woba_using_speedangle`` of balls
    in play whose pitch was located in that cell, from the catcher's POV. Cells with
    fewer than :data:`_CELL_FLOOR` batted balls are suppressed (value ``None``) — the
    honesty mechanism that keeps a 1-ball cell from screaming red.

    Args:
        conn: Read connection to padres.db.
        player_id: MLBAM batter id.
        season: Season year.
        as_of: Card date; defaults to today.

    Returns:
        A validated ``SpatialDataset`` (card="hotcold") with one point per filled
        cell, or ``None`` when no in-zone contact with location + xwOBA exists.
    """
    rows = conn.execute(
        """
        SELECT player_name, plate_x, plate_z, estimated_woba
        FROM statcast_batted_balls
        WHERE player_id = ? AND season = ? AND game_type = 'R'
          AND plate_x IS NOT NULL AND plate_z IS NOT NULL AND estimated_woba IS NOT NULL
        """,
        [player_id, season],
    ).fetchall()
    if not rows:
        return None

    name_raw = rows[0][0]
    cells: dict[tuple[int, int], list[float]] = {}
    overall: list[float] = []
    for _name, px, pz, xwoba in rows:
        overall.append(xwoba)
        cell = _cell(px, pz)
        if cell is not None:
            cells.setdefault(cell, []).append(xwoba)

    if not cells:
        return None

    points: list[SpatialPoint] = []
    for (col, row), vals in cells.items():
        n_cell = len(vals)
        mean = sum(vals) / n_cell
        points.append(
            SpatialPoint(
                x=float(col),
                y=float(row),
                value=round(mean, 3) if n_cell >= _CELL_FLOOR else None,
                label=str(n_cell),
            )
        )

    n = len(overall)
    overall_xwoba = sum(overall) / n
    suppressed = sum(1 for p in points if p.value is None)
    note = "xwOBA on contact by zone · catcher's POV · cells <5 BBE suppressed"
    if n < 150:
        note = f"Small sample ({n} BBE) — illustrative · {note}"

    name = _display_name(name_raw, str(player_id))
    return SpatialDataset(
        card="hotcold",
        title=name,
        subtitle=f"Hot & cold zones · {season}",
        as_of=as_of or date.today(),
        points=points,
        hero={
            "value": f"{overall_xwoba:.3f}",
            "label": "xwOBA / Contact",
            "context": f"{n} BBE" + (f" · {suppressed} cells low-N" if suppressed else ""),
        },
        n=n,
        coverage=f"{season} season",
        handedness="All",
        park="All parks",
        pov="Catcher's POV",
        note=note,
        source="Baseball Savant",
        headline=f"{name} {season} hot/cold zones ({n} BBE)",
        claim_scope=f"{season} season",
    )


def build_zone(
    conn: duckdb.DuckDBPyConnection,
    pitcher_id: int,
    season: int,
    *,
    pitch_type: str | None = None,
    as_of: date | None = None,
) -> SpatialDataset | None:
    """Assemble a pitch-location density ``SpatialDataset`` for one pitcher.

    Points are ``x=plate_x`` (flipped to the **catcher's POV** at render time so
    positive plate_x plots left), ``y=plate_z`` — both already in feet. The hero is
    the in-zone rate against the league-average strike zone (a pitcher's aggregate
    card crosses many batters, so a fixed reference zone is correct here).

    Args:
        conn: Read connection to padres.db.
        pitcher_id: MLBAM pitcher id.
        season: Season year.
        pitch_type: Restrict to one pitch type (e.g. "SL"); ``None`` = all.
        as_of: Card date; defaults to today.

    Returns:
        A validated ``SpatialDataset`` (card="zone"), or ``None`` when the pitcher
        has no regular-season pitches with location data.
    """
    sql = """
        SELECT pitcher_name, plate_x, plate_z
        FROM statcast_pitches
        WHERE pitcher_id = ? AND season = ? AND game_type = 'R'
          AND plate_x IS NOT NULL AND plate_z IS NOT NULL
    """
    params: list[object] = [pitcher_id, season]
    if pitch_type:
        sql += " AND pitch_type = ?"
        params.append(pitch_type)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return None

    name_raw = rows[0][0]
    points: list[SpatialPoint] = []
    in_zone = 0
    for _name, px, pz in rows:
        points.append(SpatialPoint(x=round(px, 3), y=round(pz, 3)))
        if abs(px) <= 0.83 and 1.5 <= pz <= 3.5:
            in_zone += 1

    n = len(points)
    zone_pct = in_zone / n if n else 0.0
    label = pitch_type or "All pitches"
    note = "Location density · catcher's POV · strike zone = league avg"
    if n < 100:
        note = f"Small sample ({n} pitches) — illustrative · {note}"

    name = _display_name(name_raw, str(pitcher_id))
    return SpatialDataset(
        card="zone",
        title=name,
        subtitle=f"{label} location · {season}",
        as_of=as_of or date.today(),
        points=points,
        hero={"value": f"{zone_pct:.0%}", "label": "In Zone", "context": f"{label} · {n} pitches"},
        n=n,
        coverage=f"{season} season",
        handedness="All",
        park="All parks",
        pov="Catcher's POV",
        note=note,
        source="Baseball Savant",
        headline=f"{name} {season} {label} location ({n} pitches)",
        claim_scope=f"{season} season",
    )


def build_launch(
    conn: duckdb.DuckDBPyConnection,
    player_id: int,
    season: int,
    *,
    as_of: date | None = None,
) -> SpatialDataset | None:
    """Assemble a launch-angle / exit-velo ``SpatialDataset`` for one hitter.

    Each point is ``x=launch_angle`` (deg), ``y=launch_speed`` (mph). Barrels are
    Statcast's own classification (``launch_speed_angle == 6``) — not an eyeballed
    EV/LA wedge — so the barrel rate is authoritative. The template draws only the
    fixed reference lines (sweet-spot band 8-32 deg, hard-hit 95 mph).

    Args:
        conn: Read connection to padres.db.
        player_id: MLBAM batter id.
        season: Season year.
        as_of: Card date; defaults to today.

    Returns:
        A validated ``SpatialDataset`` (card="launch"), or ``None`` when the hitter
        has no batted balls with both launch angle and exit velocity.
    """
    rows = conn.execute(
        """
        SELECT player_name, launch_angle, launch_speed, launch_speed_angle, events
        FROM statcast_batted_balls
        WHERE player_id = ? AND season = ? AND game_type = 'R'
          AND launch_angle IS NOT NULL AND launch_speed IS NOT NULL
        """,
        [player_id, season],
    ).fetchall()
    if not rows:
        return None

    name_raw = rows[0][0]
    points: list[SpatialPoint] = []
    barrels = 0
    hard = 0
    for _name, la, ev, lsa, _events in rows:
        if lsa == 6:
            barrels += 1
            kind = "barrel"
        elif ev >= 95:
            kind = "hard_hit"
        else:
            kind = "soft"
        if ev >= 95:
            hard += 1  # hard-hit% includes barrels
        points.append(SpatialPoint(x=round(la, 1), y=round(ev, 1), kind=kind))

    n = len(points)
    brl_pct = barrels / n if n else 0.0
    hh_pct = hard / n if n else 0.0

    note = "Barrel = Statcast classification · sweet spot 8-32° · hard-hit 95+ mph"
    if n < 40:
        note = f"Small sample ({n} BBE) — illustrative, not predictive · {note}"

    name = _display_name(name_raw, str(player_id))
    return SpatialDataset(
        card="launch",
        title=name,
        subtitle=f"Launch angle / exit velo · {season}",
        as_of=as_of or date.today(),
        points=points,
        hero={
            "value": f"{brl_pct:.1%}",
            "label": "Barrel Rate",
            "context": f"{barrels} barrels · {hh_pct:.0%} hard-hit",
        },
        n=n,
        coverage=f"{season} season",
        handedness="All",
        park="All parks",
        note=note,
        source="Baseball Savant",
        headline=f"{name} {season} launch profile ({n} BBE)",
        claim_scope=f"{season} season",
    )


def build_hr_spray(
    conn: duckdb.DuckDBPyConnection,
    player_id: int,
    season: int,
    *,
    as_of: date | None = None,
) -> SpatialDataset | None:
    """Assemble a home-run spray ``SpatialDataset`` — landing spots + true distance.

    Distance is Statcast's ``hit_distance_sc`` (a trajectory model), never derived
    from the landing coordinates. The longest HR is labeled on the card.

    Args:
        conn: Read connection to padres.db.
        player_id: MLBAM batter id.
        season: Season year.
        as_of: Card date; defaults to today.

    Returns:
        A validated ``SpatialDataset`` (card="hr"), or ``None`` when the hitter
        has no plottable regular-season home runs.
    """
    rows = conn.execute(
        """
        SELECT player_name, hc_x, hc_y, hit_distance_sc
        FROM statcast_batted_balls
        WHERE player_id = ? AND season = ? AND game_type = 'R'
          AND events = 'home_run' AND hc_x IS NOT NULL AND hc_y IS NOT NULL
        """,
        [player_id, season],
    ).fetchall()
    if not rows:
        return None

    name_raw = rows[0][0]
    longest = max((r[3] for r in rows if r[3] is not None), default=None)
    dists = [r[3] for r in rows if r[3] is not None]
    avg_dist = sum(dists) / len(dists) if dists else None

    points: list[SpatialPoint] = []
    for _name, hc_x, hc_y, dist in rows:
        x = round((hc_x - _HC_X0) * _HC_SCALE, 1)
        y = round((_HC_Y0 - hc_y) * _HC_SCALE, 1)
        label = f"{dist:.0f} ft" if (dist is not None and dist == longest) else None
        points.append(SpatialPoint(x=x, y=y, kind="home_run", value=dist, label=label))

    n = len(points)
    name = _display_name(name_raw, str(player_id))
    ctx = ""
    if longest is not None:
        ctx = f"Longest {longest:.0f} ft"
        if avg_dist is not None:
            ctx += f" · avg {avg_dist:.0f} ft"
    return SpatialDataset(
        card="hr",
        title=name,
        subtitle=f"Home-run spray · {season}",
        as_of=as_of or date.today(),
        points=points,
        hero={"value": str(n), "label": "Home Runs", "context": ctx},
        n=n,
        coverage=f"{season} season",
        handedness="All",
        park="All parks",
        note="Landing direction · distance = Statcast hit_distance_sc (true carry)",
        source="Baseball Savant",
        headline=f"{name} {season} home runs ({n})",
        claim_scope=f"{season} season",
    )
