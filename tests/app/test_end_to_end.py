"""End-to-end golden test for the court-monitor pipeline."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from candidates.service import CandidateQueryService
from db.orm_models import PersonRecord
from extraction.documents import SqlAlchemyExtractionDocumentRepository
from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.normalizers import RuleBasedMentionNormalizer
from extraction.persistence import SqlAlchemyExtractionPersistence
from extraction.pipeline import ExtractionPipeline
from extraction.resolution_service import ExtractionResolutionService
from persecution.classification_service import PersecutionClassificationService
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolver import RuleBasedPersonResolver
from rosfinmonitoring.ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from rosfinmonitoring.matcher_persistence import RosfinMatchPersistence
from rosfinmonitoring.persistence import RosfinmonitoringPersistence
from sources.models import ParsedArticle, RawDocument
from sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence


def test_end_to_end_pipeline(session_factory: sessionmaker[Session]) -> None:
    """Test the complete pipeline from articles to candidates."""
    # Step 1: Ingest articles
    ingestion_persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="Test Source",
        source_base_url="https://test.example.com",
    )

    articles = [
        (
            "e2e-1",
            "Политическое задержание",
            # Same word order ("Иванова Ивана") as e2e-2 deliberately — the
            # exact matching_key baseline resolves person mentions by
            # concatenating normalized words in the order they appear, so a
            # different case/order (e.g. "Ивана Иванова") would resolve to a
            # second, distinct canonical person instead of merging with it.
            "В Москве полиция задержала Иванова Ивана на антивоенном митинге. Правозащитники сообщают о политическом преследовании.",
        ),
        (
            "e2e-2",
            "Арест активиста",
            "Басманный суд арестовал Иванова Ивана по статье 280 УК РФ. Адвокаты считают дело политически мотивированным.",
        ),
        (
            "e2e-3",
            "Обычное задержание",
            "Полиция задержала Петра Петрова за мелкое хулиганство. Составлен протокол по КоАП.",
        ),
        (
            "e2e-4",
            "Преследование правозащитника",
            (
                "Сергей Сидоров, известный правозащитник, задержан на антивоенном митинге. "
                "Активисты считают дело политически мотивированным."
            ),
        ),
    ]

    article_ids = []
    for ext_id, title, text in articles:
        raw_doc = RawDocument(
            external_id=ext_id,
            url=f"https://test.example.com/{ext_id}",
            fetched_at=datetime.now(UTC),
            content_type="text/html",
            content=text.encode("utf-8"),
        )
        parsed_article = ParsedArticle(
            external_id=ext_id,
            url=f"https://test.example.com/{ext_id}",
            title=title,
            published_at=datetime.now(UTC),
            text=text,
        )
        result = ingestion_persistence.save(raw_doc, parsed_article)
        article_ids.append(result.article_id)

    # Step 2: Extract entities and events
    extraction_persistence = SqlAlchemyExtractionPersistence(session_factory)
    doc_repo = SqlAlchemyExtractionDocumentRepository(session_factory)
    pipeline = ExtractionPipeline(
        extractors=[RuleBasedEntityExtractor()],
        normalizers=[RuleBasedMentionNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=extraction_persistence,
    )

    for article_id in article_ids:
        doc = doc_repo.get_by_article_id(article_id)
        pipeline.run(doc)

    # Step 3: Resolve persons
    person_persistence = SqlAlchemyPersonPersistence(session_factory)
    person_resolver = RuleBasedPersonResolver(person_persistence)
    resolution_service = ExtractionResolutionService(
        persistence=person_persistence,
        resolver=person_resolver,
        session_factory=session_factory,
    )

    for article_id in article_ids:
        run_id = extraction_persistence.get_latest_run_by_article_id(article_id)
        if run_id:
            resolution_service.resolve_extraction_run(run_id)

    # Identify the one person the dataset expects to survive every filter:
    # political persecution AND confirmed absent from Rosfinmonitoring.
    # Ivanov is political but IS in the RF snapshot below (-> MATCHED, not a
    # candidate); Petrov is absent from RF but NOT political (-> filtered out
    # by persecution status); only Sidorov is both.
    with session_factory() as session:
        sidorov = session.scalar(
            select(PersonRecord).where(PersonRecord.canonical_name.ilike("%Сидоров%"))
        )
    assert sidorov is not None, "Сергей Сидоров must have been resolved to a canonical person"
    sidorov_person_id = sidorov.id

    # Step 4: Classify persecution
    classification_service = PersecutionClassificationService(session_factory)
    with session_factory() as session:
        persons = session.scalars(select(PersonRecord)).all()
        for person in persons:
            classification_service.classify_person(person.id)

    # Step 5: Ingest Rosfinmonitoring data
    # "Иванов Иван" (2 words, no patronymic) matches what the extraction/
    # normalization pipeline actually produces for the mentions above —
    # see the word-order comment on e2e-1.
    rosfin_csv = "full_name,birth_date,inclusion_reason\nИванов Иван,01.01.1980,Тестовое включение\n".encode()
    rosfin_persistence = RosfinmonitoringPersistence(session_factory)
    rosfin_ingestion = RosfinmonitoringIngestionPipeline(
        persistence=rosfin_persistence,
    )
    snapshot_id = rosfin_ingestion.ingest(
        raw_content=rosfin_csv,
        source_url="https://rosfinmonitoring.gov.ru/test",
        snapshot_date=datetime.now(UTC),
    ).snapshot_id

    # Step 6: Match persons to Rosfinmonitoring
    matcher = RuleBasedRosfinmonitoringMatcher(session_factory)
    match_persistence = RosfinMatchPersistence(session_factory)
    with session_factory() as session:
        persons = session.scalars(select(PersonRecord)).all()
        for person in persons:
            match_result = matcher.match_person(person.id, snapshot_id)
            match_persistence.save_match_result(match_result)

    # Step 7: Query candidates
    candidate_service = CandidateQueryService(session_factory)
    candidates_result = candidate_service.get_candidates(snapshot_id=snapshot_id)

    # Business-significant check: the dataset has three deliberately distinct
    # cases (POLITICAL+MATCHED, NON_POLITICAL+NOT_MATCHED, POLITICAL+
    # NOT_MATCHED) and only the last one is a real candidate. A loop over
    # `candidates_result.candidates` with no length/identity check would
    # pass just as well on an empty list, so assert the exact set instead.
    assert candidates_result.snapshot_id == snapshot_id
    assert candidates_result.total_count == 1
    assert {candidate.person_id for candidate in candidates_result.candidates} == {
        sidorov_person_id
    }

    candidate = candidates_result.candidates[0]
    assert candidate.canonical_name
    assert candidate.rosfinmonitoring_status == "not_matched"
    assert candidate.persecution_status == "political"
