"""One button: bring the four Airtable lists into PostgreSQL.

The service owns the order, the logging and the failure policy. A table that fails is
reported and the rest still run — one broken base must not hide the three that are
fine. Airtable is a source here and nowhere else: the pipeline reads only PostgreSQL,
so an unavailable Airtable costs this one page, not the run.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from airtable import repository
from airtable.articles import sync_articles
from airtable.client import AirtableClient, AirtableError, AirtableRecord, HttpAirtableClient
from airtable.config import AirtableConfigurationError, AirtableSettings
from airtable.files import FileTableClient, ImportSettings
from airtable.links import FallbackTableClient, ShareTableClient, share_links
from airtable.models import (
    MODE_API,
    MODE_FILES,
    MODE_SHARE,
    TABLES,
    SyncReport,
    TableStatus,
    TableSyncResult,
)
from db.locks import try_advisory_lock

__all__ = [
    "AirtableSyncAlreadyRunningError",
    "AirtableSyncService",
    "SyncReport",
    "SyncSource",
    "build_sync_service",
    "build_sync_source",
]

logger = logging.getLogger("airtable")

# The lock key: one sync at a time, across every worker of every process.
LOCK_KEY = "airtable:sync"


class AirtableSyncAlreadyRunningError(RuntimeError):
    """Another worker holds the sync lock; the API answers 409."""


@dataclass(frozen=True)
class SyncSource:
    """Where the four lists come from, and under what name each is addressed.

    One object, so the sync itself cannot tell the two modes apart: it asks for a list by
    its own name and gets records back, whether those came from the Airtable API or from
    a file the operator exported.
    """

    mode: str
    client: AirtableClient
    tables: Mapping[str, str]

    def table_for(self, name: str) -> str:
        return self.tables[name]


class AirtableSyncService:
    """Runs one sync of the four lists. Constructed per request; holds no state."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        source: SyncSource,
        *,
        lock: Callable[[], AbstractContextManager[bool]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._source = source
        # A lock given here is the caller's (tests pass one that always refuses, to
        # exercise the conflict without a second connection); otherwise an advisory
        # lock on the session factory's engine.
        self._lock = lock

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
        report = SyncReport(started_at=started_at, mode=self._source.mode)
        logger.info(
            "event=airtable_sync started mode=%s tables=%s", self._source.mode, ",".join(TABLES)
        )
        # A client the source built is ours to close; one we were handed is not.
        owned = not isinstance(self._source.client, FileTableClient)
        client = self._source.client
        try:
            for table in TABLES:
                report.tables[table] = self._sync_one(client, table)
            rfm = report.tables["rfm_persons"]
            if rfm.status == TableStatus.SUCCESS:
                self._refresh_probable_matches()
        finally:
            if owned:
                close = getattr(client, "close", None)
                if close is not None:
                    close()
            report.finished_at = datetime.now(UTC)
        logger.info(
            "event=airtable_sync_finished mode=%s status=%s duration_ms=%d",
            self._source.mode,
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
        """One table. A failure to read it is that table's failure, not the sync's, and a
        list with no file is skipped rather than read as an empty one."""
        sync = _SYNC_BY_TABLE[table]
        try:
            records = client.list_records(self._source.table_for(table))
        except FileNotFoundError as exc:
            logger.info("event=airtable_sync_table_skipped table=%s file=%s", table, exc)
            return TableSyncResult(status=TableStatus.SKIPPED, error=f"файл не выгружен: {exc}")
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


_SYNC_BY_TABLE: dict[str, Callable[[Session, Sequence[AirtableRecord]], TableSyncResult]] = {
    "sources": repository.sync_sources,
    "rfm_persons": repository.sync_rfm_persons,
    "known_persons": repository.sync_known_persons,
    "officials": repository.sync_officials,
    "articles": sync_articles,
}


def build_sync_source(
    env: Mapping[str, str] | None = None,
    client: AirtableClient | None = None,
) -> SyncSource:
    """The source to sync from: the API if it is configured, else public share links, else
    exported files.

    The API is the better path — it is automatic and carries each record's id — so it
    wins whenever the six settings are present. Share links come next: they also need no
    one at the keyboard, and they work for a base that is only readable in a browser,
    which is the case that forced the file mode into existence. Files are the last
    resort, because a file means the operator exported it by hand.

    Raises `AirtableConfigurationError` when none is available; the caller turns that
    into a readable answer instead of a stack trace.
    """
    env = os.environ if env is None else env
    try:
        settings = AirtableSettings.from_env(env)
    except AirtableConfigurationError:
        links = share_links(env)
        if links:
            logger.info("event=airtable_sync_mode mode=share lists=%d", len(links))
            return SyncSource(
                mode=MODE_SHARE,
                client=client or ShareTableClient(links),
                tables={name: name for name in links},
            )
        import_files = FileTableClient(ImportSettings.from_env(env))
        if not import_files.present():
            raise
        logger.info("event=airtable_sync_mode mode=files dir=%s", import_files.directory)
        return SyncSource(
            mode=MODE_FILES,
            client=import_files,
            tables=dict(zip(TABLES, TABLES, strict=True)),
        )
    api_tables = {
        "sources": settings.sources_table,
        "rfm_persons": settings.rfm_persons_table,
        "known_persons": settings.known_persons_table,
        "officials": settings.officials_table,
        "articles": settings.articles_table,
    }
    return SyncSource(
        mode=MODE_API,
        client=FallbackTableClient(
            client or HttpAirtableClient(settings), share_links(env), api_tables
        ),
        tables=api_tables,
    )


def build_sync_service(
    session_factory: sessionmaker[Session],
    env: Mapping[str, str] | None = None,
    *,
    client: AirtableClient | None = None,
) -> AirtableSyncService:
    """A service with the configuration from the environment, in whichever mode it is
    set up. Raises `AirtableConfigurationError` when neither mode is available."""
    return AirtableSyncService(session_factory, build_sync_source(env, client=client))
