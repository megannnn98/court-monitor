"""End-to-end golden test for the court-monitor pipeline."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from candidate_query_service import CandidateQueryService
from extraction_documents import SqlAlchemyExtractionDocumentRepository
from extraction_events import RuleBasedEventExtractor
from extraction_extractors import RuleBasedEntityExtractor
from extraction_normalizers import RuleBasedMentionNormalizer
from extraction_persistence import SqlAlchemyExtractionPersistence
from extraction_pipeline import ExtractionPipeline
from extraction_resolution_service import ExtractionResolutionService
from models import ParsedArticle, RawDocument
from orm_models import PersonRecord
from persecution_classification_service import PersecutionClassificationService
from person_persistence import SqlAlchemyPersonPersistence
from person_resolver import RuleBasedPersonResolver
from rosfinmonitoring_ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring_matcher import RuleBasedRosfinmonitoringMatcher
from rosfinmonitoring_matcher_persistence import RosfinMatchPersistence
from rosfinmonitoring_persistence import RosfinmonitoringPersistence
from sqlalchemy_persistence import SqlAlchemyIngestionPersistence


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
            "В Москве полиция задержала Ивана Иванова на антивоенном митинге. Правозащитники сообщают о политическом преследовании.",
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

    # Step 4: Classify persecution
    classification_service = PersecutionClassificationService(session_factory)
    with session_factory() as session:
        persons = session.scalars(select(PersonRecord)).all()
        for person in persons:
            classification_service.classify_person(person.id)

    # Step 5: Ingest Rosfinmonitoring data
    rosfin_csv = "full_name,birth_date,inclusion_reason\nИванов Иван Иванович,01.01.1980,Тестовое включение\n".encode()
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

    # Verify the pipeline ran successfully
    assert candidates_result.snapshot_id == snapshot_id
    assert hasattr(candidates_result, "candidates")
    assert hasattr(candidates_result, "query_timestamp")

    # Each candidate should have required fields
    for candidate in candidates_result.candidates:
        assert candidate.person_id is not None
        assert candidate.canonical_name
        # get_candidates now includes only confirmed NOT_MATCHED by default —
        # NO_MATCH_RECORD/AMBIGUOUS/NEEDS_REVIEW/INSUFFICIENT_DATA are not
        # confirmed absences and must not appear here.
        assert candidate.rosfinmonitoring_status == "not_matched"
        assert candidate.persecution_status == "political"
