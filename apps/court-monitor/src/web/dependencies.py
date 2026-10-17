"""FastAPI dependencies shared by every route: the database session and the services built once."""

import os
import sys
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from db.database import DatabasePoolSettings, create_database_engine, create_session_factory
from orchestrator.models import (
    MonitoringSettings,
)
from orchestrator.operations import (
    CommandLine,
    OperationRegistry,
)
from web.health import (
    REPO_ROOT,
    ReadinessChecker,
    expected_schema_revision,
)

# An operation runs `main.py`, the application's command line, from the repository.
OPERATION_COMMAND_LINE = CommandLine(
    program=(sys.executable, str(Path(__file__).resolve().parents[1] / "main.py")),
    cwd=REPO_ROOT,
)


# Database dependency
@lru_cache(maxsize=1)
def _get_session_factory() -> sessionmaker[Session]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    engine = create_database_engine(database_url, DatabasePoolSettings.from_env())
    return create_session_factory(engine)


def session_factory_for(db: Session) -> sessionmaker[Session]:
    """A session factory on the request session's engine, for services that open their
    own sessions. The one place routes get one: it follows an overridden `get_db`."""
    return sessionmaker(bind=db.get_bind())


def get_db() -> Iterator[Session]:
    """Get database session."""
    try:
        session_factory = _get_session_factory()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    with session_factory() as session:
        try:
            yield session
        finally:
            session.close()


def get_operation_registry(db: Session = Depends(get_db)) -> OperationRegistry:  # noqa: B008
    """Runs live in PostgreSQL, so a registry holds no state of its own: one per request,
    on the engine of the request's session (an overridden `get_db` included)."""
    return OperationRegistry(session_factory_for(db), command_line=OPERATION_COMMAND_LINE)


def get_readiness_checker() -> ReadinessChecker:
    try:
        session_factory: sessionmaker[Session] | None = _get_session_factory()
    except RuntimeError:
        session_factory = None
    return ReadinessChecker(
        session_factory,
        expected_revision=expected_schema_revision(),
        stale_run_after=MonitoringSettings.from_env().stale_run_after,
    )
