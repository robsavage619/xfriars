"""Named background jobs for the Studio.

The Studio kicks engine work — a data sync, a discovery chain, an inbox
ingest — that takes longer than a request. Each job runs on one background
thread, reports per-step progress the frontend polls, and is persisted to
``studio_jobs`` when it finishes so a server restart doesn't erase the answer
to "what did last night's run find".

Only one job runs at a time. DuckDB takes a single writer per process, and the
chains here all write; refusing a second start is the honest failure rather
than a lock error halfway through a run.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


@dataclass
class JobStep:
    """One unit of work within a job. ``ok`` is None while it is still running."""

    name: str
    ok: bool | None = None
    detail: str = ""


@dataclass
class JobRun:
    """A single execution of a named job."""

    run_id: str
    job: str
    season: int | None
    started_at: str
    finished_at: str | None = None
    running: bool = True
    ok: bool | None = None
    summary: str = ""
    steps: list[JobStep] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the status endpoint."""
        return {**asdict(self), "steps": [asdict(s) for s in self.steps]}


class JobReporter:
    """Handle a worker uses to report progress. Every mutation is lock-guarded."""

    __slots__ = ("_lock", "_run")

    def __init__(self, run: JobRun, lock: threading.Lock) -> None:
        """Bind a reporter to one run, sharing the module lock."""
        self._run = run
        self._lock = lock

    def start_step(self, name: str) -> JobStep:
        """Open a step. It shows as running until finished."""
        step = JobStep(name=name)
        with self._lock:
            self._run.steps.append(step)
        return step

    def finish_step(self, step: JobStep, *, ok: bool, detail: str = "") -> None:
        """Close a step with its outcome."""
        with self._lock:
            step.ok = ok
            step.detail = detail

    def step(self, name: str, fn: Callable[[], str]) -> bool:
        """Run one step, recording its outcome. Failures are recorded, not raised.

        A chain step that fails should not abort the steps after it: a stale
        Statcast pull still leaves the detectors worth running, and the user
        needs to see which part failed rather than a single opaque error.
        """
        step = self.start_step(name)
        try:
            detail = fn()
        except Exception as exc:
            logger.exception("job step %r failed", name)
            self.finish_step(step, ok=False, detail=str(exc))
            return False
        self.finish_step(step, ok=True, detail=detail)
        return True

    def summarize(self, summary: str) -> None:
        """Record the one-line result the Desk shows for this run."""
        with self._lock:
            self._run.summary = summary


Worker = Callable[[JobReporter], None]

_LOCK = threading.Lock()
_RUNS: dict[str, JobRun] = {}
_ACTIVE: str | None = None


def active_job() -> str | None:
    """Name of the job currently running, if any."""
    with _LOCK:
        return _ACTIVE


def latest(job: str) -> dict[str, Any] | None:
    """In-memory state of the most recent run of ``job``."""
    with _LOCK:
        run = _RUNS.get(job)
        return run.as_dict() if run else None


def _persist(run: JobRun) -> None:
    """Record a finished run so it survives a restart."""
    from padres_analytics.storage.db import connect
    from padres_analytics.storage.schemas import initialize

    try:
        with connect() as conn:
            initialize(conn)
            conn.execute(
                "INSERT OR REPLACE INTO studio_jobs "
                "(run_id, job, season, started_at, finished_at, ok, summary, steps_json) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [
                    run.run_id,
                    run.job,
                    run.season,
                    run.started_at,
                    run.finished_at,
                    run.ok,
                    run.summary,
                    json.dumps([asdict(s) for s in run.steps]),
                ],
            )
    except Exception:
        logger.exception("could not persist job run %s", run.run_id)


def last_persisted(conn: duckdb.DuckDBPyConnection, job: str) -> dict[str, Any] | None:
    """Most recent persisted run of ``job``, for a cold server with no memory."""
    try:
        row = conn.execute(
            "SELECT run_id, job, season, CAST(started_at AS VARCHAR), "
            "CAST(finished_at AS VARCHAR), ok, summary, steps_json "
            "FROM studio_jobs WHERE job = ? ORDER BY started_at DESC LIMIT 1",
            [job],
        ).fetchone()
    except Exception:
        logger.exception("could not read persisted job history for %r", job)
        return None
    if not row:
        return None
    steps = json.loads(row[7]) if isinstance(row[7], str) else (row[7] or [])
    return {
        "run_id": row[0],
        "job": row[1],
        "season": row[2],
        "started_at": row[3],
        "finished_at": row[4],
        "running": False,
        "ok": row[5],
        "summary": row[6],
        "steps": steps,
    }


def start(job: str, worker: Worker, *, season: int | None = None) -> JobRun:
    """Start ``job`` on a background thread.

    Raises:
        JobBusyError: if this or another job is already running.
    """
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is not None:
            raise JobBusyError(_ACTIVE)
        run = JobRun(
            run_id=uuid.uuid4().hex[:16],
            job=job,
            season=season,
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        _RUNS[job] = run
        _ACTIVE = job

    reporter = JobReporter(run, _LOCK)

    def _run() -> None:
        global _ACTIVE
        try:
            worker(reporter)
        except Exception as exc:
            logger.exception("job %r failed", job)
            with _LOCK:
                run.steps.append(JobStep(name=job, ok=False, detail=str(exc)))
        finally:
            with _LOCK:
                run.running = False
                run.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
                run.ok = all(s.ok for s in run.steps) if run.steps else True
            # Persist before releasing the slot: a caller that sees the job
            # finished must be able to read the run, and the next job needs
            # this one's writer released first.
            _persist(run)
            with _LOCK:
                _ACTIVE = None

    threading.Thread(target=_run, daemon=True, name=f"studio-job-{job}").start()
    return run


class JobBusyError(RuntimeError):
    """Raised when a job is requested while another is still running."""

    def __init__(self, running: str) -> None:
        """Name the job that holds the slot."""
        super().__init__(f"{running} is already running")
        self.running = running


def reset_for_tests() -> None:
    """Clear in-memory job state. Tests only."""
    global _ACTIVE
    with _LOCK:
        _RUNS.clear()
        _ACTIVE = None
