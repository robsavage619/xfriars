"""Tests for the live GUMBO snapshot parser + game resolver (no network)."""

from __future__ import annotations

from padres_analytics.live import BatterLine, parse_feed, pick_game


def _feed() -> dict:
    """A minimal GUMBO feed/live payload mid-at-bat."""
    return {
        "gamePk": 777001,
        "metaData": {"timeStamp": "20260620_023145"},
        "gameData": {
            "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
            "teams": {
                "home": {"abbreviation": "SD"},
                "away": {"abbreviation": "LAD"},
            },
        },
        "liveData": {
            "linescore": {
                "currentInning": 6,
                "inningHalf": "Bottom",
                "teams": {"home": {"runs": 3}, "away": {"runs": 5}},
            },
            "plays": {
                "currentPlay": {
                    "matchup": {
                        "batter": {"id": 592518, "fullName": "Manny Machado"},
                        "pitcher": {"id": 111, "fullName": "Some Reliever"},
                    },
                    "count": {"balls": 1, "strikes": 2, "outs": 1},
                    "playEvents": [
                        {
                            "isPitch": True,
                            "pitchData": {"startSpeed": 94.3},
                            "details": {
                                "type": {"description": "Four-Seam Fastball"},
                                "description": "Ball",
                            },
                        },
                        {"isPitch": False, "details": {"description": "Mound Visit"}},
                        {
                            "isPitch": True,
                            "pitchData": {"startSpeed": 86.1},
                            "details": {
                                "type": {"description": "Slider"},
                                "description": "Swinging Strike",
                            },
                        },
                    ],
                }
            },
            "boxscore": {
                "teams": {
                    "home": {
                        "players": {
                            "ID592518": {
                                "stats": {
                                    "batting": {
                                        "atBats": 3,
                                        "hits": 2,
                                        "homeRuns": 1,
                                        "baseOnBalls": 0,
                                        "strikeOuts": 1,
                                        "rbi": 2,
                                    }
                                }
                            }
                        }
                    },
                    "away": {"players": {}},
                }
            },
        },
    }


def test_parse_feed_last_pitch_and_line() -> None:
    snap = parse_feed(_feed())
    assert snap.is_live
    assert snap.scoreline() == "LAD 5 @ SD 3"
    assert (snap.half, snap.inning) == ("Bottom", 6)

    lp = snap.last_pitch
    assert lp is not None
    # last *pitch* event wins, skipping the non-pitch mound visit
    assert lp.pitch_type == "Slider"
    assert lp.velo == 86.1
    assert lp.result == "Swinging Strike"
    assert (lp.balls, lp.strikes, lp.outs) == (1, 2, 1)
    assert "86.1 mph Slider" in lp.describe()

    bl = snap.batter_line
    assert bl is not None
    assert bl.name == "Manny Machado"
    assert bl.line() == "2-for-3, HR, 2 RBI, 1 K"


def test_parse_feed_preview_has_no_pitch() -> None:
    """A game with no plays yet parses cleanly with None pitch/line."""
    feed = {
        "gameData": {
            "status": {"abstractGameState": "Preview", "detailedState": "Warmup"},
            "teams": {"home": {"abbreviation": "SD"}, "away": {"abbreviation": "SF"}},
        },
        "liveData": {"linescore": {}, "plays": {"currentPlay": {}}, "boxscore": {}},
    }
    snap = parse_feed(feed)
    assert not snap.is_live
    assert snap.state == "Preview"
    assert snap.last_pitch is None
    assert snap.batter_line is None


def test_pick_game_prefers_live() -> None:
    games = [
        {"game_pk": 1, "abstract_state": "Final", "game_datetime": "2026-06-20T20:10:00Z"},
        {"game_pk": 2, "abstract_state": "Live", "game_datetime": "2026-06-20T23:10:00Z"},
        {"game_pk": 3, "abstract_state": "Preview", "game_datetime": "2026-06-21T02:10:00Z"},
    ]
    chosen = pick_game(games)
    assert chosen is not None
    assert chosen["game_pk"] == 2
    assert pick_game([]) is None


def test_batter_line_formatting() -> None:
    assert BatterLine("X", ab=4, h=0, hr=0, bb=1, k=2, rbi=0).line() == "0-for-4, 1 BB, 2 K"
    assert BatterLine("Y", ab=2, h=2, hr=2, bb=0, k=0, rbi=3).line() == "2-for-2, 2 HR, 3 RBI"
