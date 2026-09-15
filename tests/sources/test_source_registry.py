import asyncio

import httpx

from sources.article_parser import OvdInfoArticleParser
from sources.models import RawDocument, SourceReference
from sources.ovd_info.source_adapter import OvdInfoSourceAdapter
from sources.sota_vision.article_parser import SotaVisionArticleParser
from sources.sota_vision.source_adapter import SotaVisionSourceAdapter
from sources.source_adapter import SourceAdapter
from sources.source_registry import OVD_INFO, SOTA_VISION, SOURCES, get_source_definition


class FakeDocumentFetcher:
    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        raise AssertionError("fetch must not be called")


def _accepts_source_adapter(adapter: SourceAdapter) -> None:
    pass


def test_registry_contains_both_websites() -> None:
    assert {"ovd-info", "sota-vision"} <= set(SOURCES)
    assert SOURCES["ovd-info"] is OVD_INFO
    assert SOURCES["sota-vision"] is SOTA_VISION


def test_get_source_definition_returns_registered_source() -> None:
    assert get_source_definition("ovd-info") is OVD_INFO
    assert get_source_definition("sota-vision") is SOTA_VISION


def test_get_source_definition_rejects_unknown_source() -> None:
    try:
        get_source_definition("unknown-source")
    except ValueError as exc:
        assert "unknown-source" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_ovd_info_definition_builds_working_adapter_and_parser() -> None:
    async def run() -> None:
        async with httpx.AsyncClient() as client:
            adapter = OVD_INFO.create_adapter(client, FakeDocumentFetcher())
            parser = OVD_INFO.create_parser()

            assert isinstance(adapter, OvdInfoSourceAdapter)
            assert isinstance(parser, OvdInfoArticleParser)
            _accepts_source_adapter(adapter)

    asyncio.run(run())


def test_sota_vision_definition_builds_working_adapter_and_parser() -> None:
    async def run() -> None:
        async with httpx.AsyncClient() as client:
            adapter = SOTA_VISION.create_adapter(client, FakeDocumentFetcher())
            parser = SOTA_VISION.create_parser()

            assert isinstance(adapter, SotaVisionSourceAdapter)
            assert isinstance(parser, SotaVisionArticleParser)
            _accepts_source_adapter(adapter)

    asyncio.run(run())
