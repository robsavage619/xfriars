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
from padres_analytics.detect.registry import ScanConfig
from padres_analytics.detect.scanner import _build_candidate, _run_metric
from padres_analytics.detect.sql import max_year, padre_ids, padre_ids_roster, resolve_table

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_DATE_COLS = ("game_date", "date")


def _has_date_column(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    src = resolve_table(conn, table)
    try:
        info = conn.execute(f"PRAGMA table_info('{src}')").fetchall()
    except Exception:
        return False
    cols = {str(r[1]).lower() for r in info}
    return any(c in cols for c in _DATE_COLS)


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

        out: list[StatCandidate] = []
        for spec in specs:
            try:
                candidate = self._scan_one(conn, spec, as_of, cfg)
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
    ) -> StatCandidate | None:
        result = validate(conn, spec)
        if not result.ok:
            store.log_outcome(
                conn, spec, as_of, "invalid", reason=f"{result.code}: {result.reason}"
            )
            return None

        if spec.window is not None and not _has_date_column(conn, spec.table):
            store.log_outcome(
                conn,
                spec,
                as_of,
                "unsupported_window",
                reason=f"{spec.table} is season-grain; no per-game date column",
            )
            return None

        year = max_year(conn, spec.table)
        if year is None:
            store.log_outcome(conn, spec, as_of, "no_data", reason="table has no rows")
            return None

        roster_year = year if year <= as_of.year else as_of.year
        padres = padre_ids_roster(conn, roster_year) or padre_ids(conn, roster_year)
        if not padres:
            store.log_outcome(conn, spec, as_of, "no_data", reason=f"no roster for {roster_year}")
            return None

        metric = spec.to_metric_spec()
        hits = _run_metric(conn, metric, year, padres, cfg.min_observation_n)
        if not hits:
            store.log_outcome(conn, spec, as_of, "no_data", reason="no qualifying Padre rows")
            return None

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


register(HypothesisScanDetector())
