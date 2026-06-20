"""Tests for the network-backed live 'ask' intents (matchup, RISP)."""

from __future__ import annotations

from typing import Any

from padres_analytics.live_ask import answer_with_client


class _FakeClient:
    """Stand-in for MlbStatsClient exposing only the lookups under test."""

    def vs_pitcher(self, batter_id: int, pitcher_id: int, season: int) -> dict[str, Any]:
        return {"ab": 11, "h": 4, "hr": 1, "bb": 2, "k": 3, "avg": ".364"}

    def team_risp(self, team_id: int, season: int) -> dict[str, Any]:
        return {"avg": ".246", "ab": 114, "h": 28}


def _feed() -> dict[str, Any]:
    """A Live feed: Buehler pitching to Machado, with boxscore + plays."""
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
                            "pitcher": {"id": 621111, "fullName": "Walker Buehler"},
                            "batter": {"id": 592518, "fullName": "Manny Machado"},
                        },
                        "playEvents": [],
                    }
                ]
            },
            "boxscore": {
                "teams": {
                    "home": {
                        "players": {
                            "ID592518": {
                                "person": {"id": 592518, "fullName": "Manny Machado"},
                            }
                        }
                    },
                    "away": {
                        "players": {
                            "ID621111": {
                                "person": {"id": 621111, "fullName": "Walker Buehler"},
                            }
                        }
                    },
                }
            },
        },
    }


def test_matchup_intent() -> None:
    out = answer_with_client("how has Machado done against Buehler", _feed(), _FakeClient(), 2026)
    expected = (
        "Manny Machado vs Walker Buehler: 4-for-11, 1 HR, 2 BB, 3 K (2026)  ·  live · unofficial"
    )
    assert out == expected


def test_risp_intent() -> None:
    out = answer_with_client("how are they doing with RISP", _feed(), _FakeClient(), 2026)
    assert out == "Padres with RISP: .246 (28-for-114), 2026  ·  live · unofficial"


def test_plain_question_delegates_to_feed() -> None:
    out = answer_with_client("what's the score", _feed(), _FakeClient(), 2026)
    assert out.startswith("LAD 1 @ SD 2 — Top 5")


def test_matchup_empty_data() -> None:
    class _Empty(_FakeClient):
        def vs_pitcher(self, batter_id: int, pitcher_id: int, season: int) -> dict[str, Any]:
            return {}

    out = answer_with_client("Machado against Buehler", _feed(), _Empty(), 2026)
    expected = "No prior matchup data for Manny Machado vs Walker Buehler.  ·  live · unofficial"
    assert out == expected
