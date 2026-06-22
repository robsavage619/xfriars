"""Tests for the deterministic live 'ask' interface."""

from __future__ import annotations

from padres_analytics.live_ask import answer_from_feed, match_player, participants


def _feed() -> dict:
    """A Live feed: King pitching to Machado, with a boxscore and score."""
    return {
        "gameData": {
            "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
            "teams": {"home": {"abbreviation": "SD"}, "away": {"abbreviation": "LAD"}},
        },
        "liveData": {
            "linescore": {
                "currentInning": 5,
                "inningHalf": "Top",
                "teams": {"home": {"runs": 2}, "away": {"runs": 1}},
            },
            "plays": {
                "allPlays": [
                    {
                        "atBatIndex": 0,
                        "about": {"inning": 5, "halfInning": "top"},
                        "matchup": {
                            "pitcher": {"id": 500, "fullName": "Michael King"},
                            "batter": {"id": 592518, "fullName": "Manny Machado"},
                        },
                        "playEvents": [
                            {
                                "isPitch": True,
                                "pitchNumber": 1,
                                "pitchData": {"startSpeed": 95.6},
                                "count": {"balls": 0, "strikes": 1},
                                "details": {
                                    "type": {"description": "Four-Seam Fastball"},
                                    "description": "Called Strike",
                                },
                            },
                            {
                                "isPitch": True,
                                "pitchNumber": 2,
                                "pitchData": {"startSpeed": 87.2},
                                "count": {"balls": 0, "strikes": 2},
                                "details": {
                                    "type": {"description": "Slider"},
                                    "description": "Swinging Strike",
                                },
                            },
                            {
                                "isPitch": True,
                                "pitchNumber": 3,
                                "pitchData": {"startSpeed": 96.0},
                                "count": {"balls": 0, "strikes": 2},
                                "details": {
                                    "type": {"description": "Four-Seam Fastball"},
                                    "description": "Foul",
                                },
                            },
                        ],
                    }
                ]
            },
            "boxscore": {
                "teams": {
                    "home": {
                        "players": {
                            "ID592518": {
                                "person": {"id": 592518, "fullName": "Manny Machado"},
                                "stats": {
                                    "batting": {
                                        "atBats": 3,
                                        "hits": 2,
                                        "homeRuns": 1,
                                        "baseOnBalls": 0,
                                        "strikeOuts": 1,
                                        "rbi": 2,
                                    }
                                },
                            }
                        }
                    },
                    "away": {
                        "players": {
                            "ID500": {
                                "person": {"id": 500, "fullName": "Michael King"},
                                "stats": {"pitching": {"pitchesThrown": 3}},
                            }
                        }
                    },
                }
            },
        },
    }


def test_pitcher_question() -> None:
    out = answer_from_feed("how is King throwing tonight", _feed())
    assert "Michael King tonight: 3 pitches, 1 whiff(s)" in out
    assert "Four-Seam Fastball 2" in out
    assert "Slider 1" in out
    assert "live · unofficial" in out


def test_batter_question() -> None:
    out = answer_from_feed("how's Machado looking at the plate", _feed())
    assert "Manny Machado tonight: 2-for-3, HR, 2 RBI, 1 K" in out
    assert "on 3 pitches" in out


def test_score_question_no_player() -> None:
    out = answer_from_feed("what's the score", _feed())
    assert out.startswith("LAD 1 @ SD 2 — Top 5")


def test_unknown_player_falls_back_to_state() -> None:
    out = answer_from_feed("how is Bogaerts doing", _feed())
    # Bogaerts isn't in this game -> game-state answer, not an error
    assert "LAD 1 @ SD 2" in out


def test_participants_and_match() -> None:
    people = participants(_feed())
    assert (500, "Michael King") in people
    assert match_player("talk about King", people) == 500
    assert match_player("nobody here", people) is None
