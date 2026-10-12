"""A command-line context over a test's own session factory."""

from __future__ import annotations

from functools import cached_property

from sqlalchemy.orm import Session, sessionmaker

from cli.context import CliContext


class SessionContext(CliContext):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    @cached_property
    def session_factory(self) -> sessionmaker[Session]:
        return self._factory
