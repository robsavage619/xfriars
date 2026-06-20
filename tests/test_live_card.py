"""Tests for the live story card — Padres-correct attribution + audited render."""

from __future__ import annotations

from padres_analytics.live_card import live_angle
from padres_analytics.render.story_infographic import audit_rendered, compose

PADRES = 135
RANGERS = 140


def _pitch(pid: int, name: str, ptype: str, velo: float, result: str) -> dict:
    return {
        "atBatIndex": 0,
        "about": {"inning": 3, "halfInning": "bottom"},
        "matchup": {
            "pitcher": {"id": pid, "fullName": name},
            "batter": {"id": 1, "fullName": "Some Batter"},
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


def _feed() -> dict:
    """Padres (away) at Rangers (home). King (Padre) pitches; Gore (Rangers) is busier.

    The card must feature King — the Padre — not the busiest pitcher (Gore).
    """
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
                "currentInning": 3,
                "inningHalf": "Bottom",
                "teams": {"home": {"runs": 0}, "away": {"runs": 1}},
            },
            "plays": {
                "allPlays": [
                    _pitch(500, "Michael King", "Four-Seam Fastball", 95.0, "Called Strike"),
                    _pitch(500, "Michael King", "Slider", 87.0, "Swinging Strike"),
                    _pitch(700, "MacKenzie Gore", "Four-Seam Fastball", 96.0, "Ball"),
                    _pitch(700, "MacKenzie Gore", "Curveball", 81.0, "Ball"),
                    _pitch(700, "MacKenzie Gore", "Curveball", 82.0, "Foul"),
                ]
            },
            "boxscore": {
                "teams": {
                    "away": {
                        "pitchers": [500],
                        "players": {
                            "ID500": {
                                "person": {"id": 500, "fullName": "Michael King"},
                                "stats": {
                                    "pitching": {
                                        "inningsPitched": "2.0",
                                        "hits": 1,
                                        "runs": 0,
                                        "strikeOuts": 4,
                                        "baseOnBalls": 1,
                                    }
                                },
                            }
                        },
                    },
                    "home": {
                        "pitchers": [700],
                        "players": {
                            "ID700": {
                                "person": {"id": 700, "fullName": "MacKenzie Gore"},
                                "stats": {"pitching": {"inningsPitched": "3.0"}},
                            }
                        },
                    },
                }
            },
        },
    }


def test_features_the_padre_not_the_busiest() -> None:
    """King (the Padre) is featured even though Gore threw more pitches."""
    angle = live_angle(_feed())
    assert angle is not None
    assert angle.key == "live_pitcher"
    assert "Michael King" in angle.subject
    assert "MacKenzie Gore" not in angle.subject
    assert angle.subject == "Michael King"  # short kicker (name only)
    assert "vs TEX" in angle.headline  # opponent labeled in the subhead
    # CSW-led: 1 called strike + 1 whiff on 2 pitches = 100% CSW
    assert "100% CSW" in angle.headline
    assert "2 pitches" in angle.headline
    assert "1 whiff" in angle.headline
    assert [p.kind for p in angle.panels] == ["hero", "pitchmix", "trend", "statline"]


def test_headshot_injected_and_rendered() -> None:
    """A photo resolver puts a data URI on the angle and an <image> on the card."""
    uri = "data:image/png;base64,AAAA"
    angle = live_angle(_feed(), photo_resolver=lambda _pid: uri)
    assert angle is not None
    assert angle.headshot == uri
    svg = compose(angle)
    assert "<image" in svg and uri in svg
    # without a resolver, no photo and the wordmark stays
    plain = live_angle(_feed())
    assert plain is not None and plain.headshot is None
    assert "<image" not in compose(plain)


def test_compose_audits_clean() -> None:
    angle = live_angle(_feed())
    assert angle is not None
    svg = compose(angle)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "MICHAEL KING" in svg and "live · unofficial" in svg  # uppercased in the kicker
    assert audit_rendered(angle, svg) == []


def test_none_when_padres_not_in_game() -> None:
    feed = _feed()
    feed["gameData"]["teams"]["away"]["id"] = 999
    feed["gameData"]["teams"]["home"]["id"] = 998
    assert live_angle(feed) is None


def test_none_when_padres_pitcher_hasnt_thrown() -> None:
    feed = _feed()
    feed["liveData"]["boxscore"]["teams"]["away"]["pitchers"] = []
    assert live_angle(feed) is None
