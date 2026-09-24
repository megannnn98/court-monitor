from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import httpx

from extraction.models import MEMOPZK_REGISTRY_SOURCE_NAME
from sources.article_parser import ArticleParser, OvdInfoArticleParser
from sources.kommersant.article_parser import (
    KOMMERSANT_FEED_URL,
    KommersantArticleParser,
    is_case_news,
    kommersant_external_id,
)
from sources.memopzk.article_parser import FigurantParser
from sources.memopzk.source_adapter import MEMOPZK_BASE_URL, MemopzkFigurantAdapter
from sources.ovd_info.listing_parser import OvdInfoListingParser
from sources.ovd_info.source_adapter import OvdInfoSourceAdapter
from sources.rss.source_adapter import RssSourceAdapter
from sources.sota_vision.article_parser import SotaVisionArticleParser
from sources.sota_vision.listing_parser import SotaVisionListingParser
from sources.sota_vision.source_adapter import SotaVisionSourceAdapter
from sources.source_adapter import DocumentFetcher, SourceAdapter
from sources.sudrf.article_parser import SudrfArticleParser
from sources.sudrf.listing_parser import SudrfListingParser
from sources.sudrf.source_adapter import SudrfSourceAdapter
from sources.telegram.article_parser import TelegramPostParser
from sources.telegram.channels import TelegramChannel, load_telegram_channels
from sources.telegram.source_adapter import (
    TELEGRAM_HISTORY_DAYS,
    TelegramSourceAdapter,
    telegram_history_days,
)


class SourceKind(StrEnum):
    """What a source publishes, for consumers that must not mix the two.

    A news source publishes dated articles about events; a registry publishes a card
    per person, which carries no news date and must stay out of "people in the news".
    """

    NEWS = "news"
    REGISTRY = "registry"


@dataclass(frozen=True)
class SourceDefinition:
    name: str
    source_name: str
    base_url: str
    create_adapter: Callable[[httpx.AsyncClient, DocumentFetcher], SourceAdapter]
    create_parser: Callable[[], ArticleParser]
    kind: SourceKind = SourceKind.NEWS
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


def sudrf_source(name: str, title: str, host: str) -> SourceDefinition:
    """A court press service on the shared `sudrf.ru` engine, addressed by its host."""
    return SourceDefinition(
        name=name,
        source_name=title,
        base_url=f"https://{host}",
        create_adapter=lambda client, fetcher: SudrfSourceAdapter(
            client=client,
            listing_parser=SudrfListingParser(host),
            document_fetcher=fetcher,
            host=host,
            max_attempts=3,
            base_delay_seconds=0.5,
        ),
        create_parser=SudrfArticleParser,
    )


SUDRF_2ZOVS = sudrf_source(
    name="sudrf-2zovs",
    title="2-й Западный окружной военный суд",
    host="2zovs.msk.sudrf.ru",
)


# The registry of persecuted people: a structured card per person (ADR 0017).
MEMOPZK_FIGURANTS = SourceDefinition(
    name="memopzk-figurants",
    source_name=MEMOPZK_REGISTRY_SOURCE_NAME,
    base_url=MEMOPZK_BASE_URL,
    create_adapter=lambda client, fetcher: MemopzkFigurantAdapter(
        client=client,
        document_fetcher=fetcher,
    ),
    create_parser=FigurantParser,
    kind=SourceKind.REGISTRY,
    # A card is addressed by its registry id, not by a page URL.
    supports_direct_fetch=False,
)

# Kommersant's site names defendants its Telegram channel leaves unnamed (ADR 0017).
KOMMERSANT = SourceDefinition(
    name="kommersant",
    source_name="Коммерсантъ (сайт)",
    base_url="https://www.kommersant.ru",
    create_adapter=lambda client, fetcher: RssSourceAdapter(
        client=client,
        feed_url=KOMMERSANT_FEED_URL,
        document_fetcher=fetcher,
        include=is_case_news,
        external_id=kommersant_external_id,
    ),
    create_parser=KommersantArticleParser,
)


def telegram_source(
    channel: TelegramChannel, *, history_days: int = TELEGRAM_HISTORY_DAYS
) -> SourceDefinition:
    return SourceDefinition(
        name=channel.source_name,
        source_name=channel.title,
        base_url=channel.base_url,
        create_adapter=lambda client, fetcher: TelegramSourceAdapter(
            client=client,
            username=channel.username,
            document_fetcher=fetcher,
            max_attempts=3,
            base_delay_seconds=0.5,
            history_days=history_days,
        ),
        create_parser=TelegramPostParser,
    )


TELEGRAM_SOURCES = [
    telegram_source(channel, history_days=telegram_history_days())
    for channel in load_telegram_channels()
]

SOURCES: dict[str, SourceDefinition] = {
    OVD_INFO.name: OVD_INFO,
    SOTA_VISION.name: SOTA_VISION,
    SUDRF_2ZOVS.name: SUDRF_2ZOVS,
    MEMOPZK_FIGURANTS.name: MEMOPZK_FIGURANTS,
    KOMMERSANT.name: KOMMERSANT,
    **{definition.name: definition for definition in TELEGRAM_SOURCES},
}


def get_source_definition(name: str) -> SourceDefinition:
    try:
        return SOURCES[name]
    except KeyError:
        raise ValueError(f"Unknown source: {name}") from None


def news_sources() -> list[SourceDefinition]:
    """Registered news sources, in stable display order."""
    return sorted(
        (definition for definition in SOURCES.values() if definition.kind is SourceKind.NEWS),
        key=lambda definition: (definition.source_name.casefold(), definition.name),
    )


def news_source_base_urls() -> list[str]:
    """Base URLs of the news sources, as stored in `sources.base_url`."""
    return sorted(
        definition.base_url for definition in SOURCES.values() if definition.kind is SourceKind.NEWS
    )
