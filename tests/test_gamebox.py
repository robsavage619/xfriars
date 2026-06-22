"""game_box ingest: schedule parsing (Final filter) + the upsert writer."""

from __future__ import annotations

from typing import Any

import duckdb

from padres_analytics.ingest import mlb_api
from padres_analytics.ingest.mlb_api import MlbStatsClient, ingest_gamebox

# ── game_scores parsing ─────────────────────────────────────────────────────────


def _schedule_payload() -> dict[str, Any]:
    """Two games: one Final (settled score), one in-progress (no score yet)."""
    return {
        "dates": [
            {
                "date": "2026-06-20",
                "games": [
                    {
                        "gamePk": 778001,
                        "status": {"abstractGameState": "Final"},
                        "teams": {
                            "home": {"team": {"id": 135}},
                            "away": {"team": {"id": 119}},
                        },
                        "linescore": {
                            "currentInning": 9,
                            "teams": {
                                "home": {"runs": 5},
                                "away": {"runs": 3},
                            },
                        },
                        "decisions": {
                            "winner": {"id": 600001},
                            "loser": {"id": 600002},
                            "save": {"id": 600003},
                        },
                    }
                ],
            },
            {
                "date": "2026-06-21",
                "games": [
                    {
                        "gamePk": 778002,
                        "status": {"abstractGameState": "Live"},
                        "teams": {
                            "home": {"team": {"id": 135}},
                            "away": {"team": {"id": 119}},
                        },
                        "linescore": {"currentInning": 4, "teams": {}},
                    }
                ],
            },
        ]
    }


def test_game_scores_keeps_only_finals(monkeypatch) -> None:
    client = MlbStatsClient(politeness_delay=0.0)
    monkeypatch.setattr(client, "_get", lambda *a, **k: _schedule_payload())

    games = client.game_scores(season=2026)

    assert len(games) == 1
    g = games[0]
    assert g["game_pk"] == 778001
    assert g["game_date"] == "2026-06-20"
    assert g["home_team_id"] == 135
    assert g["away_team_id"] == 119
    assert g["home_score"] == 5
    assert g["away_score"] == 3
    assert g["innings"] == 9
    assert g["winning_pitcher_id"] == 600001
    assert g["losing_pitcher_id"] == 600002
    assert g["save_pitcher_id"] == 600003


# ── ingest_gamebox writer ───────────────────────────────────────────────────────


class _FakeClient:
    """Stub MlbStatsClient returning canned game scores."""

    def __init__(self, games: list[dict[str, Any]]) -> None:
        self._games = games

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def game_scores(self, *_a: object, **_k: object) -> list[dict[str, Any]]:
        return self._games


def _game(game_pk: int, home_score: int, away_score: int) -> dict[str, Any]:
    return {
        "game_pk": game_pk,
        "game_date": "2026-06-20",
        "home_team_id": 135,
        "away_team_id": 119,
        "home_score": home_score,
        "away_score": away_score,
        "innings": 9,
        "winning_pitcher_id": 600001,
        "losing_pitcher_id": 600002,
        "save_pitcher_id": None,
    }


def test_ingest_gamebox_writes_rows(padres_db: duckdb.DuckDBPyConnection, monkeypatch) -> None:
    monkeypatch.setattr(
        mlb_api, "MlbStatsClient", lambda *a, **k: _FakeClient([_game(778001, 5, 3)])
    )

    n = ingest_gamebox(padres_db, 2026)

    assert n == 1
    row = padres_db.execute(
        "SELECT home_score, away_score, innings, save_pitcher_id FROM game_box WHERE game_pk = ?",
        [778001],
    ).fetchone()
    assert row == (5, 3, 9, None)


def test_ingest_gamebox_upserts_on_rerun(padres_db: duckdb.DuckDBPyConnection, monkeypatch) -> None:
    monkeypatch.setattr(
        mlb_api, "MlbStatsClient", lambda *a, **k: _FakeClient([_game(778001, 5, 3)])
    )
    ingest_gamebox(padres_db, 2026)

    # Re-run with a corrected score for the same game_pk.
    monkeypatch.setattr(
        mlb_api, "MlbStatsClient", lambda *a, **k: _FakeClient([_game(778001, 6, 3)])
    )
    ingest_gamebox(padres_db, 2026)

    rows = padres_db.execute(
        "SELECT home_score FROM game_box WHERE game_pk = ?", [778001]
    ).fetchall()
    assert rows == [(6,)]  # one row, updated in place
