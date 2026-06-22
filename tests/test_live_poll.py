"""Tests for live pitch extraction, idempotent persistence, and the watch loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from padres_analytics.ingest.live_poll import poll_once, upsert_game_pitches, watch
from padres_analytics.live import iter_pitches

if TYPE_CHECKING:
    import duckdb


def _feed(state: str = "Live") -> dict:
    """A GUMBO feed with two plate appearances and several pitches."""
    return {
        "gamePk": 822885,
        "gameData": {"status": {"abstractGameState": state, "detailedState": state}},
        "liveData": {
            "plays": {
                "allPlays": [
                    {
                        "atBatIndex": 0,
                        "about": {"inning": 1, "halfInning": "top"},
                        "matchup": {
                            "pitcher": {"id": 500, "fullName": "Star Starter"},
                            "batter": {"id": 600, "fullName": "Leadoff Guy"},
                        },
                        "playEvents": [
                            {
                                "isPitch": True,
                                "pitchNumber": 1,
                                "pitchData": {"startSpeed": 95.1},
                                "count": {"balls": 0, "strikes": 1},
                                "details": {
                                    "type": {"description": "Four-Seam Fastball"},
                                    "description": "Called Strike",
                                    "isInPlay": False,
                                },
                            },
                            {
                                "isPitch": True,
                                "pitchNumber": 2,
                                "pitchData": {"startSpeed": 86.4},
                                "count": {"balls": 0, "strikes": 2},
                                "details": {
                                    "type": {"description": "Slider", "code": "SL"},
                                    "description": "Swinging Strike",
                                    "isInPlay": False,
                                },
                            },
                            {"isPitch": False, "details": {"description": "Step Off"}},
                            {
                                "isPitch": True,
                                "pitchNumber": 3,
                                "pitchData": {"startSpeed": 87.0},
                                "count": {"balls": 0, "strikes": 3},
                                "details": {
                                    "type": {"description": "Slider", "code": "SL"},
                                    "description": "Swinging Strike",
                                    "isInPlay": False,
                                },
                            },
                        ],
                    },
                    {
                        "atBatIndex": 1,
                        "about": {"inning": 1, "halfInning": "top"},
                        "matchup": {
                            "pitcher": {"id": 500, "fullName": "Star Starter"},
                            "batter": {"id": 601, "fullName": "Two Hitter"},
                        },
                        "playEvents": [
                            {
                                "isPitch": True,
                                "pitchNumber": 1,
                                "pitchData": {"startSpeed": 94.0},
                                "count": {"balls": 0, "strikes": 0},
                                "details": {
                                    "type": {"description": "Four-Seam Fastball"},
                                    "description": "In play, out(s)",
                                    "isInPlay": True,
                                },
                            },
                        ],
                    },
                ]
            }
        },
    }


def test_iter_pitches_extracts_all_with_flags() -> None:
    rows = iter_pitches(_feed())
    assert len(rows) == 4  # the non-pitch "Step Off" is skipped
    assert all(r.game_pk == 822885 for r in rows)

    whiffs = [r for r in rows if r.is_whiff]
    assert len(whiffs) == 2  # two swinging strikes
    assert {r.pitch_type for r in whiffs} == {"Slider"}

    in_play = [r for r in rows if r.in_play]
    assert len(in_play) == 1
    assert in_play[0].is_swing

    called = next(r for r in rows if r.result == "Called Strike")
    assert not called.is_swing and not called.is_whiff


def test_upsert_is_idempotent(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Re-polling the same feed keeps one row per (game, at-bat, pitch)."""
    rows = iter_pitches(_feed())
    upsert_game_pitches(padres_db, 822885, rows)
    upsert_game_pitches(padres_db, 822885, rows)  # second poll, same pitches
    row = padres_db.execute("SELECT count(*) FROM live_pitches WHERE game_pk = 822885").fetchone()
    assert row is not None and row[0] == 4
    # pitch mix is queryable for running splits
    mix = dict(
        padres_db.execute(
            "SELECT pitch_type, count(*) FROM live_pitches WHERE pitcher_id = 500 "
            "GROUP BY pitch_type"
        ).fetchall()
    )
    assert mix == {"Four-Seam Fastball": 2, "Slider": 2}


class _FakeClient:
    """Returns a Live feed for the first N polls, then Final."""

    def __init__(self, live_polls: int) -> None:
        self._left = live_polls

    def live_feed(self, game_pk: int) -> dict:
        if self._left > 0:
            self._left -= 1
            return _feed("Live")
        return _feed("Final")


def test_watch_stops_on_final(padres_db: duckdb.DuckDBPyConnection) -> None:
    slept: list[float] = []
    polls = watch(
        _FakeClient(live_polls=2),
        padres_db,
        822885,
        interval=5.0,
        sleeper=slept.append,
    )
    assert polls == 3  # two Live + one Final
    assert slept == [5.0, 5.0]  # no sleep after the Final poll


def test_watch_respects_max_polls(padres_db: duckdb.DuckDBPyConnection) -> None:
    polls = watch(
        _FakeClient(live_polls=99), padres_db, 822885, max_polls=2, sleeper=lambda _: None
    )
    assert polls == 2


def test_poll_once_persists(padres_db: duckdb.DuckDBPyConnection) -> None:
    snap, n = poll_once(_FakeClient(live_polls=1), padres_db, 822885)
    assert snap.state == "Live"
    assert n == 4
