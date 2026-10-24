"""The reads a stage after ingestion makes over every stored article (ADR 0031)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ingestion import read
from ingestion.sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from ingestion.storage.models import ParsedArticleRecord
from monitor_core.model import ParsedArticle, RawDocument


def _store(session_factory: sessionmaker[Session], texts: dict[str, str]) -> dict[str, int]:
    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )
    for external_id, text in texts.items():
        url = f"https://ovd.info/{external_id}"
        persistence.save(
            RawDocument(
                external_id=external_id,
                url=url,
                fetched_at=datetime(2026, 9, 15, tzinfo=UTC),
                content_type="text/html",
                content=text.encode(),
            ),
            ParsedArticle(
                external_id=external_id,
                url=url,
                title=external_id,
                published_at=datetime(2026, 9, 15, tzinfo=UTC),
                text=text,
            ),
        )
    with session_factory() as session:
        return {
            row.title: row.id
            for row in session.execute(
                select(ParsedArticleRecord.id, ParsedArticleRecord.title)
            ).all()
        }


def test_every_stored_article_is_listed_in_id_order(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        assert read.all_article_ids(session) == []
    stored = _store(session_factory, {"b": "второй", "a": "первый"})

    with session_factory() as session:
        assert read.all_article_ids(session) == sorted(stored.values())


def test_text_lengths_count_characters_and_leave_out_unknown_ids(
    session_factory: sessionmaker[Session],
) -> None:
    stored = _store(session_factory, {"cyrillic": "Иванов Иван", "latin": "abc"})
    wanted = [stored["cyrillic"], stored["latin"], stored["latin"], 999_999]

    with session_factory() as session:
        assert read.text_lengths(session, wanted) == {
            stored["cyrillic"]: len("Иванов Иван"),
            stored["latin"]: 3,
        }
        assert read.text_lengths(session, []) == {}
