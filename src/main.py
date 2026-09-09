import argparse
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from qdrant_client import QdrantClient

from article_parser import OvdInfoArticleParser
from chunker import Chunker
from database import create_database_engine, create_session_factory
from evaluation_loader import load_evaluation_cases, load_evaluation_documents
from ingestion_pipeline import IngestionPipeline
from models import ArticleChunk, ParsedArticle, RawDocument, SearchQuery, SourceReference
from postgres_lexical_search import PostgresLexicalSearch
from qdrant_chunk_indexer import QdrantChunkIndexer
from qdrant_dense_search import QdrantDenseSearch
from search_backend import SearchBackend
from search_evaluator import SearchEvaluator
from sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from text_embedder import TextEmbedder
from website_adapter import WebsiteAdapter

DEFAULT_EVALUATION_CORPUS_PATH = Path("tests/fixtures/evaluation_corpus.json")
DEFAULT_EVALUATION_CASES_PATH = Path("tests/fixtures/evaluation_cases.json")
FIXED_EVALUATION_FETCHED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def main() -> None:
    argument_parser = argparse.ArgumentParser(description="Ingest and search OVD-Info articles")
    subparsers = argument_parser.add_subparsers(
        dest="command",
        required=True,
    )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Load and save an OVD-Info article",
    )
    ingest_parser.add_argument("url")

    search_parser = subparsers.add_parser(
        "search",
        help="Search saved article chunks",
    )
    search_parser.add_argument("text")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )

    evaluate_search_parser = subparsers.add_parser(
        "evaluate-search",
        help="Evaluate PostgreSQL lexical search against fixed cases",
    )
    evaluate_search_parser.add_argument(
        "--corpus-path",
        type=Path,
        default=DEFAULT_EVALUATION_CORPUS_PATH,
    )
    evaluate_search_parser.add_argument(
        "--cases-path",
        type=Path,
        default=DEFAULT_EVALUATION_CASES_PATH,
    )
    evaluate_search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )
    evaluate_search_parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
    )
    evaluate_search_parser.add_argument(
        "--backend",
        choices=["lexical", "dense"],
        default="lexical",
    )
    search_parser.add_argument(
        "--backend",
        choices=["lexical", "dense"],
        default="lexical",
    )

    args = argument_parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")

    if database_url is None:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    database_engine = create_database_engine(database_url)
    session_factory = create_session_factory(database_engine)
    search: SearchBackend

    if args.command == "search":
        if args.backend == "lexical":
            search = PostgresLexicalSearch(session_factory)
        else:
            client = QdrantClient(url=os.environ["QDRANT_URL"])
            embedder = TextEmbedder(os.environ["EMBEDDING_MODEL_ID"])

            search = QdrantDenseSearch(
                client=client,
                collection_name=os.environ["QDRANT_COLLECTION"],
                embedder=embedder,
            )
        hits = search.search(
            SearchQuery(
                text=args.text,
                limit=args.limit,
            )
        )

        for hit in hits:
            print(f"[{hit.score:.4f}] {hit.title}")
            print(hit.url)
            print(hit.text)
            print()

        return

    if args.command == "evaluate-search":
        documents = load_evaluation_documents(args.corpus_path)

        persistence = SqlAlchemyIngestionPersistence(
            session_factory=session_factory,
            source_name="ОВД-Инфо evaluation",
            source_base_url="https://ovd.info",
        )

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
                fetched_at=FIXED_EVALUATION_FETCHED_AT,
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

            persistence.save(
                raw_document=raw_document,
                article=article,
                chunks=chunks,
            )

        cases = load_evaluation_cases(args.cases_path)

        if args.backend == "lexical":
            search = PostgresLexicalSearch(session_factory)
        else:
            client = QdrantClient(url=os.environ["QDRANT_URL"])
            embedder = TextEmbedder(os.environ["EMBEDDING_MODEL_ID"])

            indexer = QdrantChunkIndexer(
                client=client,
                collection_name=os.environ["QDRANT_COLLECTION"],
                vector_size=768,
                session_factory=session_factory,
                embedder=embedder,
            )
            indexer.recreate_collection()
            indexer.index_all()

            search = QdrantDenseSearch(
                client=client,
                collection_name=os.environ["QDRANT_COLLECTION"],
                embedder=embedder,
            )

        evaluator = SearchEvaluator(search=search, limit=args.limit)

        report = evaluator.evaluate(cases)

        report_json = report.model_dump_json(indent=2)

        if args.output_path is not None:
            args.output_path.parent.mkdir(parents=True, exist_ok=True)
            args.output_path.write_text(report_json + "\n", encoding="utf-8")
        else:
            print(report_json)

        return

    reference = SourceReference(
        external_id=args.url,
        url=args.url,
    )

    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )

    ingestion_pipeline = IngestionPipeline(
        website_adapter=WebsiteAdapter(),
        parser=OvdInfoArticleParser(),
        chunker=Chunker(),
        persistence=persistence,
    )

    result = asyncio.run(ingestion_pipeline.run(reference))

    print("title:", result.article.title)
    print("published_at:", result.article.published_at)

    for chunk in result.chunks:
        print(f"chunk {chunk.ordinal}:", chunk.text)

    print("document_id:", result.persistence.document_id)
    print("chunks_saved:", result.persistence.chunks_saved)


if __name__ == "__main__":
    main()
