"""Tests for live moment discovery — gating, ranking, and the hitter card."""

from __future__ import annotations

from padres_analytics.live_moments import (
    discover_live,
    hitter_moment,
    pitcher_moment,
)
from padres_analytics.render.story_infographic import audit_rendered, compose

PADRES = 135
RANGERS = 140


def _pitch(pid: int, name: str, ptype: str, velo: float, result: str) -> dict:
    return {
        "atBatIndex": 0,
        "about": {"inning": 6, "halfInning": "bottom"},
        "matchup": {
            "pitcher": {"id": pid, "fullName": name},
            "batter": {"id": 900, "fullName": "Foe"},
        },
        "playEvents": [
            {
                "isPitch": True,
                "pitchNumber": 1,
                "pitchData": {"startSpeed": velo},
                "details": {"type": {"description": ptype}, "description": result},
            }
        ],
    }


def _feed(*, pitcher_pitches: int = 30, pitcher_csw_each: bool = True, padres_hr: int = 0) -> dict:
    """Padres (away) at Rangers. King throws `pitcher_pitches`; optionally a Padre homers."""
    plays = []
    for i in range(pitcher_pitches):
        # alternate called/swinging strikes -> high CSW when csw_each, else mostly balls
        result = ("Swinging Strike" if i % 2 else "Called Strike") if pitcher_csw_each else "Ball"
        plays.append(_pitch(500, "Michael King", "Slider", 88.0, result))

    # Both the Padres pitcher (King) and hitter (Tatis) are on the AWAY side.
    tatis_batting = {
        "atBats": 4,
        "hits": 2 if padres_hr else 1,
        "homeRuns": padres_hr,
        "rbi": 3 if padres_hr else 0,
        "baseOnBalls": 0,
        "strikeOuts": 1,
    }
    box_padres_players = {
        "ID500": {
            "person": {"id": 500, "fullName": "Michael King"},
            "stats": {
                "pitching": {
                    "inningsPitched": "6.0",
                    "hits": 2,
                    "runs": 0,
                    "strikeOuts": 9,
                    "baseOnBalls": 1,
                }
            },
        },
        "ID665487": {
            "person": {"id": 665487, "fullName": "Fernando Tatis Jr."},
            "stats": {"batting": tatis_batting},
        },
    }
    if padres_hr:
        # a batted ball with exit velo for the hitter's hardest-hit
        plays.append(
            {
                "atBatIndex": 99,
                "about": {"inning": 5, "halfInning": "top"},
                "matchup": {
                    "batter": {"id": 665487, "fullName": "Fernando Tatis Jr."},
                    "pitcher": {"id": 700, "fullName": "Opp"},
                },
                "playEvents": [
                    {
                        "isPitch": True,
                        "pitchNumber": 1,
                        "pitchData": {"startSpeed": 92.0},
                        "hitData": {"launchSpeed": 110.4},
                        "details": {
                            "type": {"description": "Four-Seam Fastball"},
                            "description": "In play, run(s)",
                            "isInPlay": True,
                        },
                    }
                ],
            }
        )

    return {
        "gameData": {
            "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
            "teams": {
                "home": {"id": RANGERS, "abbreviation": "TEX"},
                "away": {"id": PADRES, "abbreviation": "SD"},
            },
        },
        "liveData": {
            "linescore": {
                "currentInning": 6,
                "inningHalf": "Bottom",
                "teams": {"home": {"runs": 0}, "away": {"runs": 4}},
            },
            "plays": {"allPlays": plays},
            "boxscore": {
                "teams": {
                    "home": {"pitchers": [700], "players": {}},
                    "away": {"pitchers": [500], "players": box_padres_players},
                }
            },
        },
    }


def test_pitcher_gate_blocks_early_outing() -> None:
    """Fewer than the minimum pitches → no pitcher card yet."""
    assert pitcher_moment(_feed(pitcher_pitches=10)) is None


def test_pitcher_gate_blocks_non_dominant() -> None:
    """Enough pitches but not dominant (all balls → 0% CSW, and K from the line is 9...)."""
    # K=9 from the boxscore line clears the K gate, so this DOES fire; verify a truly
    # weak line (low K, low CSW) does not by zeroing strikeouts.
    feed = _feed(pitcher_pitches=30, pitcher_csw_each=False)
    feed["liveData"]["boxscore"]["teams"]["away"]["players"]["ID500"]["stats"]["pitching"][
        "strikeOuts"
    ] = 2
    assert pitcher_moment(feed) is None


def test_pitcher_moment_fires_when_dominant() -> None:
    angle = pitcher_moment(_feed(pitcher_pitches=30, pitcher_csw_each=True))
    assert angle is not None
    assert angle.key == "live_pitcher"
    assert angle.interest > 0


def test_hitter_moment_fires_on_homer() -> None:
    angle = hitter_moment(_feed(padres_hr=1))
    assert angle is not None
    assert angle.key == "live_hitter"
    assert "Fernando Tatis Jr." in angle.subject
    assert angle.title == "WENT DEEP"
    assert "1 HR" in angle.headline and "3 RBI" in angle.headline
    svg = compose(angle)
    assert "110 mph" in svg  # hardest-hit exit velo surfaced
    assert audit_rendered(angle, svg) == []


def test_no_hitter_without_a_real_night() -> None:
    # 1-for-4, no HR, no RBI -> not card-worthy
    assert hitter_moment(_feed(padres_hr=0)) is None


def test_discover_ranks_homer_over_solid_start() -> None:
    """A multi-RBI homer outranks a strong-but-ordinary start in the same game."""
    angles = discover_live(_feed(pitcher_pitches=30, pitcher_csw_each=True, padres_hr=2))
    assert angles  # both fire
    assert angles[0].key == "live_hitter"  # 2 HR night wins
