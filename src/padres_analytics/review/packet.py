"""Build the review packet a referee reasons over.

The packet carries what's needed to judge an *argument*, which is more than the
card shows: the population filter and its size, the window and the battery it
came out of, the coverage status of every table touched, and an explicit list of
what was never checked. A referee can only catch what the packet shows, so the
gaps are stated rather than left to inference.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from padres_analytics.review.models import ReviewPacket

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


def _loads(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if raw is not None else {}


def _coverage_rows(conn: duckdb.DuckDBPyConnection, tables: set[str]) -> list[dict]:
    """Coverage-contract status for every table the claim rests on."""
    if not tables:
        return []
    try:
        from padres_analytics.storage.coverage import audit

        reports = audit(conn)
    except Exception as exc:
        logger.debug("packet: coverage audit unavailable: %s", exc)
        return []
    return [
        {
            "table": r.table,
            "domain": r.domain,
            "status": r.status,
            "reason": r.reason,
        }
        for r in reports
        if r.table in tables
    ]


def _not_checked(facts: dict, coverage: list[dict], population_size: int | None) -> list[str]:
    """Enumerate the blind spots a referee should weigh against the claim."""
    gaps: list[str] = []

    if population_size is None:
        gaps.append("Population size is not recorded — the comparison universe is unstated.")

    non_ok = [c for c in coverage if c.get("status") != "OK"]
    for c in non_ok:
        gaps.append(f"Coverage for {c['table']} is {c['status']}: {c.get('reason', '')}".strip())

    if not facts.get("metric_year") and not facts.get("year"):
        gaps.append("No season stamped in facts — the claim's time window is implicit.")

    # A conjunction asserts a compound; without the peer count it is two facts
    # printed together, and a referee should not read uniqueness into it.
    if facts.get("n_metrics") and "players_meeting_all" not in facts:
        gaps.append("Compound claim carries no league peer count — no uniqueness was established.")

    gaps.append(
        "No park or era adjustment is applied unless the metric itself is adjusted; "
        "Petco depresses raw offensive rates."
    )
    gaps.append(
        "Defensive metrics (OAA, arm strength) carry wide year-to-year variance and "
        "are not regressed here."
    )
    return gaps


def build_packet(
    conn: duckdb.DuckDBPyConnection,
    *,
    draft_id: str | None = None,
    candidate_id: str | None = None,
    as_of: date | None = None,
) -> ReviewPacket:
    """Assemble the packet for a draft (preferred) or a bare candidate.

    Args:
        conn: Read connection with hist attached.
        draft_id: Draft to review — carries the caption, so prefer this.
        candidate_id: Candidate to review when no draft exists yet.
        as_of: Reference date; defaults to today.

    Returns:
        A populated ReviewPacket.

    Raises:
        ValueError: If neither id is given, or the target isn't found.
    """
    if not draft_id and not candidate_id:
        raise ValueError("build_packet requires draft_id or candidate_id.")

    caption = ""
    target_kind: str = "candidate"
    target_id = candidate_id or ""

    if draft_id:
        row = conn.execute(
            "SELECT candidate_id, text FROM tweet_drafts WHERE draft_id = ?",
            [draft_id],
        ).fetchone()
        if row is None:
            raise ValueError(f"Draft {draft_id!r} not found.")
        candidate_id, caption = row[0], row[1] or ""
        target_kind, target_id = "draft", draft_id

    crow = conn.execute(
        """
        SELECT facts_json, provenance_json, claim_scope, coverage_window,
               detector, novelty_score, as_of
        FROM stat_candidates
        WHERE candidate_id = ?
        """,
        [candidate_id],
    ).fetchone()
    if crow is None:
        raise ValueError(f"Candidate {candidate_id!r} not found.")

    facts_json = _loads(crow[0])
    provenance = _loads(crow[1]) or []
    claim_scope, coverage_window, detector = crow[2] or "", crow[3] or "", crow[4] or ""
    candidate_as_of = crow[6]

    inner_facts = facts_json.get("facts", {}) if isinstance(facts_json, dict) else {}
    claim = facts_json.get("headline") or facts_json.get("framing") or ""
    population_label = facts_json.get("population_label", "") or ""
    population_size = inner_facts.get("population_size")

    tables = {p.get("source_table", "").split(".")[-1] for p in provenance if isinstance(p, dict)}
    tables.discard("")
    coverage = _coverage_rows(conn, tables)

    lens = ""
    for p in provenance:
        if isinstance(p, dict) and p.get("lens"):
            lens = str(p["lens"])
            break

    return ReviewPacket(
        target_kind=target_kind,  # type: ignore[arg-type]
        target_id=target_id,
        as_of=as_of or (candidate_as_of if isinstance(candidate_as_of, date) else date.today()),
        claim=claim,
        caption=caption,
        facts=inner_facts if isinstance(inner_facts, dict) else {},
        claim_scope=claim_scope,
        coverage_window=coverage_window,
        population_label=population_label,
        population_size=int(population_size) if isinstance(population_size, int | float) else None,
        detector=detector,
        lens=lens,
        rarity=float(crow[5]) if crow[5] is not None else None,
        provenance=[p for p in provenance if isinstance(p, dict)],
        coverage_status=coverage,
        not_checked=_not_checked(
            inner_facts if isinstance(inner_facts, dict) else {},
            coverage,
            int(population_size) if isinstance(population_size, int | float) else None,
        ),
    )
