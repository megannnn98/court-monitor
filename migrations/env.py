"""Alembic environment.

Reads the database URL from court_monitor settings (CM_DATABASE_URL / .env)
so migrations use the same configuration as the application. Targets the same
``Base.metadata`` as the ORM models.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure the package (src layout) is importable when alembic runs.
from court_monitor.config.settings import settings  # noqa: F401  (side-effect import)
from court_monitor.storage.orm import Base

config = context.config

# Inject URL from application settings unless alembic.ini/-x overrides it.
if config.get_main_option("sqlalchemy.url") is None:
    config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
