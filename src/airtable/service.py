"""One button: bring the four Airtable lists into PostgreSQL.

The service owns the order, the logging and the failure policy. A table that fails is
reported and the rest still run — one broken base must not hide the three that are
fine. Airtable is a source here and nowhere else: the pipeline reads only PostgreSQL,
so an unavailable Airtable costs this one page, not the run.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from airtable import repository
from airtable.client import AirtableClient, AirtableError, AirtableRecord, HttpAirtableClient
from airtable.config import AirtableSettings
from airtable.models import TABLES, SyncReport, TableStatus, TableSyncResult
from db.locks import try_advisory_lock

__all__ = [
    "AirtableSyncAlreadyRunningError",
    "AirtableSyncService",
    "SyncReport",
    "build_sync_service",
]

logger = logging.getLogger("airtable")

# The lock key: one sync at a time, across every worker of every process.
LOCK_KEY = "airtable:sync"


class AirtableSyncAlreadyRunningError(RuntimeError):
    """Another worker holds the sync lock; the API answers 409."""


class AirtableSyncService:
    """Runs one sync of the four lists. Constructed per request; holds no state."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: AirtableSettings,
        *,
        client: AirtableClient | None = None,
        lock: Callable[[], AbstractContextManager[bool]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        # A client given here is the caller's (a fake in tests, a shared one in
        # production); otherwise one is built per sync and closed with it.
        self._client = client
        # A lock given here is the caller's (tests pass one that always refuses, to
        # exercise the conflict without a second connection); otherwise an advisory
        # lock on the session factory's engine.
        self._lock = lock

    def _new_client(self) -> AirtableClient:
        """The client a sync reads through. A seam: a test replaces this one method."""
        return HttpAirtableClient(self._settings)

    def sync(self) -> SyncReport:
        """Sync every table, or raise `AirtableSyncAlreadyRunningError`."""
        with self._guard() as acquired:
            if not acquired:
                raise AirtableSyncAlreadyRunningError("Airtable synchronization is already running")
            return self._sync_all()

    @contextmanager
    def _guard(self) -> Iterator[bool]:
        if self._lock is not None:
            with self._lock() as acquired:
                yield bool(acquired)
            return
        engine: Engine = self._session_factory.kw["bind"]
        with try_advisory_lock(engine, LOCK_KEY) as acquired:
            yield acquired

    def _sync_all(self) -> SyncReport:
        started_at = datetime.now(UTC)
        report = SyncReport(started_at=started_at)
        logger.info("event=airtable_sync started tables=%s", ",".join(TABLES))
        # A client we built is ours to close; one we were handed is the caller's.
        owned = self._client is None
        client = self._client or self._new_client()
        try:
            for table in TABLES:
                report.tables[table] = self._sync_one(client, table)
            if not report.tables["rfm_persons"].failed:
                self._refresh_probable_matches()
        finally:
            if owned:
                close = getattr(client, "close", None)
                if close is not None:
                    close()
            report.finished_at = datetime.now(UTC)
        logger.info(
            "event=airtable_sync_finished status=%s duration_ms=%d",
            report.status,
            int(report.duration_seconds * 1000),
        )
        return report

    def _refresh_probable_matches(self) -> None:
        """Recompute the probable matches right after the list changed.

        A manual sync that left them stale would keep a person out of the candidates
        after an operator removed them from Airtable, and would keep a false candidate
        until the next monitoring run. This reads PostgreSQL only — it never goes back
        to Airtable — and a failure here is logged, not raised: the lists are in, and a
        stale secondary signal must not make the sync itself look failed.
        """
        from rosfinmonitoring.probable import AirtableProvisionalMatcher
        from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup

        official = SqlAlchemyRosfinmonitoringSnapshotLookup(
            self._session_factory
        ).latest_imported_snapshot()
        if official is None:
            # No published list to soften against: nothing to recompute.
            return
        try:
            probable = AirtableProvisionalMatcher(self._session_factory).apply(official.snapshot_id)
        except Exception as exc:  # noqa: BLE001 - an extra signal must not fail the sync
            logger.warning("event=airtable_sync_probable_failed error=%s", exc)
            return
        logger.info("event=airtable_sync_probable_refreshed probable=%d", probable)

    def _sync_one(self, client: AirtableClient, table: str) -> TableSyncResult:
        """One table. An Airtable failure is this table's failure, not the sync's."""
        sync = _SYNC_BY_TABLE[table]
        try:
            records = client.list_records(self._table_for(table))
        except AirtableError as exc:
            logger.warning("event=airtable_sync_table_failed table=%s error=%s", table, exc)
            return TableSyncResult(status=TableStatus.ERROR, error=str(exc), errors=1)
        try:
            with self._session_factory.begin() as session:
                result = sync(session, records)
        except Exception as exc:
            session_error = f"{type(exc).__name__}: {exc}"
            logger.exception("event=airtable_sync_table_failed table=%s", table)
            return TableSyncResult(status=TableStatus.ERROR, error=session_error, errors=1)
        logger.info(
            "event=airtable_sync_table table=%s received=%d created=%d updated=%d unchanged=%d"
            " errors=%d",
            table,
            result.received,
            result.created,
            result.updated,
            result.unchanged,
            result.errors,
        )
        return result

    def _table_for(self, table: str) -> str:
        return {
            "sources": self._settings.sources_table,
            "rfm_persons": self._settings.rfm_persons_table,
            "known_persons": self._settings.known_persons_table,
            "excluded_persons": self._settings.excluded_persons_table,
        }[table]


_SYNC_BY_TABLE: dict[str, Callable[[Session, Sequence[AirtableRecord]], TableSyncResult]] = {
    "sources": repository.sync_sources,
    "rfm_persons": repository.sync_rfm_persons,
    "known_persons": repository.sync_known_persons,
    "excluded_persons": repository.sync_excluded_persons,
}


def build_sync_service(
    session_factory: sessionmaker[Session],
    env: dict[str, str] | None = None,
) -> AirtableSyncService:
    """A service with the configuration from the environment.

    Raises `AirtableConfigurationError` when Airtable is not set up; the caller turns
    that into a readable answer instead of a stack trace.
    """
    return AirtableSyncService(
        session_factory, AirtableSettings.from_env(os.environ if env is None else env)
    )
