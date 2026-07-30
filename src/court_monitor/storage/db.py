"""Database engine / session factory and schema bootstrap."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from court_monitor.config.settings import settings
from court_monitor.storage.orm import Base


def _configure_sqlite(engine: Engine) -> None:
    """Set the pragmas every SQLite connection in this project needs.

    ``foreign_keys=ON``: SQLite defaults it off, which silently disables every
    ``ondelete="CASCADE"`` declared in orm.py. The ORM declares
    ``passive_deletes=True`` on those relationships, i.e. it deliberately does
    *not* delete children itself and expects the database to. With the pragma
    off neither side deletes anything, and dropping a document's facts
    (``reprocess``) left its MatchCandidate rows pointing at ids that no longer
    exist.

    ``journal_mode=WAL``: in the default rollback journal a writer blocks all
    readers. A background job holds its transaction open for as long as it
    takes to fetch every source, so an operator opening the UI mid-run hit
    "database is locked" — reproduced in ~5s against the default journal. WAL
    lets readers through while a write is in flight.

    WAL does not make two *writers* concurrent, so a longer ``busy_timeout``
    covers the remaining case: a UI write during a job now waits rather than
    failing outright. Real concurrency needs PostgreSQL (see docker-compose).
    """

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def make_engine(url: str | None = None) -> Engine:
    db_url = url or settings.database_url
    connect_args: dict[str, object] = {}
    if db_url.startswith("sqlite"):
        # Allow shared thread access in tests / FastAPI; check_same_thread keeps
        # SQLite usable across the request threadpool.
        connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(db_url, future=True, pool_pre_ping=True, connect_args=connect_args)
    if db_url.startswith("sqlite"):
        _configure_sqlite(engine)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Context manager yielding a Session; commits on success, rolls back on error."""
    eng = engine or make_engine()
    factory = make_session_factory(eng)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all(engine: Engine | None = None) -> None:
    """Create all tables directly from metadata (used by tests and dev bootstrap)."""
    Base.metadata.create_all(engine or make_engine())
