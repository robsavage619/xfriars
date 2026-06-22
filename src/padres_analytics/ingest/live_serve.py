"""Live serve daemon — auto-watch a game with state-aware poll cadence.

A thin loop over :func:`padres_analytics.ingest.live_poll.poll_once` that polls
faster once a game is in progress and slower while it's still in Preview. It
stops the moment the game goes Final (recording that final poll) or after a
caller-supplied cycle cap.

Scheduling
----------
Run ``pad live serve`` once per day a bit before first pitch; the daemon idles
on the slow Preview cadence until the game starts, ramps up, and exits at Final.

crontab — fire daily at 12:30 (adjust to local first-pitch minus a buffer);
``cd`` into the repo so ``uv`` resolves the project::

    30 12 * * * cd /path/to/padres-analytics && \
        /usr/bin/env uv run pad live serve >> /tmp/pad-live-serve.log 2>&1

macOS launchd — drop a plist at
``~/Library/LaunchAgents/com.xfriars.live-serve.plist`` and
``launchctl load`` it. Use ``StartCalendarInterval`` (Hour/Minute keys) for the
same daily trigger; set ``WorkingDirectory`` to the repo root and
``ProgramArguments`` to ``["/usr/bin/env", "uv", "run", "pad", "live",
"serve"]``. launchd's calendar trigger is the daemon-friendly equivalent of the
cron line above and survives logout.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from padres_analytics.config import PADRES_TEAM_ID
from padres_analytics.ingest.live_poll import FeedClient, poll_once
from padres_analytics.live import resolve_game_pk

if TYPE_CHECKING:
    import duckdb

    from padres_analytics.ingest.mlb_api import MlbStatsClient

logger = logging.getLogger(__name__)

DEFAULT_PREVIEW_INTERVAL = 60.0
DEFAULT_LIVE_INTERVAL = 10.0

_SLOW_STATES = frozenset({"Preview", "Unknown"})


def serve(
    client: FeedClient,
    conn: duckdb.DuckDBPyConnection,
    game_pk: int,
    *,
    preview_interval: float = DEFAULT_PREVIEW_INTERVAL,
    live_interval: float = DEFAULT_LIVE_INTERVAL,
    sleeper: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> int:
    """Poll a game with a state-aware cadence until it goes Final.

    Polls via :func:`poll_once`. While the game is in Preview/Unknown the loop
    sleeps ``preview_interval`` between polls; once Live it sleeps
    ``live_interval``. The loop stops after the first Final poll (no sleep
    follows it) or once ``max_cycles`` polls have run.

    Args:
        client: Open MLB Stats client exposing ``live_feed``.
        conn: Write connection to padres.db.
        game_pk: Game to watch.
        preview_interval: Seconds between polls while Preview/Unknown.
        live_interval: Seconds between polls while Live.
        sleeper: Sleep function (injected for testing).
        max_cycles: Stop after this many polls (None = until Final).

    Returns:
        Number of polls performed.
    """
    polls = 0
    while True:
        snapshot, _ = poll_once(client, conn, game_pk)
        polls += 1
        if snapshot.state == "Final":
            logger.info("game=%d is Final after %d poll(s)", game_pk, polls)
            break
        if max_cycles is not None and polls >= max_cycles:
            logger.info("game=%d reached max_cycles=%d", game_pk, max_cycles)
            break
        interval = preview_interval if snapshot.state in _SLOW_STATES else live_interval
        logger.info("game=%d state=%s sleeping %.1fs", game_pk, snapshot.state, interval)
        sleeper(interval)
    return polls


def serve_today(
    client: MlbStatsClient,
    conn: duckdb.DuckDBPyConnection,
    date: str,
    *,
    team_id: int = PADRES_TEAM_ID,
    **kw: object,
) -> int | None:
    """Resolve the team's game for ``date`` and :func:`serve` it.

    Args:
        client: Open MLB Stats client.
        conn: Write connection to padres.db.
        date: ISO date (YYYY-MM-DD) to resolve the game on.
        team_id: MLB team ID.
        **kw: Forwarded to :func:`serve` (e.g. ``preview_interval``).

    Returns:
        Number of polls performed, or ``None`` if there is no game on the date.
    """
    game_pk = resolve_game_pk(client, date, team_id=team_id)
    if game_pk is None:
        logger.info("no game for team=%d on %s", team_id, date)
        return None
    return serve(client, conn, game_pk, **kw)  # type: ignore[arg-type]
