"""The ingestion's explicit testing API: fixtures and reads of its tables for the
evaluation and tests.

Not for production code (`tests/test_data_ownership.py` holds every production module
to it; ADR 0031, «Evaluation»).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ingestion.storage.models import ParsedArticleRecord, Source, SourceDocument

# The tables a disposable test or evaluation database is wiped of between runs
# (`db.maintenance.truncate_disposable_tables`); a cache of model answers worth keeping
# is not among them.
DISPOSABLE_TABLES = (
    "parsed_articles",
    "source_documents",
    "sources",
)


def source_named(session: Session, name: str, base_url: str) -> int:
    """The id of the source of this name, added with `base_url` when there is none."""
    found = session.scalar(select(Source.id).where(Source.name == name))
    if found is not None:
        return found
    source = Source(name=name, base_url=base_url)
    session.add(source)
    session.flush()
    return source.id


def add_article(
    session: Session,
    source_id: int,
    *,
    external_id: str,
    url: str,
    fetched_at: datetime,
    title: str,
    text: str,
    published_at: datetime | None = None,
) -> int:
    """A plain-text document of the source and its parsed article; the article's id."""
    document = SourceDocument(
        source_id=source_id,
        external_id=external_id,
        canonical_url=url,
        fetched_at=fetched_at,
        content_type="text/plain",
        raw_content=text.encode(),
    )
    session.add(document)
    session.flush()
    article = ParsedArticleRecord(
        document_id=document.id, title=title, published_at=published_at, text=text
    )
    session.add(article)
    session.flush()
    return article.id


def every_article(session: Session) -> list[tuple[int, str, str, str, str, str]]:
    """(id, source base URL, external id, title, text, URL) of every article."""
    return [
        (row[0], row[1], row[2], row[3], row[4], row[5])
        for row in session.execute(
            select(
                ParsedArticleRecord.id,
                Source.base_url,
                SourceDocument.external_id,
                ParsedArticleRecord.title,
                ParsedArticleRecord.text,
                SourceDocument.canonical_url,
            )
            .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
            .join(Source, Source.id == SourceDocument.source_id)
        )
    ]
