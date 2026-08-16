import asyncio

from article_parser import OvdInfoArticleParser
from models import SourceReference
from website_adapter import WebsiteAdapter
from chunker import Chunker

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

    website_adapter = WebsiteAdapter()
    parser = OvdInfoArticleParser()
    chunker = Chunker()

    raw_document = asyncio.run(website_adapter.fetch(reference))
    article = parser.parse(raw_document)
    chunks = chunker.split(article)

    for chunk in chunks:
        print("chunk.ordinal:", chunk.ordinal)
        print("chunk.text:", chunk.text)

    # print("external_id:", article.external_id)
    # print("url:", article.url)
    # print("article.title:", article.title)
    # print("article.published_at:", article.published_at)
    # print("article.text:", article.text)


if __name__ == "__main__":
    main()
