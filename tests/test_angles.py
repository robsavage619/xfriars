"""Tests for the story-discovery engine + audited infographic renderer."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from padres_analytics.detect.angles import (
    REGRESSION_PA_PRIOR,
    Stat,
    StoryAngle,
    audit_angle,
    confidence_tier,
    discover,
    lead_gloss,
    regress,
    reliability,
)
from padres_analytics.render.story_infographic import audit_rendered, compose

if TYPE_CHECKING:
    import duckdb

_NOW = datetime(2026, 6, 20, 0, 0, 0)


def _aux(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS team_rosters (player_id INTEGER, player_name VARCHAR)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS player_game_batting (player_id INTEGER, player_name VARCHAR, "
        "season INTEGER, game_date DATE, game_pk INTEGER, ab INTEGER, hits INTEGER, bb INTEGER, "
        "hbp INTEGER, source VARCHAR, ingested_at TIMESTAMP)"
    )


def _expected(
    c: duckdb.DuckDBPyConnection, pid: int, name: str, pa: int, w: float, x: float
) -> None:
    c.execute(
        "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa, bip, ba, est_ba, "
        "slg, est_slg, woba, est_woba, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [pid, name, 2026, pa, pa - 30, 0.25, 0.25, 0.40, 0.40, w, x, _NOW],
    )


def _ev(c: duckdb.DuckDBPyConnection, pid: int, name: str, att: int, ev: float) -> None:
    c.execute(
        "INSERT INTO statcast_batter_exitvelo_barrels (player_id, player_name, year, attempts, "
        "avg_hit_speed, max_hit_speed, barrels, brl_percent, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [pid, name, 2026, att, ev, ev + 15, 12, 9.0, _NOW],
    )


def _pct(c: duckdb.DuckDBPyConnection, pid: int, name: str, **vals: float) -> None:
    cols = ["player_id", "player_name", "year", *vals.keys(), "ingested_at"]
    c.execute(
        f"INSERT INTO statcast_batter_percentile_ranks ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})",
        [pid, name, 2026, *vals.values(), _NOW],
    )


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    _aux(conn)
    for i in range(6):
        _expected(conn, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    # Padres: an unlucky core + a free-swinger + an elite-barrel bat.
    pad = [
        (1, "Machado, Manny", 296, 0.270, 0.330),  # big individual under-performer
        (2, "Bogaerts, Xander", 250, 0.285, 0.318),
        (3, "Merrill, Jackson", 150, 0.300, 0.312),
    ]
    for pid, name, pa, w, x in pad:
        conn.execute("INSERT INTO team_rosters VALUES (?, ?)", [pid, name])
        _expected(conn, pid, name, pa, w, x)
        _ev(conn, pid, name, 150, 90.0)
        for gd, (ab, h) in zip(("2026-06-15", "2026-06-17"), ((10, 1), (12, 5)), strict=True):
            conn.execute(
                "INSERT INTO player_game_batting (player_id, player_name, season, game_date, "
                "game_pk, ab, hits, bb, hbp, source, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [pid, name, 2026, gd, 1, ab, h, 0, 0, "t", _NOW],
            )
    _pct(
        conn,
        1,
        "Machado, Manny",
        xwoba=70,
        hard_hit_percent=80,
        brl_percent=75,
        chase_percent=45,
        k_percent=44,
    )
    _pct(
        conn,
        2,
        "Bogaerts, Xander",
        xwoba=55,
        hard_hit_percent=30,
        brl_percent=40,
        chase_percent=8,
        k_percent=68,
    )  # 8th pct chase -> approach outlier
    _pct(
        conn,
        3,
        "Merrill, Jackson",
        xwoba=60,
        hard_hit_percent=92,
        brl_percent=95,
        chase_percent=40,
        k_percent=32,
    )  # 95th pct barrels -> power outlier


def test_reliability_and_regression() -> None:
    """The 220-PA prior weights observation and prior equally at the break-even."""
    assert reliability(REGRESSION_PA_PRIOR) == 0.5
    assert regress(0.400, REGRESSION_PA_PRIOR, 0.320) == (0.400 + 0.320) / 2
    assert confidence_tier(0.92) == "high"
    assert confidence_tier(0.50) == "moderate"
    assert confidence_tier(0.20) == "low"


def test_discover_surfaces_ranked_angles(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Multiple lenses fire, are ranked by interest, and carry direction + confidence."""
    _seed(padres_db)
    angles = discover(padres_db, 2026, as_of=date(2026, 6, 20))
    keys = {a.key for a in angles}
    assert "team_luck" in keys
    assert "player_luck" in keys  # Machado under-performing
    assert "approach_outlier" in keys  # Bogaerts 8th-pct chase
    assert "power_outlier" in keys  # Merrill 95th-pct barrels
    # sorted by interest descending
    assert [a.interest for a in angles] == sorted((a.interest for a in angles), reverse=True)
    # direction-aware: the under-performer is "owed up"
    pl = next(a for a in angles if a.key == "player_luck")
    assert pl.direction == "up"
    assert pl.title == "BETTER THAN THE LINE"


def test_no_story_below_threshold(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A lineup performing at its expected level yields no luck story."""
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    # Padres hitting exactly their expected line — nothing owed.
    for pid in (1, 2, 3):
        padres_db.execute("INSERT INTO team_rosters VALUES (?, ?)", [pid, f"P, {pid}"])
        _expected(padres_db, pid, f"P, {pid}", 300, 0.321, 0.321)
    angles = discover(padres_db, 2026, as_of=date(2026, 6, 20))
    assert not any(a.key in ("team_luck", "player_luck") for a in angles)


def test_render_audit_passes_for_real_angle(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Every shown stat and headline number lands on the rendered card."""
    _seed(padres_db)
    angles = discover(padres_db, 2026, as_of=date(2026, 6, 20))
    for a in angles:
        assert not audit_angle(a), f"{a.key} self-audit: {audit_angle(a)}"
        svg = compose(a)
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        problems = audit_rendered(a, svg)
        assert not problems, f"{a.key} render audit: {problems}"


def test_audit_catches_unbacked_headline_number(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A headline number not backed by any stat is flagged (the credibility guard)."""
    _seed(padres_db)
    angle = next(
        a for a in discover(padres_db, 2026, as_of=date(2026, 6, 20)) if a.key == "team_luck"
    )
    tampered = type(angle)(**{**angle.__dict__, "headline": "The bats are 999 points unlucky"})
    assert any("999" in v for v in audit_angle(tampered))


def test_render_audit_catches_dropped_stat(padres_db: duckdb.DuckDBPyConnection) -> None:
    """If a shown stat never reaches the SVG, the render audit flags it."""
    _seed(padres_db)
    angle = next(
        a for a in discover(padres_db, 2026, as_of=date(2026, 6, 20)) if a.key == "team_luck"
    )
    broken = type(angle)(**{**angle.__dict__, "panels": []})  # nothing drawn
    assert audit_rendered(broken, compose(broken))


def test_injured_players_are_not_featured(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A player on the IL (status != Active) is never surfaced as a current story."""
    padres_db.execute("DROP TABLE IF EXISTS team_rosters")
    padres_db.execute(
        "CREATE TABLE team_rosters (player_id INTEGER, player_name VARCHAR, status VARCHAR)"
    )
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    # An active star and an injured 95th-pct barrel bat.
    padres_db.execute("INSERT INTO team_rosters VALUES (1, 'Active Star', 'Active')")
    padres_db.execute("INSERT INTO team_rosters VALUES (2, 'Hurt Slugger', 'Injured 60-Day')")
    _expected(padres_db, 1, "Star, Active", 300, 0.300, 0.330)
    _expected(padres_db, 2, "Slugger, Hurt", 300, 0.300, 0.340)
    _ev(padres_db, 2, "Slugger, Hurt", 200, 92.0)
    _pct(padres_db, 2, "Slugger, Hurt", brl_percent=99, hard_hit_percent=99)

    subjects = {a.subject for a in discover(padres_db, 2026, as_of=date(2026, 6, 20))}
    assert not any("Hurt" in s or "Slugger" in s for s in subjects)


def _games(
    conn: duckdb.DuckDBPyConnection,
    pid: int,
    name: str,
    lines: list[tuple[int, int]],
    start_day: int = 1,
) -> None:
    """Insert sequential single-game (ab, hits) lines for a batter from June `start_day`."""
    for i, (ab, h) in enumerate(lines):
        conn.execute(
            "INSERT INTO player_game_batting (player_id, player_name, season, game_date, "
            "game_pk, ab, hits, bb, hbp, source, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [pid, name, 2026, f"2026-06-{start_day + i:02d}", 7000 + i, ab, h, 0, 0, "t", _NOW],
        )


def test_change_fires_on_a_separable_split(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A batter ice-cold then red-hot over two full windows surfaces a change story."""
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    padres_db.execute("INSERT INTO team_rosters VALUES (1, 'Tatis Jr., Fernando')")
    cold = [(4, 0)] * 15  # ~.000 over 60 AB
    hot = [(4, 2)] * 15  # ~.500 over 60 AB
    _games(padres_db, 1, "Tatis Jr., Fernando", cold + hot)

    angles = discover(padres_db, 2026, as_of=date(2026, 6, 25))
    chg = next((a for a in angles if a.key == "change"), None)
    assert chg is not None
    assert chg.direction == "up"
    assert chg.title == "FLIPPED A SWITCH"
    assert chg.reliability >= 0.80  # p_real gate
    assert not audit_angle(chg)


def test_change_rejects_noise_and_small_samples(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Steady production, and a big swing on too few PA, both yield no change story."""
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    padres_db.execute("INSERT INTO team_rosters VALUES (1, 'Steady, Sam')")
    padres_db.execute("INSERT INTO team_rosters VALUES (2, 'Tiny, Tim')")
    _games(padres_db, 1, "Steady, Sam", [(4, 1)] * 30)  # flat .250 over two full windows
    _games(padres_db, 2, "Tiny, Tim", [(1, 0)] * 15 + [(1, 1)] * 15)  # huge swing, 15 PA/window

    angles = discover(padres_db, 2026, as_of=date(2026, 6, 25))
    assert not any(a.key == "change" for a in angles)


def _pitching(
    conn: duckdb.DuckDBPyConnection,
    pid: int,
    name: str,
    *,
    ip: str,
    era: str,
    so: int,
    bb: int,
    hr: int,
    hbp: int,
    tbf: int,
) -> None:
    """Insert a pitcher-season row (creating the ingest-made table on first use)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS player_season_pitching (player_id INTEGER, "
        "player_name VARCHAR, season INTEGER, team_id INTEGER, so INTEGER, bb INTEGER, "
        "hr INTEGER, hbp INTEGER, tbf INTEGER, ip VARCHAR, era VARCHAR)"
    )
    conn.execute(
        "INSERT INTO player_season_pitching (player_id, player_name, season, team_id, so, bb, "
        "hr, hbp, tbf, ip, era) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [pid, name, 2026, 135, so, bb, hr, hbp, tbf, ip, era],
    )


def _fip_const(conn: duckdb.DuckDBPyConnection, const: float = 3.10) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS league_pitching_constants (season INTEGER, fip_const DOUBLE, "
        "lg_era DOUBLE, lg_ip DOUBLE)"
    )
    conn.execute(
        "INSERT INTO league_pitching_constants (season, fip_const, lg_era, lg_ip) VALUES (?,?,?,?)",
        [2026, const, 4.20, 20000.0],
    )


def test_pitcher_luck_surfaces_the_widest_era_fip_gap(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    """An ace ERA hiding bad peripherals beats a smaller gap, with direction + audit."""
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    _fip_const(padres_db)
    padres_db.execute("INSERT INTO team_rosters VALUES (10, 'Lucky, Lou')")
    padres_db.execute("INSERT INTO team_rosters VALUES (11, 'Solid, Sid')")
    # Lou: shiny 1.50 ERA but weak peripherals -> high FIP -> big lucky gap.
    _pitching(
        padres_db, 10, "Lucky, Lou", ip="40.0", era="1.50", so=25, bb=22, hr=6, hbp=3, tbf=170
    )
    # Sid: 3.20 ERA matching a ~3.2 FIP -> negligible gap.
    _pitching(
        padres_db, 11, "Solid, Sid", ip="40.0", era="3.20", so=40, bb=12, hr=4, hbp=2, tbf=165
    )

    angles = discover(padres_db, 2026, as_of=date(2026, 6, 25))
    pit = next((a for a in angles if a.key == "pitcher_luck"), None)
    assert pit is not None
    assert "Lou" in pit.subject  # the wider gap wins
    assert pit.direction == "down"  # ERA outrunning FIP = lucky
    assert pit.title == "OUTRUNNING THE ARM"
    assert not audit_angle(pit)


def test_pitcher_luck_needs_innings_and_a_constant(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Below the IP floor, or with no league constant, no pitcher story fires."""
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    padres_db.execute("INSERT INTO team_rosters VALUES (10, 'Tiny, Tim')")
    # A huge gap but only 10 IP — below the 30 IP floor.
    _pitching(padres_db, 10, "Tiny, Tim", ip="10.0", era="9.00", so=4, bb=10, hr=4, hbp=2, tbf=70)
    # No league_pitching_constants row yet -> detector must no-op, not crash.
    assert not any(
        a.key == "pitcher_luck" for a in discover(padres_db, 2026, as_of=date(2026, 6, 25))
    )
    _fip_const(padres_db)  # now a constant exists, but IP still too low
    assert not any(
        a.key == "pitcher_luck" for a in discover(padres_db, 2026, as_of=date(2026, 6, 25))
    )


_PRIOR = ("2026-05-01", "2026-05-25")
_RECENT = ("2026-05-26", "2026-06-19")


def _league_window(
    conn: duckdb.DuckDBPyConnection, pid: int, window: str, ab: int, hits: int
) -> None:
    """Insert one league-cohort window row (creating the ingest table on first use)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS league_window_batting (season INTEGER, window_label VARCHAR, "
        "start_date DATE, end_date DATE, player_id INTEGER, player_name VARCHAR, ab INTEGER, "
        "hits INTEGER, bb INTEGER, hbp INTEGER, pa INTEGER)"
    )
    start, end = _PRIOR if window == "prior" else _RECENT
    conn.execute(
        "INSERT INTO league_window_batting (season, window_label, start_date, end_date, player_id, "
        "player_name, ab, hits, bb, hbp, pa) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [2026, window, start, end, pid, f"Lg {pid}", ab, hits, 0, 0, ab],
    )


def _subject_games(
    conn: duckdb.DuckDBPyConnection, pid: int, window: str, ab: int, hits: int
) -> None:
    """Insert the subject's games inside a window's date bounds (one game per row)."""
    start, _ = _PRIOR if window == "prior" else _RECENT
    day = int(start[-2:])
    month = start[5:7]
    conn.execute(
        "INSERT INTO player_game_batting (player_id, player_name, season, game_date, game_pk, ab, "
        "hits, bb, hbp, source, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [pid, "Subject, Sam", 2026, f"2026-{month}-{day:02d}", 1, ab, hits, 0, 0, "t", _NOW],
    )


def test_league_control_isolates_a_player_from_league_drift(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    """A Padre collapsing while the league holds steady surfaces, controlled and audited."""
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    # Non-Padres cohort: ~flat league with a little spread (prior ~.300, recent ~.305).
    for i in range(30):
        _league_window(padres_db, 1000 + i, "prior", 100, 30)
        _league_window(padres_db, 1000 + i, "recent", 100, 28 + (i % 9))  # mean ~.32, real spread
    # The Padre: scorching prior window, ice-cold recent window — a big personal swing.
    padres_db.execute("INSERT INTO team_rosters VALUES (1, 'Subject, Sam')")
    for _ in range(6):
        _subject_games(padres_db, 1, "prior", 10, 5)  # .500 over 60 AB
        _subject_games(padres_db, 1, "recent", 10, 2)  # .200 over 60 AB

    angles = discover(padres_db, 2026, as_of=date(2026, 6, 25))
    lc = next((a for a in angles if a.key == "league_control"), None)
    assert lc is not None
    assert "Sam" in lc.subject
    assert lc.direction == "down"
    assert not audit_angle(lc)
    # the controlled (residual) change is reported, distinct from the raw change
    assert any(s.key == "lc_residual" for s in lc.stats)


def test_league_control_noops_without_cohort(padres_db: duckdb.DuckDBPyConnection) -> None:
    """With no league_window_batting cohort, the detector must no-op, not crash."""
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    padres_db.execute("INSERT INTO team_rosters VALUES (1, 'Subject, Sam')")
    for _ in range(6):
        _subject_games(padres_db, 1, "prior", 10, 5)
        _subject_games(padres_db, 1, "recent", 10, 2)
    assert not any(
        a.key == "league_control" for a in discover(padres_db, 2026, as_of=date(2026, 6, 25))
    )


def _bbe(conn: duckdb.DuckDBPyConnection, pid: int, name: str, woba_seq: list[float]) -> None:
    """Insert a chronological sequence of batted balls (xwOBA on contact) for a hitter."""
    for i, w in enumerate(woba_seq):
        conn.execute(
            "INSERT INTO statcast_batted_balls (player_id, player_name, season, game_type, "
            "game_date, game_pk, at_bat_number, pitch_number, estimated_woba) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [pid, name, 2026, "R", "2026-06-01", 1, i, 1, w],
        )


def test_contact_change_fires_on_a_real_quality_shift(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    """Soft contact turning loud over two BBE windows surfaces, audited."""
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    padres_db.execute("INSERT INTO team_rosters VALUES (1, 'Tatis Jr., Fernando')")
    soft = [0.10, 0.20] * 25  # 50 BBE, ~.150 xwOBACON
    loud = [0.55, 0.65] * 25  # 50 BBE, ~.600 xwOBACON
    _bbe(padres_db, 1, "Tatis Jr., Fernando", soft + loud)

    angles = discover(padres_db, 2026, as_of=date(2026, 6, 25))
    cc = next((a for a in angles if a.key == "contact_change"), None)
    assert cc is not None
    assert cc.direction == "up"
    assert cc.title == "SQUARING IT UP"
    assert not audit_angle(cc)


def test_contact_change_rejects_flat_and_thin_samples(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Steady contact, and too few batted balls, both yield no contact-change story."""
    _aux(padres_db)
    for i in range(6):
        _expected(padres_db, 900 + i, f"League, G{i}", 300, 0.320, 0.322)
    padres_db.execute("INSERT INTO team_rosters VALUES (1, 'Flat, Fred')")
    padres_db.execute("INSERT INTO team_rosters VALUES (2, 'Thin, Theo')")
    _bbe(padres_db, 1, "Flat, Fred", [0.25, 0.35] * 50)  # 100 BBE, no shift
    _bbe(padres_db, 2, "Thin, Theo", [0.10, 0.60] * 20)  # 40 BBE total — below 2 windows

    assert not any(
        a.key == "contact_change" for a in discover(padres_db, 2026, as_of=date(2026, 6, 25))
    )


def test_lead_gloss_translates_the_headline_stat() -> None:
    """A card's lead jargon stat gets a plain-language gloss; ungloss-able -> None."""
    angle = StoryAngle(
        key="pitcher_luck",
        subject="Wandy Peralta",
        title="OUTRUNNING THE ARM",
        headline="Peralta's 1.96 ERA outruns a 4.49 FIP.",
        thesis="t",
        direction="down",
        effect=2.5,
        reliability=0.4,
        interest=1.0,
        confidence="low",
        as_of=date(2026, 6, 20),
        subject_id=10,
        stats=[
            Stat("pit_era", 1.96, "count", "ERA", 100),
            Stat("pit_fip", 4.49, "count", "FIP", 100),
        ],
    )
    gloss = lead_gloss(angle)
    assert gloss is not None and gloss.startswith("ERA is")  # first glossable stat wins
    # the gloss lands on the card (wrapped across lines, so check a one-line fragment)
    assert "earned runs a pitcher allows" in compose(angle)

    bare = type(angle)(**{**angle.__dict__, "stats": [Stat("x", 1, "count", "x", 0)]})
    assert lead_gloss(bare) is None
