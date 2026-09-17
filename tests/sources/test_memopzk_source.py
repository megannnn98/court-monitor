"""The figurant registry of «Поддержка политзаключённых. Мемориал» as a source (real cards)."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.models import (
    MEMOPZK_REGISTRY_SOURCE_NAME,
    EntityType,
    EventEntityRole,
    ExtractionDocument,
)
from extraction.normalizers import RuleBasedMentionNormalizer
from sources.ingestion_errors import ParseError, PermanentDiscoveryError
from sources.memopzk import taxonomy
from sources.memopzk.article_parser import FigurantParser
from sources.memopzk.source_adapter import (
    FIGURANT_COLLECTION_URL,
    MemopzkFigurantAdapter,
)
from sources.models import RawDocument, SourceReference
from sources.source_registry import get_source_definition

CARDS = json.loads((Path(__file__).parents[1] / "fixtures/memopzk/figurants.json").read_text())
BY_NAME = {card["title"]["rendered"]: card for card in CARDS}


class FailingFetcher:
    async def fetch(self, reference: SourceReference) -> RawDocument:
        raise AssertionError("the adapter serves listed cards itself")


def _adapter(handle: object) -> tuple[MemopzkFigurantAdapter, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))  # type: ignore[arg-type]
    adapter = MemopzkFigurantAdapter(
        client=client,
        document_fetcher=FailingFetcher(),
        base_delay_seconds=0,
        crawl_delay_seconds=0,
        now=lambda: datetime(2026, 9, 17, tzinfo=UTC),
    )
    return adapter, client


def _pages(page_size: int, total_pages: int) -> tuple[object, list[dict[str, list[str]]]]:
    requests: list[dict[str, list[str]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        assert f"{url.scheme}://{url.netloc}{url.path}" == FIGURANT_COLLECTION_URL
        query = parse_qs(url.query)
        requests.append(query)
        page = int(query["page"][0])
        if page > total_pages:
            return httpx.Response(400, json={"code": "rest_post_invalid_page_number"})
        per_page = int(query["per_page"][0])
        cards = [
            {**CARDS[0], "id": page * 1000 + index, "link": f"https://memopzk.org/f/{page}-{index}"}
            for index in range(min(per_page, page_size))
        ]
        return httpx.Response(200, json=cards)

    return handle, requests


def _discover(handle: object, limit: int) -> list[SourceReference]:
    async def run() -> list[SourceReference]:
        adapter, client = _adapter(handle)
        async with client:
            return await adapter.discover(limit=limit)

    return asyncio.run(run())


def test_discovery_pages_through_the_most_recently_changed_cards() -> None:
    handle, requests = _pages(page_size=100, total_pages=3)

    references = _discover(handle, limit=250)

    assert len(references) == 250
    assert [query["page"] for query in requests] == [["1"], ["2"], ["3"]]
    assert requests[0]["orderby"] == ["modified"] and requests[0]["order"] == ["desc"]
    assert references[0] == SourceReference(external_id="1000", url="https://memopzk.org/f/1-0")


def test_discovery_ends_at_the_page_past_the_last_one() -> None:
    """WordPress answers 400 for a page past the end: that is the end, not a failure."""
    handle, requests = _pages(page_size=100, total_pages=1)

    assert len(_discover(handle, limit=500)) == 100
    assert len(requests) == 2


def test_a_failing_first_page_is_a_discovery_error() -> None:
    handle, _ = _pages(page_size=100, total_pages=0)

    with pytest.raises(PermanentDiscoveryError):
        _discover(handle, limit=10)


def test_fetch_serves_the_listed_card_without_a_request() -> None:
    """Monitoring discovers and ingests with two adapter instances."""
    handle, requests = _pages(page_size=1, total_pages=1)

    async def run() -> RawDocument:
        discovering, discovery_client = _adapter(handle)
        async with discovery_client:
            [reference] = await discovering.discover(limit=1)
        listed = len(requests)
        ingesting, ingestion_client = _adapter(handle)
        async with ingestion_client:
            document = await ingesting.fetch(reference)
        assert len(requests) == listed
        return document

    document = asyncio.run(run())
    assert json.loads(document.content)["title"]["rendered"] == "Ярош Сергей Васильевич"


def test_an_unlisted_card_is_fetched_alone_under_the_crawl_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []
    delays: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json=CARDS[1])

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("sources.memopzk.source_adapter.asyncio.sleep", fake_sleep)

    async def run() -> RawDocument:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        adapter = MemopzkFigurantAdapter(client=client, document_fetcher=FailingFetcher())
        async with client:
            return await adapter.fetch(
                SourceReference(external_id="999001", url="https://memopzk.org/f/unlisted")
            )

    document = asyncio.run(run())

    assert delays == [10.0]
    assert requested == [
        f"{FIGURANT_COLLECTION_URL}/999001?_fields=" + requested[0].split("=", 1)[1]
    ]
    assert json.loads(document.content)["id"] == CARDS[1]["id"]


def _raw(card: dict[str, object]) -> RawDocument:
    return RawDocument(
        external_id=str(card["id"]),
        url=str(card["link"]),
        fetched_at=datetime(2026, 9, 17, tzinfo=UTC),
        content_type="application/json",
        content=json.dumps(card, ensure_ascii=False).encode(),
    )


def test_a_card_becomes_a_short_article_from_its_fields() -> None:
    article = FigurantParser().parse(_raw(BY_NAME["Ярош Сергей Васильевич"]))

    assert article.title == "Ярош Сергей Васильевич"
    assert article.published_at == datetime(2026, 9, 15, 22, 3, 25, tzinfo=UTC)
    assert article.text == (
        "Ярош Сергей Васильевич.\n"
        "Регион: Республика Саха (Якутия).\n"
        "Ярош Сергей Васильевич обвиняется по статьям: ч. 1 ст. 205.1 УК РФ, "
        "ч. 2 ст. 205.2 УК РФ.\n"
        "Мера: заключение под стражу.\n"
        "Категория дела: терроризм.\n"
        "Стадия преследования: следствие.\n"
        "Проект «Поддержка политзаключённых. Мемориал» внёс человека в реестр преследуемых: "
        "«Другие жертвы политических репрессий»."
    )


def test_a_card_after_the_verdict_says_sentenced_in_the_persons_gender() -> None:
    korotkov = FigurantParser().parse(_raw(BY_NAME["Коротков Дмитрий Константинович"]))

    assert "Коротков Дмитрий Константинович осужден по статьям: ч. 1.1 ст. 205.1" in (korotkov.text)


def test_a_former_name_in_brackets_is_left_out() -> None:
    article = FigurantParser().parse(_raw(BY_NAME["Мандрыгина (Шелковникова) Евгения Михайловна"]))

    assert article.title == "Мандрыгина Евгения Михайловна"


@pytest.mark.parametrize("title", ["Л. М. Е.", "Коваленко", ""])
def test_a_card_without_a_name_is_rejected(title: str) -> None:
    with pytest.raises(ParseError):
        FigurantParser().parse(_raw({**CARDS[0], "title": {"rendered": title}}))


def test_a_surname_with_initials_is_a_name() -> None:
    article = FigurantParser().parse(_raw({**CARDS[0], "title": {"rendered": "Сизов А. В."}}))

    assert article.title == "Сизов А. В."


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("uk-205-1-ch-1-1", ["ч. 1.1 ст. 205.1 УК РФ"]),
        ("uk-275-cherez-ch-3-st-30", ["ст. 275 УК РФ", "ч. 3 ст. 30 УК РФ"]),
        ("uk-207-3-ch-2-p-d", ["п. «д» ч. 2 ст. 207.3 УК РФ"]),
        ("uk-205-5-chast-neizvestna", ["ст. 205.5 УК РФ"]),
        ("uk-356-pt-1", ["ч. 1 ст. 356 УК РФ"]),
        ("koap-20-3-3-ch-1", ["ч. 1 ст. 20.3.3 КоАП РФ"]),
        ("uk-neizvestno", []),
        ("regions-krym", []),
    ],
)
def test_article_slugs_are_read_back(slug: str, expected: list[str]) -> None:
    assert taxonomy.legal_references(slug) == expected


def test_region_slugs_are_the_transliterated_names() -> None:
    assert taxonomy.REGIONS["respublika-saha-yakutiya"] == "Республика Саха (Якутия)"
    assert taxonomy.REGIONS["kemerovskaya-oblast-kuzbass"] == "Кемеровская область — Кузбасс"
    assert taxonomy.REGIONS["habarovskij-kraj"] == "Хабаровский край"
    assert taxonomy.REGIONS["krym"] == "Республика Крым"


def _extracted(title: str) -> tuple[list[str], list[list[str]]]:
    article = FigurantParser().parse(_raw(BY_NAME[title]))
    document = ExtractionDocument(
        article_id=1,
        title=article.title,
        text=article.text,
        published_at=article.published_at,
        source_name=MEMOPZK_REGISTRY_SOURCE_NAME,
        source_url=article.url,
        content_hash="h",
    )
    normalizer = RuleBasedMentionNormalizer()
    mentions = [
        normalizer.normalize(mention, document)
        for mention in RuleBasedEntityExtractor().extract(document)
    ]
    people = sorted({m.normalized_text for m in mentions if m.entity_type is EntityType.PERSON})
    targets = [
        [mentions[link.mention_index].normalized_text for link in event.links]
        for event in RuleBasedEventExtractor().extract(document, mentions)
        if any(link.role is EventEntityRole.TARGET for link in event.links)
    ]
    return people, targets


@pytest.mark.parametrize(
    "title",
    [
        "Ярош Сергей Васильевич",
        # A surname the dictionary also knows as an ordinary word.
        "Салманов Рамиль Дилгамович",
        # Nominative surnames the normalizer would otherwise read as inflected.
        "Акузин Андрей Викторович",
        "Ипатова Елена Анатольевна",
    ],
)
def test_the_card_names_one_person_as_written_and_charges_them(title: str) -> None:
    people, targets = _extracted(title)

    assert people == [title]
    assert targets and all(title in event for event in targets)


def test_a_surname_that_is_an_ordinary_word_is_still_the_name() -> None:
    """Real card «Чёрный Виталий Владиславович»: only the title says «Чёрный» is a name."""
    card = {**CARDS[0], "title": {"rendered": "Чёрный Виталий Владиславович"}}
    BY_NAME["Чёрный Виталий Владиславович"] = card

    people, targets = _extracted("Чёрный Виталий Владиславович")

    assert people == ["Черный Виталий Владиславович"]
    assert targets == [["Черный Виталий Владиславович", *targets[0][1:]]]


def test_the_sources_are_registered() -> None:
    definition = get_source_definition("memopzk-figurants")

    assert definition.source_name == MEMOPZK_REGISTRY_SOURCE_NAME
    assert definition.supports_direct_fetch is False
    assert isinstance(definition.create_parser(), FigurantParser)
