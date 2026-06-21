"""Tests for the self-grading predictions (receipts) layer."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.angles import Stat, StoryAngle
from padres_analytics.predict import grade_predictions, log_predictions, scorecard

if TYPE_CHECKING:
    import duckdb

_AS_OF = date(2026, 6, 19)
_MATURED = date(2026, 8, 1)  # past the 30-day resolves_by


def _pitcher_angle(era: float, fip: float, pid: int = 10) -> StoryAngle:
    return StoryAngle(
        key="pitcher_luck",
        subject="Wandy Peralta",
        title="OUTRUNNING THE ARM",
        headline=f"Peralta's {era:.2f} ERA outruns a {fip:.2f} FIP.",
        thesis="t",
        direction="down",
        effect=abs(era - fip),
        reliability=0.4,
        interest=1.0,
        confidence="low",
        as_of=_AS_OF,
        subject_id=pid,
        stats=[
            Stat("pit_era", era, "count", "ERA", 100),
            Stat("pit_fip", fip, "count", "FIP", 100),
        ],
    )


def _non_gradable_angle() -> StoryAngle:
    return StoryAngle(
        key="change",
        subject="Gavin Sheets",
        title="HIT A WALL",
        headline="Sheets cooled 176 points.",
        thesis="t",
        direction="down",
        effect=176,
        reliability=0.96,
        interest=1.0,
        confidence="high",
        as_of=_AS_OF,
        subject_id=11,
        stats=[Stat("chg_delta", 176, "pts", "pts", 120)],
    )


def _season_pitching(conn: duckdb.DuckDBPyConnection, pid: int, era: str) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS player_season_pitching (player_id INTEGER, season INTEGER, "
        "era VARCHAR)"
    )
    conn.execute(
        "INSERT INTO player_season_pitching (player_id, season, era) VALUES (?, 2026, ?)",
        [pid, era],
    )


def test_log_only_falsifiable_angles_and_is_idempotent(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    """Luck calls are logged with a baseline/target/direction; non-luck is skipped."""
    angles = [_pitcher_angle(1.96, 4.49), _non_gradable_angle()]
    assert log_predictions(padres_db, angles, 2026, as_of=_AS_OF) == 1  # only the luck call
    row = padres_db.execute(
        "SELECT detector, subject_id, metric, baseline, target, expected_dir, outcome "
        "FROM predictions"
    ).fetchone()
    assert row == ("pitcher_luck", 10, "ERA", 1.96, 4.49, "up", "open")
    # re-running the same board logs nothing new (one open call per subject)
    assert log_predictions(padres_db, angles, 2026, as_of=_AS_OF) == 0


def test_grade_scores_movement_toward_the_target(padres_db: duckdb.DuckDBPyConnection) -> None:
    """ERA rising toward FIP = correct; falling away = incorrect; flat = push."""
    log_predictions(padres_db, [_pitcher_angle(1.96, 4.49, pid=10)], 2026, as_of=_AS_OF)
    _season_pitching(padres_db, 10, "3.80")  # rose toward the 4.49 FIP
    tally = grade_predictions(padres_db, as_of=_MATURED)
    assert tally["correct"] == 1
    sc = scorecard(padres_db)
    assert sc["correct"] == 1 and sc["graded"] == 1 and sc["accuracy"] == 1.0


def test_grade_marks_a_miss_and_a_push(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A metric that moved away is a miss; one that barely moved is a push."""
    log_predictions(padres_db, [_pitcher_angle(1.96, 4.49, pid=20)], 2026, as_of=_AS_OF)
    _season_pitching(padres_db, 20, "1.40")  # fell further from FIP — wrong
    assert grade_predictions(padres_db, as_of=_MATURED)["incorrect"] == 1

    log_predictions(padres_db, [_pitcher_angle(1.96, 4.49, pid=21)], 2026, as_of=_AS_OF)
    _season_pitching(padres_db, 21, "1.98")  # within epsilon — push
    assert grade_predictions(padres_db, as_of=_MATURED)["push"] == 1


def test_grade_leaves_immature_predictions_open(padres_db: duckdb.DuckDBPyConnection) -> None:
    """A prediction that hasn't come due is untouched by grading."""
    log_predictions(padres_db, [_pitcher_angle(1.96, 4.49, pid=30)], 2026, as_of=_AS_OF)
    _season_pitching(padres_db, 30, "4.00")
    tally = grade_predictions(padres_db, as_of=_AS_OF)  # same day — nothing due
    assert tally == {"correct": 0, "incorrect": 0, "push": 0, "ungradeable": 0}
    assert scorecard(padres_db)["open"] == 1
