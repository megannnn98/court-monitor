"""Airtable, held still: the fake every test of the sync and the endpoint shares.

No test in this suite reaches the network. `install_fake_airtable` points the service
at a `FakeAirtable` and sets the environment it reads, so a test that forgets either
fails loudly instead of quietly skipping the sync.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from airtable.client import AirtableError, AirtableRecord
from airtable.files import FileTableClient, ImportSettings
from airtable.models import TABLES
from airtable.service import MODE_FILES, AirtableSyncService, SyncSource

CONFIGURED_ENV = {
    "AIRTABLE_TOKEN": "secret-token",
    "AIRTABLE_BASE_ID": "appTest",
    "AIRTABLE_SOURCES_TABLE": "Sources",
    "AIRTABLE_RFM_PERSONS_TABLE": "RFM",
    "AIRTABLE_KNOWN_PERSONS_TABLE": "Known",
    "AIRTABLE_OFFICIALS_TABLE": "Excluded",
    "AIRTABLE_ARTICLES_TABLE": "Articles",
}


class FakeAirtable:
    """Airtable's lists, as lists. A value that is an `AirtableError` is raised instead of
    returned, which is how a failing table is exercised offline."""

    def __init__(self, tables: dict[str, list[AirtableRecord] | AirtableError]) -> None:
        self.tables = tables
        self.requested: list[str] = []

    def list_records(self, table: str) -> list[AirtableRecord]:
        self.requested.append(table)
        answer = self.tables.get(table, [])
        if isinstance(answer, AirtableError):
            raise answer
        return list(answer)


@contextmanager
def configure_airtable(env: dict[str, str] | None = None) -> Iterator[None]:
    """`CONFIGURED_ENV` (or `env`) in the environment for the block, then gone."""
    wanted = CONFIGURED_ENV if env is None else env
    before = {name: os.environ.get(name) for name in wanted}
    try:
        for name, value in wanted.items():
            os.environ[name] = value
        yield
    finally:
        for name, saved in before.items():
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved


# The Airtable tables of the fake, named as the configuration names them. A source built
# here addresses them the way production does, so a fake keyed on "Known" still answers.
FAKE_TABLES = {
    "sources": CONFIGURED_ENV["AIRTABLE_SOURCES_TABLE"],
    "rfm_persons": CONFIGURED_ENV["AIRTABLE_RFM_PERSONS_TABLE"],
    "known_persons": CONFIGURED_ENV["AIRTABLE_KNOWN_PERSONS_TABLE"],
    "officials": CONFIGURED_ENV["AIRTABLE_OFFICIALS_TABLE"],
    "articles": CONFIGURED_ENV["AIRTABLE_ARTICLES_TABLE"],
}


def fake_source(fake: FakeAirtable, mode: str = "api") -> SyncSource:
    """A `SyncSource` reading `fake`, so a test builds the service the way the app does."""
    return SyncSource(mode=mode, client=fake, tables=FAKE_TABLES)


def file_source(directory: Path) -> SyncSource:
    """A `SyncSource` reading exported CSVs from `directory`.

    In file mode each list is addressed by the file's own stem, which is why this is not
    `fake_source` with a different client: the names a source answers to are the mode's.
    """
    return SyncSource(
        mode=MODE_FILES,
        client=FileTableClient(ImportSettings(directory)),
        tables={name: name for name in TABLES},
    )


def install_fake_airtable(
    session_factory: sessionmaker[Session],
    fake: FakeAirtable,
    *,
    env: dict[str, str] | None = None,
) -> AirtableSyncService:
    """A service that reads `fake` and writes through `session_factory`."""
    for name, value in (env or CONFIGURED_ENV).items():
        os.environ.setdefault(name, value)
    return AirtableSyncService(session_factory, fake_source(fake))


def no_lock() -> Iterator[bool]:
    """A lock nobody holds, for a test that is not about the lock."""
    yield True
