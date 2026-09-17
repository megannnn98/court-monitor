from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
)
from extraction.documents import SqlAlchemyExtractionDocumentRepository
from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.models import ExtractionRunStatus
from extraction.normalizers import RuleBasedMentionNormalizer
from extraction.persistence import SqlAlchemyExtractionPersistence
from extraction.pipeline import ExtractionPipeline
from sources.models import ParsedArticle, RawDocument
from sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence


def _save_article(
    session_factory: sessionmaker[Session],
    *,
    text: str,
    external_id: str = "article-1",
) -> int:
    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )
    persistence.save(
        RawDocument(
            external_id=external_id,
            url=f"https://ovd.info/{external_id}",
            fetched_at=datetime(2026, 9, 13, tzinfo=UTC),
            content_type="text/html",
            content=b"<html></html>",
        ),
        ParsedArticle(
            external_id=external_id,
            url=f"https://ovd.info/{external_id}",
            title="Test",
            published_at=datetime(2026, 9, 13, tzinfo=UTC),
            text=text,
        ),
    )
    return _article_id(session_factory)


def _article_id(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        article_id = session.scalar(select(ParsedArticleRecord.id))
    if article_id is None:
        raise AssertionError("parsed article was not saved")
    return article_id


def _pipeline(session_factory: sessionmaker[Session]) -> ExtractionPipeline:
    return ExtractionPipeline(
        extractors=[RuleBasedEntityExtractor()],
        normalizers=[RuleBasedMentionNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=SqlAlchemyExtractionPersistence(session_factory),
    )


def test_extraction_persistence_saves_successful_run_mentions_events_and_links(
    session_factory: sessionmaker[Session],
) -> None:
    _save_article(
        session_factory,
        text="Басманный районный суд Москвы арестовал Александра Иванова по ст. 207.3 УК РФ.",
    )
    article_id = _article_id(session_factory)
    document = SqlAlchemyExtractionDocumentRepository(session_factory).get_by_article_id(article_id)

    result = _pipeline(session_factory).run(document)

    assert result.status is ExtractionRunStatus.SUCCEEDED
    with session_factory() as session:
        runs = session.scalars(select(ArticleExtractionRunRecord)).all()
        mentions = session.scalars(select(EntityMentionRecord)).all()
        events = session.scalars(select(ExtractedEventRecord)).all()
        links = session.scalars(select(EventEntityMentionRecord)).all()
    assert len(runs) == 1
    assert mentions
    assert events
    assert links


def test_extraction_persistence_is_idempotent_for_same_content_hash(
    session_factory: sessionmaker[Session],
) -> None:
    _save_article(session_factory, text="МВД задержало Ивана Петрова по КоАП РФ.")
    article_id = _article_id(session_factory)
    repository = SqlAlchemyExtractionDocumentRepository(session_factory)
    pipeline = _pipeline(session_factory)

    first = pipeline.run(repository.get_by_article_id(article_id))
    second = pipeline.run(repository.get_by_article_id(article_id))

    assert first.run_id == second.run_id
    assert second.skipped_existing is True
    assert second.mentions_created == 0
    assert second.events_created == 0
    with session_factory() as session:
        assert len(session.scalars(select(ArticleExtractionRunRecord)).all()) == 1


def test_extraction_persistence_creates_new_run_for_changed_content_hash(
    session_factory: sessionmaker[Session],
) -> None:
    article_id = _save_article(session_factory, text="МВД задержало Ивана Петрова.")
    repository = SqlAlchemyExtractionDocumentRepository(session_factory)
    pipeline = _pipeline(session_factory)
    first = pipeline.run(repository.get_by_article_id(article_id))
    _save_article(
        session_factory,
        text="МВД задержало Ивана Петрова по ст. 282 УК РФ.",
    )

    second = pipeline.run(repository.get_by_article_id(article_id))

    assert second.run_id != first.run_id
    with session_factory() as session:
        assert len(session.scalars(select(ArticleExtractionRunRecord)).all()) == 2


def test_extraction_persistence_saves_failed_run(
    session_factory: sessionmaker[Session],
) -> None:
    article_id = _save_article(session_factory, text="Текст без сущностей.")
    document = SqlAlchemyExtractionDocumentRepository(session_factory).get_by_article_id(article_id)
    persistence = SqlAlchemyExtractionPersistence(session_factory)

    result = persistence.save_failed(
        document,
        extractor_name="fake",
        extractor_version="1",
        normalizer_version="1",
        error_message="broken",
    )

    assert result.status is ExtractionRunStatus.FAILED
    with session_factory() as session:
        run = session.scalar(select(ArticleExtractionRunRecord))
    assert run is not None
    assert run.error_message == "broken"


def test_save_failed_does_not_overwrite_existing_successful_run(
    session_factory: sessionmaker[Session],
) -> None:
    article_id = _save_article(
        session_factory,
        text="Басманный районный суд Москвы арестовал Александра Иванова по ст. 207.3 УК РФ.",
    )
    document = SqlAlchemyExtractionDocumentRepository(session_factory).get_by_article_id(article_id)
    pipeline = _pipeline(session_factory)
    successful = pipeline.run(document)
    persistence = SqlAlchemyExtractionPersistence(session_factory)

    versions = pipeline.versions
    failed = persistence.save_failed(
        document,
        extractor_name=versions.extractor_name,
        extractor_version=versions.extractor_version,
        normalizer_version=versions.normalizer_version,
        error_message="duplicate key value violates unique constraint",
    )

    assert failed.run_id == successful.run_id
    assert failed.status is ExtractionRunStatus.SUCCEEDED
    assert failed.skipped_existing is True
    assert failed.mentions_created == 0
    assert failed.events_created == 0
    with session_factory() as session:
        run = session.scalar(select(ArticleExtractionRunRecord))
        mentions = session.scalars(select(EntityMentionRecord)).all()
        events = session.scalars(select(ExtractedEventRecord)).all()
    assert run is not None
    assert run.status == ExtractionRunStatus.SUCCEEDED.value
    assert run.error_message is None
    assert mentions
    assert events


def test_save_replaces_existing_failed_run(
    session_factory: sessionmaker[Session],
) -> None:
    article_id = _save_article(
        session_factory,
        text="Басманный районный суд Москвы арестовал Александра Иванова по ст. 207.3 УК РФ.",
    )
    document = SqlAlchemyExtractionDocumentRepository(session_factory).get_by_article_id(article_id)
    persistence = SqlAlchemyExtractionPersistence(session_factory)
    pipeline = _pipeline(session_factory)
    versions = pipeline.versions
    failed = persistence.save_failed(
        document,
        extractor_name=versions.extractor_name,
        extractor_version=versions.extractor_version,
        normalizer_version=versions.normalizer_version,
        error_message="temporary failure",
    )

    successful = pipeline.run(document)

    assert successful.run_id == failed.run_id
    assert successful.status is ExtractionRunStatus.SUCCEEDED
    assert successful.mentions_created > 0
    assert successful.events_created > 0
    with session_factory() as session:
        run = session.scalar(select(ArticleExtractionRunRecord))
        mentions = session.scalars(select(EntityMentionRecord)).all()
        events = session.scalars(select(ExtractedEventRecord)).all()
    assert run is not None
    assert run.status == ExtractionRunStatus.SUCCEEDED.value
    assert run.error_message is None
    assert mentions
    assert events
