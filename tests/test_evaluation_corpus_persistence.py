from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from evaluation_loader import (
    load_evaluation_cases,
    load_evaluation_documents,
)
from evaluation_models import ArticleReference
from models import ParsedArticle, RawDocument
from orm_models import ParsedArticleRecord, SourceDocument
from postgres_lexical_search import PostgresLexicalSearch
from search_evaluator import SearchEvaluator
from sqlalchemy_persistence import SqlAlchemyIngestionPersistence

CORPUS_PATH = Path(__file__).parent / "fixtures" / "evaluation_corpus.json"
FIXED_FETCHED_AT = datetime(2026, 1, 1, tzinfo=UTC)
CASES_PATH = Path(__file__).parent / "fixtures" / "evaluation_cases.json"


def test_evaluation_corpus_can_be_persisted(
    session_factory: sessionmaker[Session],
) -> None:
    documents = load_evaluation_documents(CORPUS_PATH)

    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо evaluation",
        source_base_url="https://ovd.info",
    )

    for document in documents:
        raw_document = RawDocument(
            external_id=document.external_id,
            url=document.canonical_url,
            fetched_at=FIXED_FETCHED_AT,
            content_type="text/plain",
            content=document.title.encode("utf-8"),
        )

        article = ParsedArticle(
            external_id=document.external_id,
            url=document.canonical_url,
            title=document.title,
            published_at=None,
            text=document.text,
        )

        persistence.save(
            raw_document=raw_document,
            article=article,
        )

    with session_factory() as session:
        document_count = session.scalar(select(func.count()).select_from(SourceDocument))
        article_count = session.scalar(select(func.count()).select_from(ParsedArticleRecord))

    assert document_count == 6
    assert article_count == 6

    cases = load_evaluation_cases(CASES_PATH)
    search = PostgresLexicalSearch(session_factory)
    evaluator = SearchEvaluator(search=search, limit=10)

    report = evaluator.evaluate(cases)

    assert [result.query_id for result in report.results] == [
        "rehabilitation-of-nazism",
        "military-fakes",
        "picket-detention",
        "extremist-activity",
    ]

    rehabilitation_result = next(
        result for result in report.results if result.query_id == "rehabilitation-of-nazism"
    )

    assert rehabilitation_result.retrieved_articles[0] == ArticleReference(
        source_base_url="https://ovd.info",
        external_id="rehabilitation-nazism",
    )
    assert rehabilitation_result.reciprocal_rank == 1.0

    existing_articles = {(document.source_base_url, document.external_id) for document in documents}

    retrieved_articles = {
        (reference.source_base_url, reference.external_id)
        for result in report.results
        for reference in result.retrieved_articles
    }

    assert retrieved_articles <= existing_articles
