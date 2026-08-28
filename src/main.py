import argparse
import asyncio
import os

from article_parser import OvdInfoArticleParser
from chunker import Chunker
from database import create_database_engine, create_session_factory
from ingestion_pipeline import IngestionPipeline
from models import SearchQuery, SourceReference
from postgres_lexical_search import PostgresLexicalSearch
from sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from website_adapter import WebsiteAdapter


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

    args = argument_parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")

    if database_url is None:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    database_engine = create_database_engine(database_url)
    session_factory = create_session_factory(database_engine)

    if args.command == "search":
        search = PostgresLexicalSearch(session_factory)
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
