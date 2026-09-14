"""Seed canonical persons for ER v2 tests."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ParsedArticleRecord,
    SourceDocument,
)
from extraction.normalizers import RuleBasedMentionNormalizer
from persons.models import AliasOrigin
from persons.persistence import SqlAlchemyPersonPersistence
from sources.models import ParsedArticle, RawDocument
from sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence


def matching_key(name: str) -> str:
    return RuleBasedMentionNormalizer._matching_key(name)


def seed_person(
    session_factory: sessionmaker[Session], name: str, *, aliases: tuple[str, ...] = ()
) -> int:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = persistence.create_person(
        canonical_name=name, normalized_name=name, matching_key=matching_key(name)
    )
    for alias in aliases:
        persistence.create_alias(
            person_id=person_id,
            surface_text=alias,
            normalized_text=alias,
            matching_key=matching_key(alias),
            origin=AliasOrigin.MANUAL,
            confidence=1.0,
        )
    return person_id


_run_counter = 0


def seed_mentions(session_factory: sessionmaker[Session], *surfaces: str) -> tuple[int, list[int]]:
    """An article with one extraction run holding a person mention per surface form."""
    global _run_counter
    _run_counter += 1
    external_id = f"er-article-{_run_counter}-{datetime.now(UTC).timestamp()}"
    SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    ).save(
        RawDocument(
            external_id=external_id,
            url=f"https://ovd.info/{external_id}",
            fetched_at=datetime(2026, 9, 15, tzinfo=UTC),
            content_type="text/html",
            content=b"<html></html>",
        ),
        ParsedArticle(
            external_id=external_id,
            url=f"https://ovd.info/{external_id}",
            title="ER",
            published_at=datetime(2026, 9, 15, tzinfo=UTC),
            text=" ".join(surfaces),
        ),
    )
    normalizer = RuleBasedMentionNormalizer()
    with session_factory.begin() as session:
        article_id = session.scalar(
            select(ParsedArticleRecord.id)
            .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
            .where(SourceDocument.external_id == external_id)
        )
        assert article_id is not None
        run = ArticleExtractionRunRecord(
            article_id=article_id,
            article_content_hash=external_id,
            extractor_name="test",
            extractor_version="1",
            normalizer_version="1",
            status="succeeded",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        mention_ids = []
        offset = 0
        for surface in surfaces:
            normalized, data = normalizer.normalize_person(surface)
            mention = EntityMentionRecord(
                extraction_run_id=run.id,
                entity_type="person",
                surface_text=surface,
                normalized_text=normalized,
                start_offset=offset,
                end_offset=offset + len(surface),
                confidence=0.72,
                normalized_data=data.model_dump(),
                extractor_name="test",
                extractor_version="1",
                normalizer_version="1",
            )
            offset += len(surface) + 1
            session.add(mention)
            session.flush()
            mention_ids.append(mention.id)
        return run.id, mention_ids
