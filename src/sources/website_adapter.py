from datetime import UTC, datetime

import httpx

from sources.ingestion_errors import PermanentFetchError, TransientFetchError
from sources.models import RawDocument, SourceReference


class WebsiteAdapter:
    async def fetch(self, ref: SourceReference) -> RawDocument:
        try:
            async with httpx.AsyncClient(
                timeout=5.0,
                headers={"User-Agent": "my-app/1.0"},
            ) as client:
                response = await client.get(ref.url)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code

            if status_code == 429 or status_code >= 500:
                raise TransientFetchError(
                    f"Temporary failure fetching {ref.url}: HTTP {status_code}"
                ) from exc

            raise PermanentFetchError(f"Failed to fetch {ref.url}: HTTP {status_code}") from exc

        except httpx.TransportError as exc:
            raise TransientFetchError(f"Temporary failure fetching {ref.url}") from exc

        return RawDocument(
            external_id=ref.external_id,
            url=str(response.url),
            fetched_at=datetime.now(tz=UTC),
            content_type=response.headers.get("content-type", ""),
            content=response.content,
        )
