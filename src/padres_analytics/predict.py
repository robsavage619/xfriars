"""Self-grading predictions — the receipts layer.

Most analytics accounts post retrospective facts; none keep a public scorecard on
their own calls. This module turns the engine's *falsifiable* angles (the luck
detectors, which predict regression toward a baseline) into dated predictions and
grades them once they mature, so the account can show a batting average on itself.

A prediction is forward and gradeable only when the angle carries:

* a ``subject_id`` (so the metric can be re-measured for exactly that player), and
* a baseline (the current value) and a target (where the peripherals say it's
  heading) — the direction we are predicting the metric will move.

Grading re-measures the season metric at maturity and asks whether it moved toward
the target. This is directional on a cumulative season stat, so movement is diluted
(a 30-day window is a slice of the season) — surfaced honestly, never as precision
it doesn't have.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

    from padres_analytics.detect.angles import StoryAngle

DEFAULT_HORIZON_DAYS = 30

# New structured columns layered onto the shipped (bare) predictions table.
_COLUMNS = (
    "detector VARCHAR",
    "subject_id INTEGER",
    "subject VARCHAR",
    "season INTEGER",
    "metric VARCHAR",
    "baseline DOUBLE",
    "target DOUBLE",
    "expected_dir VARCHAR",
    "resolved_value DOUBLE",
    "resolved_at TIMESTAMP",
)


@dataclass(frozen=True)
class _Grade:
    """How to grade one detector's call: which stats anchor it, how to re-measure."""

    baseline_key: str  # angle.stats key holding the value at prediction time
    target_key: str  # angle.stats key holding where the peripherals point
    metric: str  # display label (ERA, wOBA)
    epsilon: float  # movement smaller than this counts as a push, not a hit/miss
    table: str
    column: str
    id_col: str
    season_col: str


# Only the luck detectors make a forward, falsifiable claim (results regress toward
# the peripheral baseline). Change/league-control describe what *happened*; the
# approach/power outliers are skill reads, not predictions.
_GRADABLE: dict[str, _Grade] = {
    "pitcher_luck": _Grade(
        baseline_key="pit_era",
        target_key="pit_fip",
        metric="ERA",
        epsilon=0.10,
        table="player_season_pitching",
        column="era",
        id_col="player_id",
        season_col="season",
    ),
    "player_luck": _Grade(
        baseline_key="p_woba",
        target_key="p_true",
        metric="wOBA",
        epsilon=0.005,
        table="statcast_batting_expected",
        column="woba",
        id_col="player_id",
        season_col="year",
    ),
}


def _ensure_columns(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the predictions table (if absent) and add the structured columns."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id VARCHAR PRIMARY KEY, draft_id VARCHAR, claim VARCHAR NOT NULL,
            posted_at TIMESTAMP, resolves_by DATE, outcome VARCHAR DEFAULT 'open'
        )
        """
    )
    for col in _COLUMNS:
        conn.execute(f"ALTER TABLE predictions ADD COLUMN IF NOT EXISTS {col}")


def _stat_value(angle: StoryAngle, key: str) -> float | None:
    for st in angle.stats:
        if st.key == key:
            return float(st.value)
    return None


def _has_open(conn: duckdb.DuckDBPyConnection, detector: str, subject_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM predictions "
        "WHERE detector = ? AND subject_id = ? AND outcome = 'open' LIMIT 1",
        [detector, subject_id],
    ).fetchone()
    return row is not None


def log_predictions(
    conn: duckdb.DuckDBPyConnection,
    angles: list[StoryAngle],
    season: int,
    *,
    as_of: date | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Log the falsifiable angles as open predictions; return how many were written.

    Idempotent per subject: a detector with an already-open prediction for the same
    player is skipped, so re-running on the same board doesn't double-log.
    """
    _ensure_columns(conn)
    today = as_of or date.today()
    resolves_by = today + timedelta(days=horizon_days)
    now = datetime.now()  # local wall-clock is fine for a posting timestamp
    logged = 0
    for angle in angles:
        grade = _GRADABLE.get(angle.key)
        if grade is None or angle.subject_id is None:
            continue
        baseline = _stat_value(angle, grade.baseline_key)
        target = _stat_value(angle, grade.target_key)
        if baseline is None or target is None or _has_open(conn, angle.key, angle.subject_id):
            continue
        expected_dir = "down" if target < baseline else "up"
        conn.execute(
            """
            INSERT INTO predictions (
                prediction_id, claim, posted_at, resolves_by, outcome,
                detector, subject_id, subject, season, metric, baseline, target, expected_dir
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                uuid.uuid4().hex,
                angle.headline,
                now,
                resolves_by,
                angle.key,
                angle.subject_id,
                angle.subject,
                season,
                grade.metric,
                baseline,
                target,
                expected_dir,
            ],
        )
        logged += 1
    return logged


def _remeasure(
    conn: duckdb.DuckDBPyConnection, grade: _Grade, subject_id: int, season: int
) -> float | None:
    row = conn.execute(
        f"SELECT {grade.column} FROM {grade.table} "
        f"WHERE {grade.id_col} = ? AND {grade.season_col} = ? LIMIT 1",
        [subject_id, season],
    ).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return None


def _verdict(baseline: float, target: float, current: float, epsilon: float) -> str:
    """Correct if the metric moved toward target, incorrect if away, else push."""
    moved = current - baseline
    if abs(moved) < epsilon:
        return "push"
    toward_down = target < baseline and moved < 0
    toward_up = target > baseline and moved > 0
    return "correct" if (toward_down or toward_up) else "incorrect"


def grade_predictions(
    conn: duckdb.DuckDBPyConnection, *, as_of: date | None = None
) -> dict[str, int]:
    """Grade every open, matured prediction by re-measuring its metric.

    Returns a tally ``{"correct", "incorrect", "push", "ungradeable"}`` of what
    this pass resolved (ungradeable = metric no longer in the DB to re-measure).
    """
    _ensure_columns(conn)
    today = as_of or date.today()
    tally = {"correct": 0, "incorrect": 0, "push": 0, "ungradeable": 0}
    rows = conn.execute(
        """
        SELECT prediction_id, detector, subject_id, season, baseline, target
        FROM predictions WHERE outcome = 'open' AND resolves_by <= ?
        """,
        [today],
    ).fetchall()
    now = datetime.now()
    for pid, detector, subject_id, season, baseline, target in rows:
        grade = _GRADABLE.get(detector)
        if grade is None or subject_id is None:
            tally["ungradeable"] += 1
            continue
        current = _remeasure(conn, grade, int(subject_id), int(season))
        if current is None:
            tally["ungradeable"] += 1
            continue
        outcome = _verdict(float(baseline), float(target), current, grade.epsilon)
        conn.execute(
            "UPDATE predictions SET outcome = ?, resolved_value = ?, resolved_at = ? "
            "WHERE prediction_id = ?",
            [outcome, current, now, pid],
        )
        tally[outcome] += 1
    return tally


def scorecard(conn: duckdb.DuckDBPyConnection) -> dict[str, object]:
    """The public batting average: graded counts and accuracy over resolved calls."""
    _ensure_columns(conn)
    counts = {
        outcome: n
        for outcome, n in conn.execute(
            "SELECT outcome, COUNT(*) FROM predictions GROUP BY outcome"
        ).fetchall()
    }
    correct = int(counts.get("correct", 0))
    incorrect = int(counts.get("incorrect", 0))
    graded = correct + incorrect
    return {
        "open": int(counts.get("open", 0)),
        "correct": correct,
        "incorrect": incorrect,
        "push": int(counts.get("push", 0)),
        "graded": graded,
        "accuracy": round(correct / graded, 3) if graded else None,
    }
