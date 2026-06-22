"""Live pitch poller — persist the GUMBO feed to ``live_pitches`` during a game.

A poll fetches the full feed (``allPlays`` is cumulative), parses every pitch,
and upserts them keyed on ``(game_pk, at_bat_index, pitch_number)`` so repeated
polls are idempotent. ``watch`` loops until the game goes Final.

The table is UNOFFICIAL and per-game by construction; nothing here writes to the
season/skill tables. Pitch types and velo are preliminary and get revised.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from padres_analytics.live import LiveSnapshot, PitchRow, iter_pitches, parse_feed

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


class FeedClient(Protocol):
    """The slice of the MLB client the poller needs — just the GUMBO feed."""

    def live_feed(self, game_pk: int) -> dict[str, Any]:
        """Return the GUMBO live feed for a game."""
        ...


DEFAULT_INTERVAL = 10.0


def upsert_game_pitches(conn: duckdb.DuckDBPyConnection, game_pk: int, rows: list[PitchRow]) -> int:
    """Replace all stored pitches for a game with the current feed's pitches.

    Args:
        conn: Write connection to padres.db.
        game_pk: The game whose pitches to replace.
        rows: Parsed pitches (the full cumulative set from the feed).

    Returns:
        Number of pitches written.
    """
    now = datetime.now(UTC)
    conn.execute("DELETE FROM live_pitches WHERE game_pk = ?", [game_pk])
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO live_pitches VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                r.game_pk,
                r.at_bat_index,
                r.pitch_number,
                r.inning,
                r.half,
                r.pitcher_id,
                r.pitcher,
                r.batter_id,
                r.batter,
                r.pitch_type,
                r.pitch_code,
                r.velo,
                r.result,
                r.is_swing,
                r.is_whiff,
                r.in_play,
                r.balls,
                r.strikes,
                now,
            )
            for r in rows
        ],
    )
    return len(rows)


def poll_once(
    client: FeedClient, conn: duckdb.DuckDBPyConnection, game_pk: int
) -> tuple[LiveSnapshot, int]:
    """Fetch the feed once, persist its pitches, and return the snapshot + count."""
    feed = client.live_feed(game_pk)
    snapshot = parse_feed(feed)
    written = upsert_game_pitches(conn, game_pk, iter_pitches(feed))
    logger.info("poll game=%d state=%s pitches=%d", game_pk, snapshot.state, written)
    return snapshot, written


def watch(
    client: FeedClient,
    conn: duckdb.DuckDBPyConnection,
    game_pk: int,
    *,
    interval: float = DEFAULT_INTERVAL,
    max_polls: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Poll a game until it goes Final (or ``max_polls`` is reached).

    Args:
        client: Open MLB Stats client.
        conn: Write connection to padres.db.
        game_pk: Game to watch.
        interval: Seconds between polls.
        max_polls: Stop after this many polls (None = until Final).
        sleeper: Sleep function (injected for testing).

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
        if max_polls is not None and polls >= max_polls:
            break
        sleeper(interval)
    return polls
