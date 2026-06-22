"""Tests for the engagement loop — learning what resonates."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.angles import StoryAngle
from padres_analytics.engagement import engagement_prior, record_metrics

if TYPE_CHECKING:
    import duckdb


def _angle(key: str) -> StoryAngle:
    return StoryAngle(
        key=key,
        subject="x",
        title="t",
        headline="h",
        thesis="t",
        direction="up",
        effect=1.0,
        reliability=0.5,
        interest=1.0,
        confidence="moderate",
        as_of=date(2026, 6, 20),
    )


def test_cold_start_is_neutral(padres_db: duckdb.DuckDBPyConnection) -> None:
    """With no recorded metrics (column may not even exist), the prior is neutral."""
    assert engagement_prior(padres_db, _angle("pitcher_luck")) == 1.0


def test_thin_sample_stays_neutral(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Below the minimum post count, an angle gets no boost or penalty."""
    record_metrics(padres_db, "t1", angle_key="change", subject="x", likes=99, follows=9)
    assert engagement_prior(padres_db, _angle("change")) == 1.0  # only 1 post


def test_audience_winners_rise_and_flops_fade(padres_db: duckdb.DuckDBPyConnection) -> None:
    """An angle the audience rewards is boosted; one it ignores is damped — bounded."""
    for i in range(4):
        record_metrics(
            padres_db, f"p{i}", angle_key="pitcher_luck", subject="x", likes=80, follows=5
        )
        record_metrics(padres_db, f"c{i}", angle_key="change", subject="x", likes=2, follows=0)

    hot = engagement_prior(padres_db, _angle("pitcher_luck"))
    cold = engagement_prior(padres_db, _angle("change"))
    assert hot > 1.0 and cold < 1.0
    assert cold >= 0.7 and hot <= 1.4  # clamped both ways


def test_latest_snapshot_wins(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Re-recording a tweet's metrics uses the newest snapshot, not a sum."""
    for i in range(3):
        record_metrics(padres_db, f"p{i}", angle_key="pitcher_luck", subject="x", likes=10)
        record_metrics(padres_db, f"q{i}", angle_key="player_luck", subject="x", likes=10)
    # player_luck posts mature to a much bigger number on a later capture
    for i in range(3):
        record_metrics(padres_db, f"q{i}", angle_key="player_luck", subject="x", likes=200)
    assert engagement_prior(padres_db, _angle("player_luck")) > 1.0
