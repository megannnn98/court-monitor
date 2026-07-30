"""Background execution of the long pipeline operations.

Fetching every source takes minutes — far longer than a request may be held
open — so the UI submits work here and polls the ``jobs`` table for its state.

**One worker, deliberately.** ``max_workers=1`` is not a placeholder for a
bigger pool: SQLite serialises writers and hands out "database is locked"
otherwise, and two concurrent ``run_all`` passes would hammer every source
twice for no benefit. The queue is the feature.

Jobs get their own Session. A request-scoped one would be closed the moment
the response is sent, long before the work finishes.

Nothing here survives a restart — a killed process leaves rows stuck in
``running``. :func:`recover_stale_jobs` marks those as interrupted at startup
rather than letting the UI show work that will never finish.
"""

from __future__ import annotations

import json
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from court_monitor.config.loader import load_monitoring, load_sources
from court_monitor.config.registry import load_registry
from court_monitor.matching.candidates import generate_matches
from court_monitor.observability import get_logger
from court_monitor.services import (
    SourceStats,
    import_rfm_records,
    process_pending,
    process_registry_source,
    process_source,
)
from court_monitor.sources.fedsfm_live import (
    LIST_URL,
    fetch_live_html,
    parse_terrorists_html,
)
from court_monitor.storage.db import make_engine, make_session_factory, session_scope
from court_monitor.storage.orm import Job

_log = get_logger("web.jobs")

ACTIVE_STATUSES = ("queued", "running")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cm-job")
_submit_lock = threading.Lock()


class JobRejected(RuntimeError):
    """Refused to queue the job — usually because the same kind is running."""


# ---------------------------------------------------------------------------
# The operations themselves
# ---------------------------------------------------------------------------


def _job_fetch_all(session: Session, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch every sudrf source and Telegram channel, parsing as we go."""
    live = bool(params.get("live"))
    monitoring = load_monitoring()
    totals = SourceStats()
    per_source: dict[str, str] = {}

    for src in (s for s in load_sources() if s.enabled):
        try:
            stats = process_source(session, src, monitoring)
            totals.accumulate(stats)
            per_source[src.name] = stats.summary()
        except Exception as exc:
            totals.failed += 1
            per_source[src.name] = f"ОШИБКА {type(exc).__name__}: {exc}"

    for entry in (e for e in load_registry() if e.enabled):
        try:
            stats = process_registry_source(session, entry, monitoring, live=live)
            totals.accumulate(stats)
            per_source[entry.id] = stats.summary()
        except Exception as exc:
            totals.failed += 1
            per_source[entry.id] = f"ОШИБКА {type(exc).__name__}: {exc}"

    return {"totals": totals.as_dict(), "sources": per_source}


def _job_parse_pending(session: Session, _params: dict[str, Any]) -> dict[str, Any]:
    stats = process_pending(session)
    return {
        "parsed": stats.parsed,
        "irrelevant": stats.irrelevant,
        "failed": stats.failed,
    }


def _job_generate_matches(session: Session, _params: dict[str, Any]) -> dict[str, Any]:
    return dict(generate_matches(session))


def _job_import_rfm(session: Session, params: dict[str, Any]) -> dict[str, Any]:
    """Download and import the live Rosfinmonitoring list.

    ``replace`` exists because the deduplication key includes birth_date, and
    the CSV export carries none: importing on top of a CSV-loaded registry
    doubles every person instead of merging them.
    """
    html = fetch_live_html()
    result = parse_terrorists_html(html)
    if not result.rows:
        raise RuntimeError("Не распознано ни одной записи — вероятно, изменилась вёрстка страницы")

    replaced = 0
    if params.get("replace"):
        from court_monitor.storage.orm import PersonRecord  # noqa: PLC0415 - avoids import cycle

        existing = session.execute(select(PersonRecord).where(PersonRecord.source == "rfm"))
        for record in existing.scalars():
            session.delete(record)
            replaced += 1
        session.flush()

    stats = import_rfm_records(session, result.rows, source="rfm", source_url=LIST_URL)
    return {
        "total_on_page": result.total_records,
        "recognized": result.recognized,
        "unrecognized": result.unrecognized,
        "imported": stats.imported,
        "updated": stats.updated,
        "duplicates": stats.duplicates,
        "deleted_before_import": replaced,
    }


def _job_run_all(session: Session, params: dict[str, Any]) -> dict[str, Any]:
    fetch = _job_fetch_all(session, params)
    matches = _job_generate_matches(session, params)
    return {**fetch, "matches": matches}


JOB_KINDS: dict[str, dict[str, Any]] = {
    "run_all": {
        "label": "Собрать всё и сопоставить",
        "runner": _job_run_all,
        "description": "Все источники и каналы, затем поиск совпадений.",
    },
    "fetch_all": {
        "label": "Собрать источники",
        "runner": _job_fetch_all,
        "description": "Загрузка и разбор без поиска совпадений.",
    },
    "parse_pending": {
        "label": "Разобрать необработанные",
        "runner": _job_parse_pending,
        "description": "Повторный разбор документов в статусе pending.",
    },
    "import_rfm": {
        "label": "Загрузить перечень РФМ",
        "runner": _job_import_rfm,
        "description": "Живая загрузка с fedsfm.ru.",
    },
    "generate_matches": {
        "label": "Найти совпадения",
        "runner": _job_generate_matches,
        "description": "Сопоставление извлечённых имён с реестром.",
    },
}


# ---------------------------------------------------------------------------
# Submission and execution
# ---------------------------------------------------------------------------


def submit(session: Session, kind: str, *, actor: str, params: dict[str, Any] | None = None) -> Job:
    """Queue a job. Raises :class:`JobRejected` if one of this kind is active."""
    if kind not in JOB_KINDS:
        raise JobRejected(f"Неизвестная операция: {kind}")

    with _submit_lock:
        active = session.execute(
            select(Job).where(Job.kind == kind, Job.status.in_(ACTIVE_STATUSES))
        ).scalars()
        if next(iter(active), None) is not None:
            raise JobRejected("Такая операция уже выполняется")

        job = Job(
            kind=kind,
            status="queued",
            actor=actor,
            params_json=json.dumps(params or {}, ensure_ascii=False),
        )
        session.add(job)
        session.commit()
        job_id = job.id

    _executor.submit(_execute, job_id)
    _log.info("web.job.queued", job_id=job_id, kind=kind, actor=actor)
    return job


def _execute(job_id: int) -> None:
    """Run one job in the worker thread, against its own session.

    The engine is disposed in ``finally``: this runs once per submitted job for
    the life of the process, so leaving pools behind would accumulate
    connections across a long-lived UI session.
    """
    engine = make_engine()
    try:
        _execute_with(engine, job_id)
    finally:
        engine.dispose()


def _execute_with(engine: Engine, job_id: int) -> None:
    factory = make_session_factory(engine)

    with factory() as session:
        job = session.get(Job, job_id)
        if job is None:  # pragma: no cover - deleted between submit and start
            return
        kind = job.kind
        params = json.loads(job.params_json or "{}")
        job.status = "running"
        job.started_at = datetime.now(UTC)
        session.commit()

    runner: Callable[[Session, dict[str, Any]], dict[str, Any]] = JOB_KINDS[kind]["runner"]
    result: dict[str, Any] | None = None
    error: str | None = None

    try:
        with session_scope(engine) as work_session:
            result = runner(work_session, params)
    except Exception as exc:
        # The traceback is the only record an operator gets of why a background
        # run failed, so keep it rather than just the message.
        error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
        _log.exception("web.job.failed", job_id=job_id, kind=kind)

    with factory() as session:
        job = session.get(Job, job_id)
        if job is None:  # pragma: no cover
            return
        job.status = "failed" if error else "succeeded"
        job.result_json = json.dumps(result, ensure_ascii=False) if result is not None else None
        job.error = error
        job.finished_at = datetime.now(UTC)
        session.commit()

    _log.info("web.job.finished", job_id=job_id, kind=kind, failed=bool(error))


def recover_stale_jobs(session: Session) -> int:
    """Mark jobs left ``running`` by a killed process, so the UI stops waiting."""
    stale = list(session.execute(select(Job).where(Job.status.in_(ACTIVE_STATUSES))).scalars())
    for job in stale:
        job.status = "failed"
        job.error = "Прервана: процесс завершился во время выполнения."
        job.finished_at = datetime.now(UTC)
    if stale:
        session.commit()
        _log.warning("web.jobs.recovered_stale", count=len(stale))
    return len(stale)


def list_jobs(session: Session, *, limit: int = 50) -> list[Job]:
    stmt = select(Job).order_by(Job.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def count_active(session: Session) -> int:
    return len(list(session.execute(select(Job).where(Job.status.in_(ACTIVE_STATUSES))).scalars()))
