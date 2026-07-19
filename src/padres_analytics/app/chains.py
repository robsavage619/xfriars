"""Engine chains the Studio can start as background jobs.

Everything here is engine code — SQL detectors, the registry scanner, the daily
briefing, the scout. No model is called anywhere in a chain: discovery produces
candidates and leads, and a human takes them to Claude through the prompt desk.

Each chain is a worker for :mod:`padres_analytics.app.jobs`, reporting one step
per stage so the UI shows which part of a run failed rather than one opaque
error.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import duckdb

from padres_analytics.app.jobs import Worker

if TYPE_CHECKING:
    from padres_analytics.app.jobs import JobReporter

logger = logging.getLogger(__name__)


def _today() -> date:
    return datetime.now(UTC).astimezone().date()


def _coverage_preflight(conn: duckdb.DuckDBPyConnection) -> str:
    """Report stale/empty domains before we detect on top of them.

    Stale Statcast has silently blocked stories before: the detectors run
    clean, surface nothing, and nothing says why. Naming the gap up front
    makes an empty discovery run interpretable.
    """
    from padres_analytics.storage import coverage

    reports = coverage.audit(conn)
    degraded = [r for r in reports if r.status != "OK"]
    if not degraded:
        return f"all {len(reports)} domains current"
    worst = ", ".join(f"{r.domain} ({r.status.lower()})" for r in degraded[:4])
    more = f", +{len(degraded) - 4} more" if len(degraded) > 4 else ""
    return f"{len(degraded)}/{len(reports)} domains degraded: {worst}{more}"


def _run_detectors(conn: duckdb.DuckDBPyConnection, ref_date: date) -> str:
    """Run every registered detector, emitting candidates. Mirrors ``pad detect run all``."""
    import importlib

    from padres_analytics.detect.base import all_detectors, emit, get_detector

    for mod in (
        "changepoint",
        "contrast",
        "gems",
        "leaderboards",
        "milestones",
        "prospects",
        "standings",
        "statcast",
        "struggles",
        "trades",
    ):
        try:
            importlib.import_module(f"padres_analytics.detect.{mod}")
        except Exception:
            logger.exception("could not import detector module %r", mod)

    total = 0
    failed: list[str] = []
    for name in all_detectors():
        try:
            candidates = get_detector(name).run(conn, ref_date)
            total += emit(conn, candidates)
        except Exception as exc:
            logger.warning("detector %s failed: %s", name, exc)
            failed.append(name)

    note = f"{total} new candidate(s) from {len(all_detectors())} detector(s)"
    return f"{note}; failed: {', '.join(failed)}" if failed else note


def _run_scan(conn: duckdb.DuckDBPyConnection, ref_date: date) -> str:
    """Run the registry-driven scanner. Mirrors ``pad scan run``."""
    import padres_analytics.detect.scanner  # noqa: F401 - triggers registration
    from padres_analytics.detect.base import emit, get_detector
    from padres_analytics.detect.registry import load_registry

    reg = load_registry()
    candidates = get_detector("scan").run(conn, ref_date)
    if not candidates:
        return "no candidates surfaced"
    n = emit(conn, candidates[: reg.scan.top_k])
    return f"{len(candidates)} surfaced, {n} new (top {reg.scan.top_k})"


def _run_scout(conn: duckdb.DuckDBPyConnection, season: int, ref_date: date) -> str:
    """Refresh the Leads lane. Mirrors ``pad scout``."""
    from padres_analytics.board import add_leads
    from padres_analytics.detect.leads import scout

    leads = scout(conn, season, as_of=ref_date)
    n = add_leads(conn, leads)
    return f"{n} lead(s) on the board"


def discovery_worker(season: int) -> Worker:
    """Build the discovery chain worker for a season.

    The chain: coverage preflight → detectors → registry scan → daily briefing
    (which grades predictions, runs the hypothesis cycle, discovers and verifies
    the day's story, renders it, and queues it on the Board) → scout.
    """

    def worker(reporter: JobReporter) -> None:
        from padres_analytics.config import CARDS_DIR
        from padres_analytics.daily import run_briefing
        from padres_analytics.storage.db import attach_trades, connect
        from padres_analytics.storage.schemas import initialize

        ref_date = _today()
        with connect() as conn:
            initialize(conn)
            try:
                attach_trades(conn)
            except Exception as exc:
                logger.info("trades DB not attached: %s", exc)

            reporter.step("coverage", lambda: _coverage_preflight(conn))
            reporter.step("detect", lambda: _run_detectors(conn, ref_date))
            reporter.step("scan", lambda: _run_scan(conn, ref_date))

            briefing_note = {"text": ""}

            def _briefing() -> str:
                briefing = run_briefing(conn, season, as_of=ref_date, out_dir=CARDS_DIR)
                if briefing.story is None:
                    briefing_note["text"] = "No story cleared the significance gates."
                    return briefing_note["text"]
                briefing_note["text"] = f"{briefing.story.title} — {briefing.story.headline}"
                return briefing_note["text"]

            reporter.step("briefing", _briefing)
            reporter.step("scout", lambda: _run_scout(conn, season, ref_date))
            reporter.summarize(briefing_note["text"])

    return worker


def sync_worker(season: int) -> Worker:
    """Build the data-refresh worker. Mirrors ``pad sync``."""

    def worker(reporter: JobReporter) -> None:
        from padres_analytics.ingest.sync import run_sync
        from padres_analytics.storage.db import connect
        from padres_analytics.storage.schemas import initialize

        with connect() as conn:
            initialize(conn)
            results = run_sync(conn, season)

        ok = sum(1 for r in results if r.ok)
        for r in results:
            step = reporter.start_step(r.name)
            reporter.finish_step(step, ok=r.ok, detail=r.detail)
        reporter.summarize(f"{ok}/{len(results)} step(s) ok")

    return worker
