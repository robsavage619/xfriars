"""Tests for the Studio's named background jobs.

The runner has to hold three properties: a failing step records rather than
aborts the run, only one job holds the writer at a time, and a finished run
survives the server that produced it.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from padres_analytics.app import api, jobs
from padres_analytics.storage.schemas import initialize


@pytest.fixture(autouse=True)
def _clean_job_state() -> None:
    jobs.reset_for_tests()


def _wait_for_idle(timeout: float = 5.0) -> None:
    """Block until no job holds the slot."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if jobs.active_job() is None:
            return
        time.sleep(0.01)
    raise AssertionError("job did not finish within timeout")


def _holder(release: threading.Event) -> jobs.Worker:
    """A worker that blocks until released, to hold the job slot."""

    def worker(reporter: jobs.JobReporter) -> None:
        reporter.step("hold", lambda: (release.wait(timeout=5), "ok")[1])

    return worker


def _briefing_for(season: int) -> jobs.Worker:
    """A worker that records which season it ran for."""

    def worker(reporter: jobs.JobReporter) -> None:
        reporter.step("briefing", lambda: f"ran for {season}")

    return worker


def _scan_step(reporter: jobs.JobReporter) -> None:
    """A minimal one-step worker."""
    reporter.step("scan", lambda: "3 new")


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp padres.db that both the runner and the API point at."""
    db_path = tmp_path / "padres.db"
    conn = duckdb.connect(str(db_path))
    initialize(conn)
    conn.close()
    monkeypatch.setattr(api, "DUCKDB_PATH", db_path)
    monkeypatch.setattr("padres_analytics.config.DUCKDB_PATH", db_path)
    monkeypatch.setattr("padres_analytics.storage.db.DUCKDB_PATH", db_path)
    return db_path


def test_steps_record_progress_and_outcome(db: Path) -> None:
    def worker(reporter: jobs.JobReporter) -> None:
        reporter.step("first", lambda: "did a thing")
        reporter.summarize("all good")

    jobs.start("discover", worker, season=2026)
    _wait_for_idle()

    state = jobs.latest("discover")
    assert state is not None
    assert state["running"] is False and state["ok"] is True
    assert state["summary"] == "all good"
    assert state["steps"] == [{"name": "first", "ok": True, "detail": "did a thing"}]


def test_a_failing_step_is_recorded_and_the_chain_continues(db: Path) -> None:
    """A stale pull shouldn't cost you the detectors that would still have run."""
    ran_after = []

    def worker(reporter: jobs.JobReporter) -> None:
        reporter.step("boom", lambda: (_ for _ in ()).throw(RuntimeError("statcast stale")))
        reporter.step("after", lambda: ran_after.append(1) or "ran anyway")

    jobs.start("discover", worker)
    _wait_for_idle()

    state = jobs.latest("discover")
    assert state is not None
    assert ran_after == [1], "the step after a failure must still run"
    assert state["steps"][0]["ok"] is False
    assert "statcast stale" in state["steps"][0]["detail"]
    assert state["steps"][1]["ok"] is True
    assert state["ok"] is False, "a run with any failed step is not ok"


def test_a_worker_that_explodes_is_captured_not_swallowed(db: Path) -> None:
    def worker(reporter: jobs.JobReporter) -> None:
        raise ValueError("chain could not start")

    jobs.start("discover", worker)
    _wait_for_idle()

    state = jobs.latest("discover")
    assert state is not None
    assert state["ok"] is False
    assert "chain could not start" in state["steps"][0]["detail"]


def test_only_one_job_runs_at_a_time(db: Path) -> None:
    release = threading.Event()

    def slow(reporter: jobs.JobReporter) -> None:
        reporter.step("hold", lambda: (release.wait(timeout=5), "done")[1])

    jobs.start("sync", slow)
    try:
        with pytest.raises(jobs.JobBusyError) as caught:
            jobs.start("discover", _scan_step)
        assert caught.value.running == "sync"
    finally:
        release.set()
        _wait_for_idle()

    # The slot frees once the first job finishes.
    jobs.start("discover", _scan_step)
    _wait_for_idle()
    assert jobs.latest("discover") is not None


def test_a_finished_run_survives_the_server(db: Path) -> None:
    jobs.start("discover", _scan_step, season=2026)
    _wait_for_idle()
    jobs.reset_for_tests()  # as if the process restarted

    assert jobs.latest("discover") is None
    conn = duckdb.connect(str(db))
    try:
        persisted = jobs.last_persisted(conn, "discover")
    finally:
        conn.close()

    assert persisted is not None
    assert persisted["season"] == 2026 and persisted["ok"] is True
    assert persisted["steps"] == [{"name": "scan", "ok": True, "detail": "3 new"}]


def test_status_endpoint_falls_back_to_the_persisted_run(db: Path) -> None:
    jobs.start("discover", _scan_step)
    _wait_for_idle()
    jobs.reset_for_tests()

    body = TestClient(api.app).get("/api/actions/discover").json()
    assert body["running"] is False
    assert body["steps"][0]["detail"] == "3 new"


def test_status_endpoint_is_honest_when_nothing_has_run(db: Path) -> None:
    body = TestClient(api.app).get("/api/actions/discover").json()
    assert body["running"] is False and body["steps"] == []


def test_discover_endpoint_reports_a_conflict_rather_than_queueing(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    monkeypatch.setattr(
        api.chains,
        "sync_worker",
        lambda season: _holder(release),
    )
    client = TestClient(api.app)
    try:
        assert client.post("/api/actions/sync").json()["started"] is True
        blocked = client.post("/api/actions/discover").json()
        assert blocked == {"started": False, "running": True, "blocked_by": "sync"}
    finally:
        release.set()
        _wait_for_idle()


def test_discover_endpoint_starts_the_chain(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api.chains,
        "discovery_worker",
        lambda season: _briefing_for(season),
    )
    client = TestClient(api.app)

    started = client.post("/api/actions/discover?season=2026").json()
    assert started["started"] is True and started["season"] == 2026
    _wait_for_idle()

    body = client.get("/api/actions/discover").json()
    assert body["steps"][0]["detail"] == "ran for 2026"
