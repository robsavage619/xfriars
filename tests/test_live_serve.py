"""Tests for the state-aware serve daemon and its today-resolver."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from padres_analytics.ingest.live_serve import serve, serve_today

if TYPE_CHECKING:
    import duckdb


def _feed(state: str = "Live") -> dict:
    """A minimal GUMBO feed carrying the abstract game state."""
    return {
        "gamePk": 822885,
        "gameData": {"status": {"abstractGameState": state, "detailedState": state}},
        "liveData": {"plays": {"allPlays": []}},
    }


class _FakeClient:
    """Yields a scripted sequence of states, repeating the last forever."""

    def __init__(self, states: list[str]) -> None:
        self._states = states
        self._i = 0

    def live_feed(self, game_pk: int) -> dict:
        state = self._states[min(self._i, len(self._states) - 1)]
        self._i += 1
        return _feed(state)


class _NoGameClient:
    """A client whose schedule lookup returns no games for the date."""

    def live_games(self, date: str, *, team_id: int) -> list[dict[str, Any]]:
        return []

    def live_feed(self, game_pk: int) -> dict:
        raise AssertionError("live_feed must not be called when there is no game")


def test_serve_cadence_and_stops_on_final(padres_db: duckdb.DuckDBPyConnection) -> None:
    """2 Preview + 3 Live + 1 Final: cadence tracks state, no sleep after Final."""
    slept: list[float] = []
    client = _FakeClient(["Preview", "Preview", "Live", "Live", "Live", "Final"])
    polls = serve(
        client,
        padres_db,
        822885,
        preview_interval=60.0,
        live_interval=10.0,
        sleeper=slept.append,
    )
    assert polls == 6
    # one sleep per non-final poll: 2 preview cadences then 3 live cadences.
    assert slept == [60.0, 60.0, 10.0, 10.0, 10.0]


def test_serve_respects_max_cycles(padres_db: duckdb.DuckDBPyConnection) -> None:
    slept: list[float] = []
    client = _FakeClient(["Live"])  # never goes Final
    polls = serve(
        client,
        padres_db,
        822885,
        live_interval=10.0,
        sleeper=slept.append,
        max_cycles=3,
    )
    assert polls == 3
    assert slept == [10.0, 10.0]  # no sleep after the capped final poll


def test_serve_today_returns_none_without_game(padres_db: duckdb.DuckDBPyConnection) -> None:
    result = serve_today(_NoGameClient(), padres_db, "2026-06-20")  # type: ignore[arg-type]
    assert result is None
