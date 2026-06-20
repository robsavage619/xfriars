"""Tests for the spatial spray builder — coord transform, filters, harness."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from padres_analytics.detect.candidates import SpatialDataset
from padres_analytics.detect.spatial import (
    build_arsenal,
    build_bat_speed,
    build_hot_cold,
    build_hr_spray,
    build_launch,
    build_release,
    build_rolling,
    build_spray,
    build_swing_take,
    build_zone,
)

_BP_COLS = (
    "batter_id, batter_name, season, game_type, game_date, game_pk, at_bat_number, "
    "pitch_number, pitch_type, plate_x, plate_z, sz_top, sz_bot, zone, description, type, "
    "delta_run_exp, bat_speed, swing_length, estimated_woba, stand, p_throws, ingested_at"
)


def _insert_batter_pitch(
    conn: duckdb.DuckDBPyConnection,
    *,
    pid: int = 1,
    season: int = 2024,
    game_type: str = "R",
    ab: int = 1,
    px: float = 0.0,
    pz: float = 2.5,
    desc: str = "ball",
    dre: float = 0.0,
    bs: float | None = None,
    sl: float | None = None,
) -> None:
    conn.execute(
        f"INSERT INTO statcast_batter_pitches ({_BP_COLS}) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            pid,
            "Test, Player",
            season,
            game_type,
            date(season, 5, 1),
            100,
            ab,
            1,
            "FF",
            px,
            pz,
            3.5,
            1.5,
            5,
            desc,
            "B",
            dre,
            bs,
            sl,
            None,
            "R",
            "R",
            datetime(2024, 5, 1, 0, 0, 0),
        ],
    )


_PITCH_COLS = (
    "pitcher_id, pitcher_name, season, game_type, game_date, game_pk, at_bat_number, "
    "pitch_number, pitch_type, release_speed, pfx_x, pfx_z, plate_x, plate_z, sz_top, "
    "sz_bot, release_pos_x, release_pos_z, description, stand, p_throws, ingested_at"
)


def _insert_pitch(
    conn: duckdb.DuckDBPyConnection,
    *,
    pid: int = 1,
    season: int = 2024,
    game_type: str = "R",
    ab: int = 1,
    pitch: int = 1,
    pitch_type: str = "FF",
    velo: float = 97.0,
    pfx_x: float = 1.0,
    pfx_z: float = 1.4,
    px: float = 0.0,
    pz: float = 2.5,
) -> None:
    conn.execute(
        f"INSERT INTO statcast_pitches ({_PITCH_COLS}) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            pid,
            "Cease, Dylan",
            season,
            game_type,
            date(season, 5, 1),
            100,
            ab,
            pitch,
            pitch_type,
            velo,
            pfx_x,
            pfx_z,
            px,
            pz,
            3.4,
            1.6,
            -1.8,
            6.0,
            "ball",
            "R",
            "R",
            datetime(2024, 5, 1, 0, 0, 0),
        ],
    )


if TYPE_CHECKING:
    import duckdb

_COLS = (
    "player_id, player_name, season, game_type, game_date, game_pk, at_bat_number, "
    "pitch_number, events, bb_type, description, stand, p_throws, hc_x, hc_y, "
    "plate_x, plate_z, launch_speed, launch_angle, launch_speed_angle, hit_distance_sc, "
    "estimated_woba, ingested_at"
)


def _insert(
    conn: duckdb.DuckDBPyConnection,
    *,
    pid: int = 1,
    season: int = 2024,
    game_type: str = "R",
    ab: int = 1,
    pitch: int = 1,
    events: str = "single",
    stand: str = "R",
    p_throws: str = "R",
    hc_x: float | None = 125.42,
    hc_y: float | None = 198.27,
    dist: float = 380.0,
    ev: float = 95.0,
    la: float = 22.0,
    lsa: int | None = None,
    plate_x: float | None = 0.0,
    plate_z: float | None = 2.5,
    xwoba: float = 0.55,
) -> None:
    conn.execute(
        f"INSERT INTO statcast_batted_balls ({_COLS}) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            pid,
            "Test, Player",
            season,
            game_type,
            date(season, 5, 1),
            100,
            ab,
            pitch,
            events,
            "fly_ball",
            "hit_into_play",
            stand,
            p_throws,
            hc_x,
            hc_y,
            plate_x,
            plate_z,
            ev,
            la,
            lsa,
            dist,
            xwoba,
            datetime(2024, 5, 1, 0, 0, 0),
        ],
    )


def test_coordinate_transform_home_plate_at_origin(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A ball at the (125.42, 198.27) pixel origin maps to field (0, 0)."""
    _insert(padres_db, hc_x=125.42, hc_y=198.27)
    ds = build_spray(padres_db, 1, 2024)
    assert ds is not None
    assert ds.points[0].x == 0.0
    assert ds.points[0].y == 0.0


def test_y_axis_not_inverted(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Smaller hc_y (higher on screen) must map to larger field y (deeper)."""
    _insert(padres_db, ab=1, hc_y=98.27)  # 100px above origin
    ds = build_spray(padres_db, 1, 2024)
    assert ds is not None
    assert ds.points[0].y == 250.0  # (198.27 - 98.27) * 2.5


def test_excludes_spring_and_postseason(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Only regular-season ('R') batted balls count toward the spray."""
    _insert(padres_db, ab=1, game_type="R")
    _insert(padres_db, ab=2, game_type="S")  # spring training
    _insert(padres_db, ab=3, game_type="D")  # division series
    ds = build_spray(padres_db, 1, 2024)
    assert ds is not None
    assert ds.n == 1


def test_returns_none_without_coordinates(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Rows missing hit coordinates are not plottable → None."""
    _insert(padres_db, hc_x=None, hc_y=None)
    assert build_spray(padres_db, 1, 2024) is None


def test_small_sample_labeled(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Below the 50-BBE floor, the card note flags an illustrative sample."""
    _insert(padres_db)
    ds = build_spray(padres_db, 1, 2024)
    assert ds is not None
    assert isinstance(ds, SpatialDataset)
    assert "illustrative" in ds.note.lower()


def test_handedness_filter_and_label(padres_db: duckdb.DuckDBPyConnection) -> None:
    """vs_hand filters to pitcher handedness and labels the harness."""
    _insert(padres_db, ab=1, p_throws="R")
    _insert(padres_db, ab=2, p_throws="L")
    ds = build_spray(padres_db, 1, 2024, vs_hand="L")
    assert ds is not None
    assert ds.n == 1
    assert ds.handedness == "vs LHP"


def test_hr_spray_counts_only_home_runs(padres_db: duckdb.DuckDBPyConnection) -> None:
    """HR spray plots only home runs and surfaces the longest distance."""
    _insert(padres_db, ab=1, events="home_run", dist=420.0)
    _insert(padres_db, ab=2, events="home_run", dist=455.0)
    _insert(padres_db, ab=3, events="single", dist=180.0)
    ds = build_hr_spray(padres_db, 1, 2024)
    assert ds is not None
    assert ds.card == "hr"
    assert ds.n == 2
    assert ds.hero is not None and ds.hero["value"] == "2"
    assert "455" in ds.hero["context"]
    assert any(p.label and "455" in p.label for p in ds.points)


def test_hr_spray_none_without_home_runs(padres_db: duckdb.DuckDBPyConnection) -> None:
    """No home runs → no card."""
    _insert(padres_db, events="single")
    assert build_hr_spray(padres_db, 1, 2024) is None


def test_arsenal_families_and_inch_transform(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Pitch types map to families; pfx feet convert to inches (x12)."""
    for i in range(5):
        _insert_pitch(padres_db, ab=i + 1, pitch_type="FF", velo=97.0, pfx_x=1.0, pfx_z=1.5)
    _insert_pitch(padres_db, ab=10, pitch_type="SL", velo=88.0, pfx_x=-0.5, pfx_z=0.1)
    _insert_pitch(padres_db, ab=11, pitch_type="CH", velo=89.0, pfx_x=1.2, pfx_z=0.5)
    ds = build_arsenal(padres_db, 1, 2024)
    assert ds is not None
    assert ds.card == "movement"
    assert ds.n == 7
    assert ds.pov == "Catcher's POV"
    ff = next(p for p in ds.points if p.label == "FF")
    assert ff.kind == "fastball"
    assert ff.x == 12.0 and ff.y == 18.0  # 1.0*12, 1.5*12
    assert next(p for p in ds.points if p.label == "SL").kind == "breaking"
    assert next(p for p in ds.points if p.label == "CH").kind == "offspeed"
    assert ds.hero is not None and ds.hero["value"] == "97"  # avg fastball


def test_arsenal_none_without_pitches(padres_db: duckdb.DuckDBPyConnection) -> None:
    """No stored pitches → no card."""
    assert build_arsenal(padres_db, 999, 2024) is None


def test_release_height_hero_and_families(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Release card surfaces avg release height and classifies pitch families."""
    for i in range(4):
        _insert_pitch(padres_db, ab=i + 1, pitch_type="FF")  # release_pos_z = 6.0
    _insert_pitch(padres_db, ab=9, pitch_type="SL")
    ds = build_release(padres_db, 1, 2024)
    assert ds is not None
    assert ds.card == "release"
    assert ds.n == 5
    assert ds.hero is not None and ds.hero["value"] == "6.0"
    assert ds.pov == "Catcher's POV"
    assert next(p for p in ds.points if p.label == "SL").kind == "breaking"


def test_release_none_without_pitches(padres_db: duckdb.DuckDBPyConnection) -> None:
    """No stored pitches → no release card."""
    assert build_release(padres_db, 999, 2024) is None


def test_rolling_builds_form_line(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Rolling card traces a trailing-window xwOBA line with a season-mean hero."""
    for i in range(25):
        _insert(padres_db, ab=i + 1, xwoba=0.55)
    ds = build_rolling(padres_db, 1, 2024)
    assert ds is not None
    assert ds.card == "rolling"
    assert ds.n == 25
    assert ds.hero is not None and ds.hero["value"] == "0.550"
    assert len(ds.points) >= 1


def test_rolling_none_when_too_few(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Fewer than 20 batted balls → no trend card."""
    for i in range(5):
        _insert(padres_db, ab=i + 1)
    assert build_rolling(padres_db, 1, 2024) is None


def test_swing_take_regions_and_total(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Pitches bucket into attack regions; run value sums per region and overall."""
    _insert_batter_pitch(padres_db, ab=1, px=0.0, pz=2.5, dre=4.0)  # heart
    _insert_batter_pitch(padres_db, ab=2, px=0.8, pz=2.5, dre=-2.0)  # shadow
    _insert_batter_pitch(padres_db, ab=3, px=1.3, pz=2.5, dre=3.0)  # chase
    _insert_batter_pitch(padres_db, ab=4, px=2.0, pz=2.5, dre=1.0)  # waste
    ds = build_swing_take(padres_db, 1, 2024)
    assert ds is not None
    assert ds.card == "swingtake"
    assert ds.n == 4
    assert ds.hero is not None and ds.hero["value"] == "+6"  # 4 - 2 + 3 + 1
    by = {p.kind: p.value for p in ds.points}
    assert by["heart"] == 4.0
    assert by["shadow"] == -2.0
    assert by["chase"] == 3.0
    assert by["waste"] == 1.0


def test_swing_take_none_without_pitches(padres_db: duckdb.DuckDBPyConnection) -> None:
    """No faced pitches → no swing/take card."""
    assert build_swing_take(padres_db, 999, 2024) is None


def test_bat_speed_filters_checked_swings(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Bat speed excludes non-competitive (<50 mph) swings; avg + fast% over the rest."""
    for i, sp in enumerate([70.0, 72.0, 76.0, 78.0, 80.0]):
        _insert_batter_pitch(padres_db, ab=i + 1, desc="swinging_strike", bs=sp)
    _insert_batter_pitch(padres_db, ab=99, desc="foul", bs=40.0)  # checked → excluded
    ds = build_bat_speed(padres_db, 1, 2024)
    assert ds is not None
    assert ds.card == "batspeed"
    assert ds.n == 5  # the 40 mph swing dropped
    assert ds.hero is not None and ds.hero["value"] == "75.2"  # mean of the five
    assert "60%" in ds.hero["context"]  # 3 of 5 are >= 75 mph


def test_bat_speed_none_without_swings(padres_db: duckdb.DuckDBPyConnection) -> None:
    """No tracked swings → no bat-speed card."""
    assert build_bat_speed(padres_db, 999, 2024) is None


# ── Pipeline integration ─────────────────────────────────────────────────────


def test_build_spatial_dispatch(padres_db: duckdb.DuckDBPyConnection) -> None:
    """The registry dispatches by card name and rejects unknown cards."""
    from padres_analytics.detect.spatial import build_spatial

    for i in range(60):
        _insert(padres_db, ab=i + 1, events="single")
    ds = build_spatial(padres_db, "spray", 1, 2024)
    assert ds is not None
    assert ds.card == "spray"
    with pytest.raises(KeyError):
        build_spatial(padres_db, "nope", 1, 2024)


def test_spatial_candidate_round_trip(padres_db: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """A spatial card flows through emit → candidate → draft → render → verify."""
    from padres_analytics.detect.base import emit
    from padres_analytics.detect.spatial import emit_spatial_candidate
    from padres_analytics.tweets.draft import ingest_draft

    for i in range(60):
        _insert(padres_db, ab=i + 1, events="single")

    candidate = emit_spatial_candidate(padres_db, "spray", 1, 2024)
    assert candidate is not None
    assert candidate.payload_kind == "spatial"
    assert emit(padres_db, [candidate]) == 1

    draft = {
        "candidate_id": candidate.candidate_id,
        "text": "Manny Machado spray chart.",  # no digits → digit-audit passes
        "interesting_judgment": "pull tendency",
        "model": "test",
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))

    ingest_draft(padres_db, draft_path, tmp_path)
    assert (tmp_path / f"{candidate.candidate_id}.png").exists()


def test_zone_in_zone_rate_and_pitch_filter(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Zone card reports in-zone rate (|x|<=0.83, 1.5<=z<=3.5) and filters by pitch."""
    _insert_pitch(padres_db, ab=1, pitch_type="SL", px=0.0, pz=2.5)  # in zone
    _insert_pitch(padres_db, ab=2, pitch_type="SL", px=0.0, pz=0.6)  # below zone
    _insert_pitch(padres_db, ab=3, pitch_type="FF", px=0.0, pz=2.5)  # different pitch
    ds = build_zone(padres_db, 1, 2024, pitch_type="SL")
    assert ds is not None
    assert ds.card == "zone"
    assert ds.n == 2  # only sliders
    assert ds.pov == "Catcher's POV"
    assert ds.hero is not None and ds.hero["value"] == "50%"  # 1 of 2 in zone


def test_zone_none_without_location(padres_db: duckdb.DuckDBPyConnection) -> None:
    """No stored pitches → no zone card."""
    assert build_zone(padres_db, 999, 2024) is None


def test_hot_cold_suppresses_low_n_cells(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Cells with fewer than 5 batted balls render suppressed (value None)."""
    # Middle cell (plate_x≈0, plate_z≈2.5): 6 BBE → filled.
    for i in range(6):
        _insert(padres_db, ab=i + 1, plate_x=0.0, plate_z=2.5, xwoba=0.500)
    # Up-and-in cell: only 2 BBE → suppressed.
    _insert(padres_db, ab=20, plate_x=0.6, plate_z=3.2, xwoba=0.900)
    _insert(padres_db, ab=21, plate_x=0.6, plate_z=3.2, xwoba=0.900)
    ds = build_hot_cold(padres_db, 1, 2024)
    assert ds is not None
    assert ds.card == "hotcold"
    assert ds.n == 8
    by_cell = {(p.x, p.y): p for p in ds.points}
    mid = by_cell[(1.0, 1.0)]
    assert mid.value == 0.5 and mid.label == "6"
    lown = next(p for p in ds.points if p.label == "2")
    assert lown.value is None  # suppressed


def test_hot_cold_excludes_out_of_zone(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Pitches outside the rulebook zone don't form cells (but count toward xwOBA)."""
    _insert(padres_db, ab=1, plate_x=3.0, plate_z=2.5, xwoba=0.4)  # way outside
    ds = build_hot_cold(padres_db, 1, 2024)
    assert ds is None  # no in-zone contact → no card


def test_launch_barrel_rate_uses_statcast_flag(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Barrels come from launch_speed_angle==6; rate is barrels / BBE."""
    _insert(padres_db, ab=1, ev=104.0, la=28.0, lsa=6)  # barrel
    _insert(padres_db, ab=2, ev=98.0, la=12.0, lsa=5)  # hard-hit, not barrel
    _insert(padres_db, ab=3, ev=80.0, la=5.0, lsa=2)  # soft
    _insert(padres_db, ab=4, ev=70.0, la=-5.0, lsa=1)  # soft
    ds = build_launch(padres_db, 1, 2024)
    assert ds is not None
    assert ds.card == "launch"
    assert ds.n == 4
    assert ds.hero is not None and ds.hero["value"] == "25.0%"  # 1 of 4
    assert "1 barrels" in ds.hero["context"]
    kinds = sorted(p.kind for p in ds.points if p.kind)
    assert kinds == ["barrel", "hard_hit", "soft", "soft"]
