from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from evaluation_loader import (
    load_evaluation_cases,
    load_evaluation_documents,
)
from evaluation_models import ChunkReference
from models import ArticleChunk, ParsedArticle, RawDocument, SearchQuery
from orm_models import ArticleChunkRecord, ParsedArticleRecord, SourceDocument
from postgres_lexical_search import PostgresLexicalSearch
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

    chunks_saved = 0

    for document in documents:
        chunks = [
            ArticleChunk(
                ordinal=ordinal,
                text=text,
            )
            for ordinal, text in enumerate(document.chunks)
        ]

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
            text="\n\n".join(document.chunks),
        )

        result = persistence.save(
            raw_document=raw_document,
            article=article,
            chunks=chunks,
        )

        chunks_saved += result.chunks_saved

    assert chunks_saved == 12

    with session_factory() as session:
        document_count = session.scalar(select(func.count()).select_from(SourceDocument))
        article_count = session.scalar(select(func.count()).select_from(ParsedArticleRecord))
        chunk_count = session.scalar(select(func.count()).select_from(ArticleChunkRecord))

    assert document_count == 6
    assert article_count == 6
    assert chunk_count == 12

    cases = load_evaluation_cases(CASES_PATH)
    search = PostgresLexicalSearch(session_factory)

    retrieved_by_query: dict[str, list[ChunkReference]] = {}

    for case in cases:
        hits = search.search(
            SearchQuery(
                text=case.query_text,
                limit=10,
            )
        )

        retrieved_by_query[case.query_id] = [
            ChunkReference(
                source_base_url=hit.source_base_url,
                external_id=hit.external_id,
                ordinal=hit.ordinal,
            )
            for hit in hits
        ]

    assert set(retrieved_by_query) == {
        "rehabilitation-of-nazism",
        "military-fakes",
        "picket-detention",
        "extremist-activity",
    }

    assert retrieved_by_query["rehabilitation-of-nazism"] == [
        ChunkReference(
            source_base_url="https://ovd.info",
            external_id="rehabilitation-nazism",
            ordinal=1,
        )
    ]

    existing_chunks = {
        ChunkReference(
            source_base_url=document.source_base_url,
            external_id=document.external_id,
            ordinal=ordinal,
        )
        for document in documents
        for ordinal, _ in enumerate(document.chunks)
    }

    retrieved_chunks = {
        reference for references in retrieved_by_query.values() for reference in references
    }

    assert retrieved_chunks <= existing_chunks
