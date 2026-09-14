from collections.abc import Callable
from dataclasses import dataclass

import httpx

from sources.article_parser import ArticleParser, OvdInfoArticleParser
from sources.ovd_info.listing_parser import OvdInfoListingParser
from sources.ovd_info.source_adapter import OvdInfoSourceAdapter
from sources.sota_vision.article_parser import SotaVisionArticleParser
from sources.sota_vision.listing_parser import SotaVisionListingParser
from sources.sota_vision.source_adapter import SotaVisionSourceAdapter
from sources.source_adapter import DocumentFetcher, SourceAdapter


@dataclass(frozen=True)
class SourceDefinition:
    name: str
    source_name: str
    base_url: str
    create_adapter: Callable[[httpx.AsyncClient, DocumentFetcher], SourceAdapter]
    create_parser: Callable[[], ArticleParser]
    # Declared capabilities used by research source routing. Every adapter
    # here implements SourceAdapter: listing discovery and direct fetch.
    supports_discovery: bool = True
    supports_direct_fetch: bool = True


OVD_INFO = SourceDefinition(
    name="ovd-info",
    source_name="ОВД-Инфо",
    base_url="https://ovd.info",
    create_adapter=lambda client, fetcher: OvdInfoSourceAdapter(
        client=client,
        listing_parser=OvdInfoListingParser(),
        document_fetcher=fetcher,
        max_attempts=3,
        base_delay_seconds=0.5,
    ),
    create_parser=OvdInfoArticleParser,
)

SOTA_VISION = SourceDefinition(
    name="sota-vision",
    source_name="SOTA",
    base_url="https://sota.vision",
    create_adapter=lambda client, fetcher: SotaVisionSourceAdapter(
        client=client,
        listing_parser=SotaVisionListingParser(),
        document_fetcher=fetcher,
        max_attempts=3,
        base_delay_seconds=0.5,
    ),
    create_parser=SotaVisionArticleParser,
)

SOURCES: dict[str, SourceDefinition] = {
    OVD_INFO.name: OVD_INFO,
    SOTA_VISION.name: SOTA_VISION,
}


def get_source_definition(name: str) -> SourceDefinition:
    try:
        return SOURCES[name]
    except KeyError:
        raise ValueError(f"Unknown source: {name}") from None
