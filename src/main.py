import asyncio

from article_parser import OvdInfoArticleParser
from models import SourceReference
from website_adapter import WebsiteAdapter
from chunker import Chunker
from ingestion_pipeline import IngestionPipeline

URL = (
    "https://ovd.info/express-news/2026/08/13/"
    "na-komika-sashu-dolgopolova-zaveli-delo-"
    "o-reabilitacii-nacizma"
)


def main() -> None:
    reference = SourceReference(
        external_id=URL,
        url=URL,
    )

    ingestion_pipeline = IngestionPipeline(
        website_adapter=WebsiteAdapter(),
        parser=OvdInfoArticleParser(),
        chunker=Chunker(),
    )

    result = asyncio.run(ingestion_pipeline.run(reference))

    print("title:", result.article.title)

    for chunk in result.chunks:
        print(f"chunk {chunk.ordinal}:", chunk.text)

    # print("external_id:", article.external_id)
    # print("url:", article.url)
    # print("article.title:", article.title)
    # print("article.published_at:", article.published_at)
    # print("article.text:", article.text)


if __name__ == "__main__":
    main()
