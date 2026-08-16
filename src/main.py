import argparse
import asyncio

from article_parser import OvdInfoArticleParser
from chunker import Chunker
from ingestion_pipeline import IngestionPipeline
from models import SourceReference
from website_adapter import WebsiteAdapter


def main() -> None:
    argument_parser = argparse.ArgumentParser(
        description="Load and parse an OVD-Info express news article"
    )
    argument_parser.add_argument("url")
    args = argument_parser.parse_args()

    reference = SourceReference(
        external_id=args.url,
        url=args.url,
    )

    ingestion_pipeline = IngestionPipeline(
        website_adapter=WebsiteAdapter(),
        parser=OvdInfoArticleParser(),
        chunker=Chunker(),
    )

    result = asyncio.run(ingestion_pipeline.run(reference))

    print("title:", result.article.title)
    print("published_at:", result.article.published_at)

    for chunk in result.chunks:
        print(f"chunk {chunk.ordinal}:", chunk.text)


if __name__ == "__main__":
    main()
