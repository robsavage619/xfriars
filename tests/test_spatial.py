"""Tests for the spatial spray builder — coord transform, filters, harness."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from padres_analytics.detect.candidates import SpatialDataset
from padres_analytics.detect.spatial import build_hr_spray, build_spray

if TYPE_CHECKING:
    import duckdb

_COLS = (
    "player_id, player_name, season, game_type, game_date, game_pk, at_bat_number, "
    "pitch_number, events, bb_type, description, stand, p_throws, hc_x, hc_y, "
    "launch_speed, launch_angle, hit_distance_sc, estimated_woba, ingested_at"
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
) -> None:
    conn.execute(
        f"INSERT INTO statcast_batted_balls ({_COLS}) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
            95.0,
            22.0,
            dist,
            0.55,
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
