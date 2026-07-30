"""Unit tests: background job runner.

The runner itself is exercised synchronously — ``_execute`` is called directly
rather than through the executor — so tests never depend on thread timing.
``_execute`` opens its own engine from settings, so these tests point
CM_DATABASE_URL at a temporary file for the duration.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from court_monitor.config import settings as settings_module
from court_monitor.services import ReprocessAllStats
from court_monitor.storage.orm import Base, Job
from court_monitor.web import jobs


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A file-backed database that both the session and _execute can reach."""
    url = f"sqlite:///{tmp_path / 'jobs.db'}"
    monkeypatch.setenv("CM_DATABASE_URL", url)

    monkeypatch.setattr(settings_module.settings, "database_url", url)

    engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _register(monkeypatch, name: str, runner) -> None:
    monkeypatch.setitem(jobs.JOB_KINDS, name, {"label": name, "runner": runner, "description": ""})


def test_submit_creates_a_queued_job(db, monkeypatch):
    monkeypatch.setattr(jobs._executor, "submit", lambda *a, **k: None)
    job = jobs.submit(db, "generate_matches", actor="tester")
    assert job.status == "queued"
    assert job.actor == "tester"
    assert json.loads(job.params_json) == {}


def test_unknown_kind_is_rejected(db):
    with pytest.raises(jobs.JobRejected, match="Неизвестная операция"):
        jobs.submit(db, "does_not_exist", actor="tester")


def test_second_job_of_the_same_kind_is_refused(db, monkeypatch):
    """Two concurrent run_all passes would fetch every source twice, and
    SQLite serialises writers anyway."""
    monkeypatch.setattr(jobs._executor, "submit", lambda *a, **k: None)
    jobs.submit(db, "generate_matches", actor="tester")
    with pytest.raises(jobs.JobRejected, match="уже выполняется"):
        jobs.submit(db, "generate_matches", actor="tester")


def test_a_different_kind_may_be_queued_alongside(db, monkeypatch):
    monkeypatch.setattr(jobs._executor, "submit", lambda *a, **k: None)
    jobs.submit(db, "generate_matches", actor="tester")
    jobs.submit(db, "parse_pending", actor="tester")  # must not raise


def test_finished_job_no_longer_blocks_resubmission(db, monkeypatch):
    monkeypatch.setattr(jobs._executor, "submit", lambda *a, **k: None)
    job = jobs.submit(db, "generate_matches", actor="tester")
    job.status = "succeeded"
    db.commit()
    jobs.submit(db, "generate_matches", actor="tester")  # must not raise


def test_execute_records_the_result(db, monkeypatch):
    monkeypatch.setattr(jobs._executor, "submit", lambda *a, **k: None)
    _register(monkeypatch, "probe", lambda _s, _p: {"did": "work"})
    job = jobs.submit(db, "probe", actor="tester")

    jobs._execute(job.id)

    db.expire_all()
    stored = db.get(Job, job.id)
    assert stored.status == "succeeded"
    assert json.loads(stored.result_json) == {"did": "work"}
    assert stored.error is None
    assert stored.started_at is not None
    assert stored.finished_at is not None


def test_execute_records_the_traceback_on_failure(db, monkeypatch):
    """A background failure has no console to print to — the row is the only
    place an operator can find out what happened."""
    monkeypatch.setattr(jobs._executor, "submit", lambda *a, **k: None)

    def _boom(_session, _params):
        raise ValueError("не сложилось")

    _register(monkeypatch, "boom", _boom)
    job = jobs.submit(db, "boom", actor="tester")

    jobs._execute(job.id)

    db.expire_all()
    stored = db.get(Job, job.id)
    assert stored.status == "failed"
    assert "ValueError: не сложилось" in stored.error
    assert "Traceback" in stored.error
    assert stored.finished_at is not None


def test_params_reach_the_runner(db, monkeypatch):
    monkeypatch.setattr(jobs._executor, "submit", lambda *a, **k: None)
    seen: dict = {}
    _register(monkeypatch, "echo", lambda _s, p: seen.update(p) or {"ok": True})

    job = jobs.submit(db, "echo", actor="tester", params={"live": True, "replace": False})
    jobs._execute(job.id)

    assert seen == {"live": True, "replace": False}


def test_recover_stale_jobs_fails_interrupted_rows(db, monkeypatch):
    """A killed process leaves rows in running forever; the UI would show work
    that is never coming back."""
    monkeypatch.setattr(jobs._executor, "submit", lambda *a, **k: None)
    running = jobs.submit(db, "generate_matches", actor="tester")
    running.status = "running"
    db.commit()

    assert jobs.recover_stale_jobs(db) == 1

    db.expire_all()
    stored = db.get(Job, running.id)
    assert stored.status == "failed"
    assert "Прервана" in stored.error


def test_recover_stale_jobs_leaves_finished_rows_alone(db, monkeypatch):
    monkeypatch.setattr(jobs._executor, "submit", lambda *a, **k: None)
    job = jobs.submit(db, "generate_matches", actor="tester")
    job.status = "succeeded"
    db.commit()

    assert jobs.recover_stale_jobs(db) == 0
    db.expire_all()
    assert db.get(Job, job.id).status == "succeeded"


def test_count_active_and_listing(db, monkeypatch):
    monkeypatch.setattr(jobs._executor, "submit", lambda *a, **k: None)
    jobs.submit(db, "generate_matches", actor="tester")
    assert jobs.count_active(db) == 1
    assert len(jobs.list_jobs(db)) == 1


def test_reprocess_all_job_defaults_to_refusing(db, monkeypatch):
    """The web runner must inherit the CLI's guard rather than quietly forcing:
    a job started from a browser can destroy operator decisions just as well."""
    seen: dict[str, object] = {}

    def _fake(_session, *, force):
        seen["force"] = force
        return ReprocessAllStats()

    monkeypatch.setattr(jobs, "reprocess_all", _fake)

    jobs._job_reprocess_all(db, {})
    assert seen["force"] is False

    jobs._job_reprocess_all(db, {"force": True})
    assert seen["force"] is True
