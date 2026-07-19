"""HypothesisScanDetector — runs queued LLM proposals through the scanner.

Each pending spec is validated (the trust boundary), gated on window support and
data availability, then run through the *same* ``_run_metric`` lenses and rarity
floor the generic scanner uses. Survivors are re-tagged ``detector='hypothesis'``
with ``origin='llm'`` provenance and emitted; every spec — survivor or not —
records a terminal outcome in the explored-space ledger.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.base import register
from padres_analytics.detect.candidates import StatCandidate, make_candidate_id
from padres_analytics.detect.hypothesis import store
from padres_analytics.detect.hypothesis.spec import HypothesisSpec
from padres_analytics.detect.hypothesis.validate import validate
from padres_analytics.detect.hypothesis.window import (
    date_column,
    fetch_window_rows,
    min_events_for,
)
from padres_analytics.detect.registry import ScanConfig
from padres_analytics.detect.scanner import _build_candidate, _Hit, _run_metric, lenses_over_rows
from padres_analytics.detect.sql import available_padre_ids, max_year
from padres_analytics.storage.coverage import CONTRACT, CoverageReport, audit

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


def _staleness_days(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    date_col: str,
    as_of: date,
) -> int | None:
    """Days between a table's newest row and the reference date, or None if unknown."""
    from padres_analytics.detect.sql import resolve_table

    try:
        row = conn.execute(f"SELECT MAX({date_col}) FROM {resolve_table(conn, table)}").fetchone()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    latest = row[0]
    latest = latest.date() if hasattr(latest, "date") else latest
    return (as_of - latest).days


def _coverage_block(reports: list[CoverageReport], table: str) -> str | None:
    """Reason the table's coverage can't back a claim, or None if it can / isn't contracted.

    Gates on the data-coverage contract before scanning: a spec over a STALE,
    PARTIAL, EMPTY, or MISSING domain is refused up front rather than producing a
    plausible-but-unsupported candidate. Tables outside the contract (e.g. barrels,
    sprint speed) carry no coverage claim and pass through.

    Gate is on the domain *status* (is the current-season granular data present and
    adequately populated), not on :func:`can_support`: a hypothesis makes an
    honestly-scoped state/window claim, not a cross-season *change* claim, so the
    prior-season-baseline requirement can_support bakes into change capabilities
    (CONTACT_TREND, APPROACH_TREND, …) does not apply here.
    """
    if table not in {s.table for s in CONTRACT}:
        return None
    report = next((r for r in reports if r.table == table), None)
    if report is None or report.status == "OK":
        return None
    return f"{report.domain} {report.status}: {report.reason}"


def _retag(candidate: StatCandidate) -> StatCandidate:
    """Re-stamp a scanner candidate as LLM-originated, with a fresh stable id."""
    cid = make_candidate_id("hypothesis", candidate.subject, candidate.facts_json)
    provenance = [{**p, "origin": "llm"} for p in candidate.provenance_json]
    return candidate.model_copy(
        update={"candidate_id": cid, "detector": "hypothesis", "provenance_json": provenance}
    )


class HypothesisScanDetector:
    """Detector that scans the LLM-proposed hypothesis queue. Registered as 'hypothesis'."""

    name = "hypothesis"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Validate and scan every pending hypothesis; return emitted candidates.

        Args:
            conn: Write-mode padres.db connection with hist attached (the ledger
                and queue are written here).
            as_of: Reference date.

        Returns:
            Candidates that cleared the rarity floor, re-tagged to this detector.
        """
        cfg = ScanConfig()
        specs = store.pending(conn)
        if not specs:
            logger.info("hypothesis: queue empty")
            return []

        reports = audit(conn)
        out: list[StatCandidate] = []
        for spec in specs:
            try:
                candidate = self._scan_one(conn, spec, as_of, cfg, reports)
            except Exception as exc:  # one bad spec must not sink the batch
                logger.warning("hypothesis: %s raised %s", spec.id, exc)
                store.log_outcome(conn, spec, as_of, "no_data", reason=f"error: {exc}")
                candidate = None
            store.mark_processed(conn, spec.spec_hash())
            if candidate is not None:
                out.append(candidate)

        out.sort(key=lambda c: c.novelty_score, reverse=True)
        return out

    def _scan_one(
        self,
        conn: duckdb.DuckDBPyConnection,
        spec: HypothesisSpec,
        as_of: date,
        cfg: ScanConfig,
        reports: list[CoverageReport],
    ) -> StatCandidate | None:
        result = validate(conn, spec)
        if not result.ok:
            store.log_outcome(
                conn, spec, as_of, "invalid", reason=f"{result.code}: {result.reason}"
            )
            return None

        block = _coverage_block(reports, spec.table)
        if block is not None:
            store.log_outcome(conn, spec, as_of, "coverage_blocked", reason=block)
            return None

        if spec.window is not None:
            hits = self._scan_windowed(conn, spec, as_of, cfg)
        else:
            hits = self._scan_seasonal(conn, spec, as_of, cfg)
        if not hits:
            return None  # the helper already logged the terminal outcome

        best = max(hits, key=lambda h: h.lens_result.rarity)
        if best.lens_result.rarity < cfg.min_rarity:
            store.log_outcome(
                conn,
                spec,
                as_of,
                "below_gate",
                max_rarity=best.lens_result.rarity,
                reason=f"rarity {best.lens_result.rarity:.2f} < floor {cfg.min_rarity:.2f}",
            )
            return None

        candidate = _retag(_build_candidate(best, as_of))
        store.log_outcome(
            conn,
            spec,
            as_of,
            "emitted",
            max_rarity=best.lens_result.rarity,
            candidate_id=candidate.candidate_id,
            reason=best.lens_result.framing[:80],
        )
        return candidate

    def _scan_seasonal(
        self,
        conn: duckdb.DuckDBPyConnection,
        spec: HypothesisSpec,
        as_of: date,
        cfg: ScanConfig,
    ) -> list[_Hit]:
        """Season-aggregate path: the metric's own table, compared league-wide."""
        year = max_year(conn, spec.table)
        if year is None:
            store.log_outcome(conn, spec, as_of, "no_data", reason="table has no rows")
            return []

        roster_year = year if year <= as_of.year else as_of.year
        padres = available_padre_ids(conn, roster_year)
        if not padres:
            store.log_outcome(conn, spec, as_of, "no_data", reason=f"no roster for {roster_year}")
            return []

        hits = _run_metric(conn, spec.to_metric_spec(), year, padres, cfg.min_observation_n)
        if not hits:
            store.log_outcome(conn, spec, as_of, "no_data", reason="no qualifying Padre rows")
        return hits

    def _scan_windowed(
        self,
        conn: duckdb.DuckDBPyConnection,
        spec: HypothesisSpec,
        as_of: date,
        cfg: ScanConfig,
    ) -> list[_Hit]:
        """Rolling last-N-day path: requires a game-grain table with a date column."""
        date_col = date_column(conn, spec.table)
        if date_col is None:
            store.log_outcome(
                conn,
                spec,
                as_of,
                "unsupported_window",
                reason=f"{spec.table} is season-grain; no per-event date column",
            )
            return []

        padres = available_padre_ids(conn, as_of.year)
        if not padres:
            store.log_outcome(conn, spec, as_of, "no_data", reason=f"no roster for {as_of.year}")
            return []

        # A window that closes before the table's latest data is a staleness
        # problem, not a bad hypothesis. Saying "no qualifying rows" would teach
        # the proposer to stop asking recency questions when the real fix is an
        # ingest run — and the ledger is read back as guidance.
        assert spec.window is not None  # windowed path; validated upstream
        stale_by = _staleness_days(conn, spec.table, date_col, as_of)
        if stale_by is not None and stale_by > spec.window.days:
            store.log_outcome(
                conn,
                spec,
                as_of,
                "no_data",
                reason=(
                    f"{spec.table} data is {stale_by} days stale; a "
                    f"{spec.window.days}-day window ends before it begins. "
                    f"Re-run ingest rather than reworking this spec."
                ),
            )
            return []

        rows, src = fetch_window_rows(conn, spec, as_of, date_col)
        # Honest claim scope: the candidate is a last-N-day window, not the season.
        metric = spec.to_metric_spec().model_copy(
            update={"coverage": f"last {spec.window.days} days (MLB)"}  # type: ignore[union-attr]
        )
        hits = lenses_over_rows(metric, rows, src, as_of.year, padres, cfg.min_observation_n)
        if not hits:
            # "No data" and "data, but nobody was extreme" teach opposite lessons.
            # The first says fix the ingest; the second says this question has been
            # asked and answered. Conflating them makes the ledger misleading, and
            # the ledger is what the proposer reads back.
            subjects_present = sum(1 for pid, _, _ in rows if pid in padres)
            if subjects_present:
                store.log_outcome(
                    conn,
                    spec,
                    as_of,
                    "below_gate",
                    reason=(
                        f"{subjects_present} Padre(s) measured over {len(rows)} players; "
                        f"none reached the rarity floor"
                    ),
                )
            else:
                store.log_outcome(
                    conn,
                    spec,
                    as_of,
                    "no_data",
                    reason=(
                        f"{len(rows)} players had qualifying windows but no available "
                        f"Padre cleared the {min_events_for(spec.table)}-event minimum"
                    ),
                )
        return hits


register(HypothesisScanDetector())
