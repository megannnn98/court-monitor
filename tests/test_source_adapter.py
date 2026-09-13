from datetime import UTC, datetime

from models import RawDocument, SourceReference
from source_adapter import SourceAdapter


class FakeSourceAdapter:
    async def discover(
        self,
        *,
        limit: int,
    ) -> list[SourceReference]:
        references = [
            SourceReference(
                external_id="article-1",
                url="https://example.com/article-1",
            ),
            SourceReference(
                external_id="article-2",
                url="https://example.com/article-2",
            ),
        ]

        return references[:limit]

    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        return RawDocument(
            external_id=reference.external_id,
            url=reference.url,
            fetched_at=datetime.now(tz=UTC),
            content_type="text/html",
            content=b"<html></html>",
        )


def accepts_source_adapter(adapter: SourceAdapter) -> None:
    pass


def test_fake_source_adapter_satisfies_protocol() -> None:
    adapter = FakeSourceAdapter()

    accepts_source_adapter(adapter)
