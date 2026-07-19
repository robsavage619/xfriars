"""`pad learn run` — recompute priors from every labelled decision, and report.

The recompute is stateless: priors are rebuilt from the raw signal tables every
run, and ``learned_priors`` is a snapshot for consumers plus an audit trail.
Nothing accumulates incrementally, so nothing can silently corrupt.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from padres_analytics.learn import features as feat
from padres_analytics.learn import priors as pri

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_PRIVATE_WEIGHTS = Path(__file__).resolve().parents[3] / "private" / "interest_weights.toml"

# How far a learned detector bonus may move the hand-set baseline.
_BONUS_SPAN = 0.15


@dataclass
class LearnResult:
    """Outcome of one learning run."""

    run_id: str
    as_of: date
    observations: int
    feature_stats: dict[str, pri.FeatureStat]
    reliability: dict[str, float]
    wrote_weights: bool
    notes: list[str]

    def informative(self) -> list[pri.FeatureStat]:
        """Features whose multiplier actually moved off neutral, strongest first."""
        moved = [s for s in self.feature_stats.values() if s.multiplier != 1.0]
        return sorted(moved, key=lambda s: abs(s.multiplier - 1.0), reverse=True)


def _graded_by_detector(conn: duckdb.DuckDBPyConnection) -> dict[str, tuple[int, int]]:
    """Correct/graded counts per detector from the predictions ledger."""
    try:
        rows = conn.execute(
            """
            SELECT detector,
                   SUM(CASE WHEN outcome = 'correct' THEN 1 ELSE 0 END),
                   COUNT(*)
            FROM predictions
            WHERE outcome IS NOT NULL AND outcome != 'pending'
            GROUP BY detector
            """
        ).fetchall()
    except Exception as exc:
        logger.debug("learn: predictions unavailable (%s)", exc)
        return {}
    return {str(r[0]): (int(r[1] or 0), int(r[2])) for r in rows}


def _persist(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    as_of: date,
    stats: dict[str, pri.FeatureStat],
    reliability: dict[str, float],
    observations: int,
) -> None:
    """Write the snapshot consumers read and the audit row humans read."""
    conn.execute("DELETE FROM learned_priors WHERE run_id = ?", [run_id])
    for s in stats.values():
        conn.execute(
            """
            INSERT INTO learned_priors
                (prior_id, run_id, kind, feature, n_pos, n_total, multiplier)
            VALUES (?, ?, 'editorial', ?, ?, ?, ?)
            """,
            [str(uuid.uuid4())[:8], run_id, s.feature, s.n_pos, s.n_total, s.multiplier],
        )
    for detector, mult in reliability.items():
        conn.execute(
            """
            INSERT INTO learned_priors
                (prior_id, run_id, kind, feature, n_pos, n_total, multiplier)
            VALUES (?, ?, 'reliability', ?, NULL, NULL, ?)
            """,
            [str(uuid.uuid4())[:8], run_id, f"detector:{detector}", mult],
        )
    conn.execute(
        """
        INSERT INTO learning_runs (run_id, as_of, observations, summary_json)
        VALUES (?, ?, ?, ?)
        """,
        [
            run_id,
            as_of,
            observations,
            json.dumps(
                {
                    "features": len(stats),
                    "informative": sum(1 for s in stats.values() if s.multiplier != 1.0),
                    "reliability": reliability,
                }
            ),
        ],
    )


def _write_weights(reliability: dict[str, float], stats: dict[str, pri.FeatureStat]) -> bool:
    """Write learned detector bonuses to private/interest_weights.toml.

    The reader (``scoring._load_weights``) has always existed; nothing ever wrote
    this file, so the engine ran on untuned example values. Only
    ``[detector_bonuses]`` is learned: refitting the component weights from
    sparse editorial verdicts is under-determined, so those stay hand-set.

    Returns:
        True if the file was written.
    """
    import tomllib

    baseline: dict = {}
    example = Path(__file__).resolve().parents[3] / "examples" / "interest_weights.example.toml"
    source = _PRIVATE_WEIGHTS if _PRIVATE_WEIGHTS.exists() else example
    if source.exists():
        with source.open("rb") as fh:
            baseline = tomllib.load(fh)

    bonuses = dict(baseline.get("detector_bonuses", {}))
    for key, s in stats.items():
        if not key.startswith("detector:") or s.multiplier == 1.0:
            continue
        detector = key.split(":", 1)[1]
        # Multiplier in [0.8, 1.25] -> additive nudge bounded by _BONUS_SPAN.
        nudge = (s.multiplier - 1.0) / 0.25 * _BONUS_SPAN
        current = float(bonuses.get(detector, 0.0))
        bonuses[detector] = round(max(-_BONUS_SPAN, min(_BONUS_SPAN, current + nudge)), 4)

    for detector, mult in reliability.items():
        nudge = (mult - 1.0) / 0.15 * _BONUS_SPAN * 0.5
        current = float(bonuses.get(detector, 0.0))
        bonuses[detector] = round(max(-_BONUS_SPAN, min(_BONUS_SPAN, current + nudge)), 4)

    if not bonuses:
        return False

    weights = baseline.get("weights", {})
    thresholds = baseline.get("thresholds", {})

    lines = [
        "# GENERATED by `pad learn run` — do not hand-edit; edits are overwritten.",
        "# [weights] and [thresholds] are carried through untouched: refitting them",
        "# from sparse editorial verdicts is under-determined. Only detector bonuses",
        "# are learned, from Board verdicts, referee outcomes and prediction grades.",
        "",
        "[weights]",
    ]
    lines += [f"{k} = {v}" for k, v in weights.items()]
    lines += ["", "[thresholds]"]
    lines += [f"{k} = {v}" for k, v in thresholds.items()]
    lines += ["", "[detector_bonuses]"]
    lines += [f"{k} = {v}" for k, v in sorted(bonuses.items())]

    _PRIVATE_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    _PRIVATE_WEIGHTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("learn: wrote %s (%d detector bonuses)", _PRIVATE_WEIGHTS, len(bonuses))
    return True


def learn(
    conn: duckdb.DuckDBPyConnection,
    as_of: date | None = None,
    *,
    dry_run: bool = False,
) -> LearnResult:
    """Recompute every prior from the labelled record.

    Args:
        conn: Write-mode connection (read-only when ``dry_run``).
        as_of: Reference date for decay; defaults to today.
        dry_run: Compute and report without persisting or writing weights.

    Returns:
        The run result, including what moved and why.
    """
    ref = as_of or date.today()
    observations = feat.collect(conn)
    stats = pri.feature_stats(observations, ref)
    graded = _graded_by_detector(conn)
    reliability = pri.detector_reliability(graded)

    notes: list[str] = []
    if not observations:
        notes.append(
            "No labelled decisions yet — every prior is neutral. Priors start "
            "learning once cards are queued/dismissed on the Board, candidates "
            "are rejected in Studio, or the referee records verdicts."
        )
    if not graded:
        notes.append(
            "No graded predictions yet — detector reliability is neutral. "
            "Run `pad daily` regularly so predictions log and mature."
        )
    informative = sum(1 for s in stats.values() if s.multiplier != 1.0)
    if observations and not informative:
        notes.append(
            f"{len(observations)} observation(s) recorded but no feature has cleared "
            f"the {pri.MIN_EVIDENCE:.0f}-evidence floor — all priors remain neutral."
        )

    run_id = str(uuid.uuid4())[:8]
    wrote = False
    if not dry_run:
        _persist(conn, run_id, ref, stats, reliability, len(observations))
        wrote = _write_weights(reliability, stats)

    return LearnResult(
        run_id=run_id,
        as_of=ref,
        observations=len(observations),
        feature_stats=stats,
        reliability=reliability,
        wrote_weights=wrote,
        notes=notes,
    )


def report(result: LearnResult) -> str:
    """Render a human-readable account of what moved and on what evidence.

    Auditability is the point: a prior that changes ranking without a legible
    reason is indistinguishable from a bug.
    """
    lines = [
        f"Learning run {result.run_id} — {result.as_of}",
        f"{result.observations} labelled observation(s), {len(result.feature_stats)} feature(s)",
        "",
    ]

    moved = result.informative()
    if moved:
        lines.append("Features off neutral (multiplier x evidence):")
        for s in moved[:20]:
            arrow = "up" if s.multiplier > 1.0 else "down"
            lines.append(
                f"  {s.feature:<34} x{s.multiplier:.3f} {arrow:<4} "
                f"({s.n_pos:.1f}/{s.n_total:.1f} kept, rate {s.rate:.2f})"
            )
    else:
        lines.append("No feature has enough evidence to move off neutral.")

    active_reliability = {d: m for d, m in result.reliability.items() if m != 1.0}
    if active_reliability:
        lines += ["", "Detector reliability (from graded predictions):"]
        for detector, mult in sorted(active_reliability.items()):
            lines.append(f"  {detector:<34} x{mult:.3f}")

    if result.wrote_weights:
        lines += ["", f"Wrote {_PRIVATE_WEIGHTS}"]

    if result.notes:
        lines += ["", "Notes:"]
        lines += [f"  - {n}" for n in result.notes]

    return "\n".join(lines)
