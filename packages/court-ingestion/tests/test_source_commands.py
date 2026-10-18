"""Source refresh preserves matching, bulk reads and caller-owned transactions."""

from typing import cast

from sqlalchemy import Table, create_engine, event, select
from sqlalchemy.orm import Session

from ingestion.commands import SourceDetails, refresh_source_details
from ingestion.storage.models import Source


def test_refresh_matches_once_keeps_blank_name_and_does_not_commit() -> None:
    engine = create_engine("sqlite://")
    try:
        cast(Table, Source.__table__).create(engine)
        with Session(engine) as session:
            session.add(Source(name="Original", base_url="https://EXAMPLE.test/", active=True))
            session.commit()
            queries: list[str] = []

            def count(*args: object) -> None:
                queries.append("query")

            event.listen(engine, "before_cursor_execute", count)
            result = refresh_source_details(
                session,
                [
                    SourceDetails("rec1", " https://example.test ", "", False),
                    SourceDetails("duplicate", "https://example.test/", "Other", True),
                    SourceDetails("missing", "https://missing.test", "Missing", True),
                ],
            )
            assert len(queries) == 1  # all matches are read in one query, not per entry
            assert (result.updated, result.unchanged) == (1, 0)
            assert result.unknown_urls == ("https://example.test/", "https://missing.test")
            source = session.scalars(select(Source)).one()
            assert (source.name, source.external_id, source.active) == ("Original", "rec1", False)
            session.rollback()
            source = session.scalars(select(Source)).one()
            assert source.active and source.external_id is None
            assert session.scalar(select(Source.name)) == "Original"
    finally:
        engine.dispose()


def test_refresh_reports_an_unchanged_source() -> None:
    engine = create_engine("sqlite://")
    try:
        cast(Table, Source.__table__).create(engine)
        with Session(engine) as session:
            session.add(
                Source(
                    name="News", base_url="https://example.test", external_id="rec1", active=True
                )
            )
            session.commit()
            result = refresh_source_details(
                session, [SourceDetails("rec1", "https://example.test", "News", True)]
            )
            assert (result.updated, result.unchanged, result.unknown_urls) == (0, 1, ())
    finally:
        engine.dispose()
