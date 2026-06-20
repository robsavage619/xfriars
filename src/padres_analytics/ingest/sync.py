"""Team-level DB refresh — the one pull that keeps the engine current.

Shared by ``pad sync`` and the app's Sync button so they never drift. Each step
is fault-isolated: a failure is recorded and the remaining steps still run, so a
flaky single source can't abort the whole refresh.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


class StepResult(NamedTuple):
    """Outcome of one ingest step."""

    name: str
    ok: bool
    detail: str


def run_sync(conn: duckdb.DuckDBPyConnection, season: int) -> list[StepResult]:
    """Refresh roster, standings, Statcast, player seasons, and game logs.

    Order matters: seasons are pulled before game logs (logs read the season
    hitter list). The roster pull carries IL status, which keeps the availability
    filter accurate.

    Args:
        conn: Write connection to padres.db (already initialized).
        season: Season year to refresh.

    Returns:
        One ``StepResult`` per step, in run order.
    """
    from padres_analytics.ingest.mlb_api import (
        ingest_game_logs,
        ingest_player_seasons,
        ingest_roster,
        ingest_standings,
    )
    from padres_analytics.ingest.statcast import ingest_statcast

    steps = [
        ("roster", lambda c: ingest_roster(c, season)),
        ("standings", lambda c: ingest_standings(c, season)),
        ("statcast", lambda c: ingest_statcast(c, season)),
        ("player-seasons", lambda c: ingest_player_seasons(c, season - 6, season)),
        ("game-logs", lambda c: ingest_game_logs(c, season)),
    ]
    results: list[StepResult] = []
    for name, fn in steps:
        try:
            n = fn(conn)
            results.append(StepResult(name, True, str(n) if isinstance(n, int) else "ok"))
        except Exception as exc:
            logger.warning("sync step %s failed: %s", name, exc)
            results.append(StepResult(name, False, str(exc)))
    return results
