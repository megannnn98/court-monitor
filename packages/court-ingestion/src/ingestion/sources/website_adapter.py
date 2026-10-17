from datetime import UTC, datetime

import httpx

from ingestion.concurrency import parse_retry_after
from monitor_core.errors import PermanentFetchError, TransientFetchError
from monitor_core.model import RawDocument, SourceReference


class WebsiteAdapter:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    def bind_http_client(self, client: httpx.AsyncClient) -> None:
        """Use the source run's pooled, limited client for article requests."""
        self._client = client

    async def fetch(self, ref: SourceReference) -> RawDocument:
        try:
            if self._client is None:
                # Direct CLI use retains its historic self-contained behaviour.
                async with httpx.AsyncClient(
                    timeout=5.0,
                    headers={"User-Agent": "my-app/1.0"},
                ) as client:
                    response = await client.get(ref.url)
                    response.raise_for_status()
            else:
                response = await self._client.get(ref.url)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code

            if status_code == 429 or status_code >= 500:
                raise TransientFetchError(
                    f"Temporary failure fetching {ref.url}: HTTP {status_code}",
                    retry_after_seconds=parse_retry_after(exc.response.headers.get("retry-after")),
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


def bind_http_client(fetcher: object, client: httpx.AsyncClient) -> None:
    """Bind a pooled run client when this fetcher supports it.

    Test and specialised source fetchers deliberately need no HTTP client, so this is
    structural rather than a requirement of the public DocumentFetcher port.
    """
    binder = getattr(fetcher, "bind_http_client", None)
    if callable(binder):
        binder(client)
