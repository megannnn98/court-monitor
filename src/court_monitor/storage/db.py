"""Database engine / session factory and schema bootstrap."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from court_monitor.config.settings import settings
from court_monitor.storage.orm import Base


def make_engine(url: str | None = None) -> Engine:
    db_url = url or settings.database_url
    connect_args: dict[str, object] = {}
    if db_url.startswith("sqlite"):
        # Allow shared thread access in tests / FastAPI; check_same_thread keeps
        # SQLite usable across the request threadpool.
        connect_args = {"check_same_thread": False}
    return create_engine(db_url, future=True, pool_pre_ping=True, connect_args=connect_args)


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
