from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from extraction_events import RuleBasedEventExtractor
from extraction_extractors import RuleBasedEntityExtractor
from extraction_models import ExtractionRunStatus
from extraction_normalizers import RuleBasedMentionNormalizer
from extraction_persistence import SqlAlchemyExtractionPersistence
from extraction_pipeline import ExtractionPipeline
from extraction_resolution_service import ExtractionResolutionService
from models import ParsedArticle, RawDocument
from orm_models import (
    EntityMentionRecord,
    PersonEventLinkRecord,
    PersonRecord,
)
from person_models import PersonStatus
from person_persistence import SqlAlchemyPersonPersistence
from person_resolver import RuleBasedPersonResolver
from sqlalchemy_persistence import SqlAlchemyIngestionPersistence


def _save_article(
    session_factory: sessionmaker[Session],
    *,
    text: str,
    external_id: str = "article-res-1",
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
    from orm_models import ParsedArticleRecord

    with session_factory() as session:
        article_id = session.scalar(select(ParsedArticleRecord.id))
    if article_id is None:
        raise AssertionError("parsed article was not saved")
    return article_id


def _run_extraction(
    session_factory: sessionmaker[Session],
    article_id: int,
) -> int:
    from extraction_documents import SqlAlchemyExtractionDocumentRepository

    repository = SqlAlchemyExtractionDocumentRepository(session_factory)
    document = repository.get_by_article_id(article_id)

    pipeline = ExtractionPipeline(
        extractors=[RuleBasedEntityExtractor()],
        normalizers=[RuleBasedMentionNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=SqlAlchemyExtractionPersistence(session_factory),
    )
    result = pipeline.run(document)
    if result.status is not ExtractionRunStatus.SUCCEEDED:
        raise AssertionError(f"Extraction failed: {result.error_message}")
    return result.run_id


def _make_resolution_service(
    session_factory: sessionmaker[Session],
) -> ExtractionResolutionService:
    person_persistence = SqlAlchemyPersonPersistence(session_factory)
    resolver = RuleBasedPersonResolver(person_persistence)
    return ExtractionResolutionService(
        persistence=person_persistence,
        resolver=resolver,
        session_factory=session_factory,
    )


def test_resolve_creates_persons_from_mentions(
    session_factory: sessionmaker[Session],
) -> None:
    article_id = _save_article(
        session_factory,
        text="Басманный суд арестовал Александра Иванова.",
    )
    run_id = _run_extraction(session_factory, article_id)
    service = _make_resolution_service(session_factory)

    stats = service.resolve_extraction_run(run_id)

    assert stats.mentions_processed > 0
    assert stats.mentions_resolved > 0
    assert stats.new_persons_created > 0

    with session_factory() as session:
        persons = session.scalars(select(PersonRecord)).all()
        resolved_mentions = session.scalars(
            select(EntityMentionRecord).where(
                EntityMentionRecord.person_id.is_not(None),
            )
        ).all()

    assert len(persons) >= 1
    assert len(resolved_mentions) >= 1


def test_resolve_links_events_to_persons(
    session_factory: sessionmaker[Session],
) -> None:
    article_id = _save_article(
        session_factory,
        text="Басманный суд арестовал Александра Иванова.",
        external_id="article-res-events",
    )
    run_id = _run_extraction(session_factory, article_id)
    service = _make_resolution_service(session_factory)

    stats = service.resolve_extraction_run(run_id)

    assert stats.events_linked > 0

    with session_factory() as session:
        links = session.scalars(select(PersonEventLinkRecord)).all()

    assert len(links) >= 1


def test_resolve_is_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    article_id = _save_article(
        session_factory,
        text="МВД задержало Ивана Петрова.",
        external_id="article-res-idempotent",
    )
    run_id = _run_extraction(session_factory, article_id)
    service = _make_resolution_service(session_factory)

    first = service.resolve_extraction_run(run_id)
    second = service.resolve_extraction_run(run_id)

    assert first.mentions_resolved == second.mentions_resolved

    with session_factory() as session:
        persons = session.scalars(
            select(PersonRecord).where(PersonRecord.status == PersonStatus.ACTIVE.value)
        ).all()

    assert len(persons) == first.new_persons_created


def test_resolve_same_person_across_articles(
    session_factory: sessionmaker[Session],
) -> None:
    article_id_1 = _save_article(
        session_factory,
        text="Александра Иванова задержали в Москве.",
        external_id="article-res-cross-1",
    )
    article_id_2 = _save_article(
        session_factory,
        text="Александру Иванова арестовал Басманный суд.",
        external_id="article-res-cross-2",
    )
    run_id_1 = _run_extraction(session_factory, article_id_1)
    run_id_2 = _run_extraction(session_factory, article_id_2)

    service = _make_resolution_service(session_factory)
    service.resolve_extraction_run(run_id_1)
    service.resolve_extraction_run(run_id_2)

    with session_factory() as session:
        persons = session.scalars(
            select(PersonRecord).where(PersonRecord.status == PersonStatus.ACTIVE.value)
        ).all()

    ivanov_persons = [p for p in persons if "иванов" in p.matching_key]
    assert len(ivanov_persons) == 1


def test_get_person_events_returns_linked_events(
    session_factory: sessionmaker[Session],
) -> None:
    article_id = _save_article(
        session_factory,
        text="Басманный суд арестовал Александра Иванова.",
        external_id="article-res-get-events",
    )
    run_id = _run_extraction(session_factory, article_id)
    service = _make_resolution_service(session_factory)
    service.resolve_extraction_run(run_id)

    with session_factory() as session:
        person = session.scalar(select(PersonRecord))
    assert person is not None

    events = service.get_person_events(person.id)
    assert len(events) >= 1


def test_get_person_mentions_returns_linked_mentions(
    session_factory: sessionmaker[Session],
) -> None:
    article_id = _save_article(
        session_factory,
        text="Александра Иванова задержали.",
        external_id="article-res-get-mentions",
    )
    run_id = _run_extraction(session_factory, article_id)
    service = _make_resolution_service(session_factory)
    service.resolve_extraction_run(run_id)

    with session_factory() as session:
        person = session.scalar(select(PersonRecord))
    assert person is not None

    mentions = service.get_person_mentions(person.id)
    assert len(mentions) >= 1
