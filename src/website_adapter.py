from datetime import UTC, datetime

import httpx

from models import RawDocument, SourceReference


class WebsiteAdapter:
    async def fetch(self, ref: SourceReference) -> RawDocument:
        async with httpx.AsyncClient(
            timeout=5.0,
            headers={"User-Agent": "my-app/1.0"},
        ) as client:
            response = await client.get(ref.url)
            response.raise_for_status()

        return RawDocument(
            external_id=ref.external_id,
            url=str(response.url),
            fetched_at=datetime.now(tz=UTC),
            content_type=response.headers.get("content-type", ""),
            content=response.content,
        )
