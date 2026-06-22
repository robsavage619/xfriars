"""The engagement loop — let the account learn what resonates.

The engine grades itself on being *right* (predictions) and gates itself on being
*defensible* (audits). This is the third loop: learning what *lands*. Recorded post
metrics are tagged with the story's angle key, and ``engagement_prior`` turns that
history into a bounded ranking multiplier — angle types the audience has rewarded
rise, ones it has ignored fade. It only speaks once it has a real sample; below
that it returns a neutral 1.0 so a cold start never distorts the board.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from padres_analytics.detect.angles import StoryAngle

# An engagement score weights the high-signal actions: a follow is worth far more
# than a like. Tuned to intent, not vanity — these are reach/affinity proxies.
_W_LIKE = 1.0
_W_REPOST = 2.0
_W_BOOKMARK = 1.5
_W_FOLLOW = 6.0

_MIN_POSTS = 3  # below this many posts for an angle, there's no signal yet
_FLOOR, _CEIL = 0.7, 1.4  # the multiplier is bounded so a hot/cold streak can't dominate


def _ensure_columns(conn: duckdb.DuckDBPyConnection) -> None:
    """Create post_metrics (if absent) and tag rows with the story they measured."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS post_metrics (
            posted_tweet_id VARCHAR, captured_at TIMESTAMP, impressions INTEGER, likes INTEGER,
            reposts INTEGER, replies INTEGER, bookmarks INTEGER, follows_attributed INTEGER,
            PRIMARY KEY (posted_tweet_id, captured_at)
        )
        """
    )
    for col in ("angle_key VARCHAR", "subject VARCHAR"):
        conn.execute(f"ALTER TABLE post_metrics ADD COLUMN IF NOT EXISTS {col}")


def record_metrics(
    conn: duckdb.DuckDBPyConnection,
    posted_tweet_id: str,
    *,
    angle_key: str,
    subject: str,
    impressions: int = 0,
    likes: int = 0,
    reposts: int = 0,
    replies: int = 0,
    bookmarks: int = 0,
    follows: int = 0,
) -> None:
    """Record one snapshot of a posted tweet's metrics, tagged with its angle."""
    _ensure_columns(conn)
    conn.execute(
        """
        INSERT INTO post_metrics (
            posted_tweet_id, captured_at, impressions, likes, reposts, replies,
            bookmarks, follows_attributed, angle_key, subject
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        [
            posted_tweet_id,
            datetime.now(),
            impressions,
            likes,
            reposts,
            replies,
            bookmarks,
            follows,
            angle_key,
            subject,
        ],
    )


def _score_sql() -> str:
    return (
        f"COALESCE(likes,0)*{_W_LIKE} + COALESCE(reposts,0)*{_W_REPOST} "
        f"+ COALESCE(bookmarks,0)*{_W_BOOKMARK} + COALESCE(follows_attributed,0)*{_W_FOLLOW}"
    )


def _latest_scores_by_angle(conn: duckdb.DuckDBPyConnection) -> dict[str, list[float]]:
    """The newest metric snapshot per tweet, scored, grouped by angle key."""
    try:
        rows = conn.execute(
            f"""
            WITH latest AS (
                SELECT angle_key, {_score_sql()} AS score,
                       ROW_NUMBER() OVER (PARTITION BY posted_tweet_id ORDER BY captured_at DESC) rn
                FROM post_metrics WHERE angle_key IS NOT NULL
            )
            SELECT angle_key, score FROM latest WHERE rn = 1
            """
        ).fetchall()
    except duckdb.Error:
        return {}
    out: dict[str, list[float]] = {}
    for key, score in rows:
        out.setdefault(key, []).append(float(score))
    return out


def engagement_prior(conn: duckdb.DuckDBPyConnection, angle: StoryAngle) -> float:
    """A bounded ranking multiplier from how this angle type has historically landed.

    Returns 1.0 (neutral) until the angle has at least ``_MIN_POSTS`` measured
    posts; then the angle's mean engagement relative to the all-angle mean, clamped
    to ``[0.7, 1.4]`` so a small hot or cold run can nudge but never dominate.
    """
    scores = _latest_scores_by_angle(conn)
    mine = scores.get(angle.key, [])
    if len(mine) < _MIN_POSTS:
        return 1.0
    all_scores = [s for vals in scores.values() for s in vals]
    overall = sum(all_scores) / len(all_scores)
    if overall <= 0:
        return 1.0
    ratio = (sum(mine) / len(mine)) / overall
    return max(_FLOOR, min(_CEIL, ratio))
