"""One research request, one database snapshot.

`ResearchService.execute` filters person ids, then loads their details, classifications
and Rosfinmonitoring status. With a session per read, a classification or a match
written in between could put two moments into one answer. `SqlAlchemyResearchUnitOfWork`
runs all of it in one read-only REPEATABLE READ transaction: every read of the request
sees the same snapshot, and nothing is ever committed.

The transaction covers the deterministic PostgreSQL research only. LLM intake and
semantic retrieval in Qdrant happen before it (they produce the request and the
candidate ids); the report is built after it closes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.orm import Session, sessionmaker

from candidates.service import CandidateQueryService
from research.repository import SqlAlchemyPersonResearchRepository

if TYPE_CHECKING:
    from research.service import CandidateQuery, PersonResearchRepository

SNAPSHOT_ISOLATION_LEVEL = "REPEATABLE READ"


@dataclass(frozen=True)
class ResearchReaders:
    """The readers of one research request, all on the same snapshot."""

    repository: PersonResearchRepository
    candidate_query: CandidateQuery


class ResearchUnitOfWork(Protocol):
    def read(self) -> AbstractContextManager[ResearchReaders]: ...


@contextmanager
def read_only_snapshot(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """A session in a read-only REPEATABLE READ transaction, rolled back on exit.

    The isolation level is set before the first statement, so the snapshot is taken
    at the first read; the connection returns to the pool with its default level.
    """
    with session_factory() as session:
        session.connection(
            execution_options={
                "isolation_level": SNAPSHOT_ISOLATION_LEVEL,
                "postgresql_readonly": True,
            }
        )
        try:
            yield session
        finally:
            # Read-only: nothing to commit, on success or on an exception.
            session.rollback()


class SqlAlchemyResearchUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @contextmanager
    def read(self) -> Iterator[ResearchReaders]:
        with read_only_snapshot(self._session_factory) as session:
            yield ResearchReaders(
                repository=SqlAlchemyPersonResearchRepository(session),
                candidate_query=CandidateQueryService(session),
            )


class FixedResearchReaders:
    """Readers given ready-made (test doubles, or readers with their own sessions)."""

    def __init__(self, readers: ResearchReaders) -> None:
        self._readers = readers

    @contextmanager
    def read(self) -> Iterator[ResearchReaders]:
        yield self._readers
