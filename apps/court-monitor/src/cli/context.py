"""The composition root of the CLI: settings, engine and session factory, built once.

Nothing is built until a command asks for it, so `--help`, `validate-config` and the
evaluations with their own disposable database never read DATABASE_URL or connect.
"""

from __future__ import annotations

from functools import cached_property

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from settings import ApplicationConfigurationError, ApplicationSettings


class CliContext:
    @cached_property
    def settings(self) -> ApplicationSettings:
        # Fail fast with every configuration problem, before touching the database.
        try:
            return ApplicationSettings.from_env()
        except ApplicationConfigurationError as exc:
            raise SystemExit(str(exc)) from None

    @cached_property
    def engine(self) -> Engine:
        return create_database_engine(self.settings.database_url, self.settings.database_pool)

    @cached_property
    def session_factory(self) -> sessionmaker[Session]:
        return create_session_factory(self.engine)
