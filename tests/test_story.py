"""Tests for the story-card composer (multi-panel infographic)."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from padres_analytics.detect.story import build_funk_story

if TYPE_CHECKING:
    import duckdb

_NOW = datetime(2026, 6, 20, 0, 0, 0)


def _create_standings(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the standings table (normally made by the ingest, not the base schema)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS standings (team_id INTEGER, team_abbr VARCHAR, "
        "team_name VARCHAR, division_id INTEGER, season INTEGER, wins INTEGER, "
        "losses INTEGER, win_pct DOUBLE, games_back VARCHAR, source VARCHAR, "
        "ingested_at TIMESTAMP)"
    )


def _standings(
    conn: duckdb.DuckDBPyConnection, name: str, tid: int, w: int, lo: int, gb: str
) -> None:
    conn.execute(
        "INSERT INTO standings (team_id, team_abbr, team_name, division_id, season, "
        "wins, losses, win_pct, games_back, source, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [tid, name[:3].upper(), name, 203, 2026, w, lo, w / (w + lo), gb, "test", _NOW],
    )


def _pctile(conn: duckdb.DuckDBPyConnection, pid: int, name: str, **vals: float) -> None:
    cols = ["player_id", "player_name", "year", *vals.keys(), "ingested_at"]
    params = [pid, name, 2026, *vals.values(), _NOW]
    placeholders = ",".join("?" * len(cols))
    conn.execute(
        f"INSERT INTO statcast_batter_percentile_ranks ({','.join(cols)}) VALUES ({placeholders})",
        params,
    )


def test_funk_story_composes(padres_db: duckdb.DuckDBPyConnection) -> None:
    """The funk story pulls the gap from standings and panels from percentiles."""
    _create_standings(padres_db)
    _standings(padres_db, "Padres", 135, 38, 36, "10.0")
    _standings(padres_db, "Dodgers", 119, 48, 26, "-")
    _pctile(padres_db, 592518, "Machado, Manny", xwoba=34.0)
    _pctile(padres_db, 665487, "Tatis Jr., Fernando", hard_hit_percent=96.0)

    card = build_funk_story(padres_db, 2026, as_of=date(2026, 6, 20))
    assert card is not None
    assert card.kind == "story"
    assert card.hero is not None
    assert card.hero["value"] == "10"
    assert "behind Dodgers" in card.hero["context"]

    by_name = {b.label: b for b in card.blocks}
    assert by_name["Manny Machado"].tone == "bad"
    assert by_name["Manny Machado"].value == "34th"
    assert by_name["Fernando Tatis Jr."].tone == "good"
    assert by_name["Fernando Tatis Jr."].percentile == 96


def test_funk_story_none_without_standings(padres_db: duckdb.DuckDBPyConnection) -> None:
    """No Padres standings row → no story."""
    assert build_funk_story(padres_db, 2026) is None


def test_compose_embeds_real_logo() -> None:
    """Hard gate: every composed card carries the real xFriars logo <image>, never text."""
    from padres_analytics.detect.angles import PanelSpec, StoryAngle
    from padres_analytics.render.story_infographic import compose, xfriars_logo_uri

    uri = xfriars_logo_uri()
    assert uri.startswith("data:image/png;base64,")

    angle = StoryAngle(
        key="t",
        subject="THE OFFENSE",
        title="IT'S NOT THE COACH",
        headline="h",
        thesis="t",
        direction="flat",
        effect=0.0,
        reliability=0.5,
        interest=0.0,
        confidence="moderate",
        as_of=date(2026, 6, 20),
        panels=[PanelSpec("hero", {"value": 0, "label": "x", "context": "y"})],
    )
    svg = compose(angle)
    assert uri in svg  # the real logo, embedded
    assert ">xFriars<" not in svg  # never the text wordmark
