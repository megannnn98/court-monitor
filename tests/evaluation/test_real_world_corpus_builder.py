"""Corpus builder over the real OVD-Info adapter and parser, without network."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from evaluation.real_world.corpus_builder import (
    CorpusBuildConfig,
    RealWorldCorpusBuilder,
    parse_targets,
    published_date_hint,
)
from evaluation.real_world.corpus_cache import CacheEntryStatus, RawCorpusCache
from evaluation.real_world.models import SourceCorpusStatus, TemporalPeriod
from evaluation.real_world.rate_limit import (
    STOP_AFTER_CONSECUTIVE_FAILURES,
    DomainRateLimiter,
    SourceHealth,
    SourceStoppedError,
)
from sources.source_registry import OVD_INFO

LISTING = "https://ovd.info/express-news"


def _article_html(day: date, text: str) -> bytes:
    return f"""<html><body>
    <h1 class="express-text-heading">Заголовок {day}</h1>
    <div id="article_published">{day:%d.%m.%Y}, 12:00</div>
    <div class="field--name-field-express-text"><p>{text}</p></div>
    </body></html>""".encode()


def _slug(day: date, index: int) -> str:
    return f"/express-news/{day:%Y/%m/%d}/story-{index}"


DAYS = [date(2026, 9, 2), date(2026, 8, 31), date(2026, 5, 1), date(2026, 3, 1), date(2026, 2, 20)]


class Site:
    def __init__(self, *, fail_article: int | None = None, block_after: int | None = None) -> None:
        self.requests: list[str] = []
        self.fail_article = fail_article
        self.block_after = block_after

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if self.block_after is not None and len(self.requests) > self.block_after:
            return httpx.Response(429, request=request)
        if url == LISTING:
            links = "".join(
                f'<a href="{_slug(day, index)}">x</a>' for index, day in enumerate(DAYS)
            )
            return httpx.Response(200, content=f"<html>{links}</html>".encode())
        if url.startswith(f"{LISTING}?page="):
            return httpx.Response(200, content=b"<html></html>")
        for index, day in enumerate(DAYS):
            if url.endswith(_slug(day, index)):
                if index == self.fail_article:
                    return httpx.Response(404, request=request)
                person = "Иван Петров и Мария Сидорова" if index % 2 else "Олег Кузнецов"
                return httpx.Response(200, content=_article_html(day, f"{person}: текст {index}."))
        return httpx.Response(404, request=request)


def _builder(site: Site, cache_dir: Path, **config: object) -> RealWorldCorpusBuilder:
    return RealWorldCorpusBuilder(
        sources={OVD_INFO.name: OVD_INFO},
        cache=RawCorpusCache(cache_dir),
        config=CorpusBuildConfig(
            period_start=date(2026, 3, 1),
            period_end=date(2026, 8, 31),
            targets={"ovd-info": 10},
            total_target=10,
            evaluation_sample_size=2,
            min_interval_seconds=0,
            discovery_limit=10,
            **config,  # type: ignore[arg-type]
        ),
        create_transport=lambda: httpx.MockTransport(site.handle),
        retry_base_delay_seconds=0,
        now=lambda: datetime(2026, 9, 15, tzinfo=UTC),
    )


def test_builds_manifest_of_in_period_articles_without_fetching_out_of_period(
    tmp_path: Path,
) -> None:
    site = Site()
    manifest = asyncio.run(_builder(site, tmp_path).build())

    assert [article.external_id for article in manifest.articles] == [
        _slug(date(2026, 3, 1), 3),
        _slug(date(2026, 5, 1), 2),
        _slug(date(2026, 8, 31), 1),
    ]
    # The URL date hint excludes 2026-02-20 and 2026-09-02 without a request.
    assert not any("story-0" in url or "story-4" in url for url in site.requests)
    report = manifest.sources[0]
    assert report.status is SourceCorpusStatus.OK
    assert (report.discovered, report.fetch_candidates, report.fetched, report.selected) == (
        5,
        3,
        3,
        3,
    )
    assert all(len(article.content_hash) == 64 for article in manifest.articles)
    assert {article.corpus_split for article in manifest.articles} <= set(TemporalPeriod)
    assert sum(article.evaluation_sample for article in manifest.articles) == 2
    assert "multi_person" in manifest.articles[2].sampling_tags


def test_rebuild_from_cache_is_identical_and_sends_no_request(tmp_path: Path) -> None:
    first = asyncio.run(_builder(Site(), tmp_path).build())
    offline_site = Site()
    second = asyncio.run(_builder(offline_site, tmp_path, offline=True).build())

    assert offline_site.requests == []
    assert second.content_fingerprint() == first.content_fingerprint()


def test_resumed_build_uses_the_cache(tmp_path: Path) -> None:
    asyncio.run(_builder(Site(), tmp_path).build())
    site = Site()
    manifest = asyncio.run(_builder(site, tmp_path).build())

    assert site.requests == [LISTING, f"{LISTING}?page=1"]
    assert manifest.sources[0].from_cache == 3


def test_failed_article_does_not_lose_the_others(tmp_path: Path) -> None:
    manifest = asyncio.run(_builder(Site(fail_article=2), tmp_path).build())

    assert len(manifest.articles) == 2
    assert manifest.sources[0].fetch_failed == 1


def test_http_429_stops_the_source_and_is_reported(tmp_path: Path) -> None:
    site = Site(block_after=3)
    manifest = asyncio.run(_builder(site, tmp_path).build())

    report = manifest.sources[0]
    assert report.status is SourceCorpusStatus.STOPPED
    assert report.stop_reason == "HTTP 429"
    # One 429, then nothing more: no retry around the site's refusal.
    assert [url for url in site.requests].count(site.requests[-1]) == 1
    assert len(site.requests) == 4
    cached = RawCorpusCache(tmp_path).entries("ovd-info")
    assert [entry.status for entry in cached.values()] == [CacheEntryStatus.ARTICLE]


def test_rate_limiter_spaces_requests_per_domain() -> None:
    now = [0.0]
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = DomainRateLimiter(1.0, clock=lambda: now[0], sleep=sleep)

    async def run() -> None:
        await limiter.wait("ovd.info")
        now[0] += 0.25
        await limiter.wait("ovd.info")
        await limiter.wait("sota.vision")

    asyncio.run(run())
    assert sleeps == [0.75]


def test_repeated_server_errors_slow_down_then_stop() -> None:
    limiter = DomainRateLimiter(1.0)
    health = SourceHealth(limiter)
    for _ in range(3):
        health.observe_status("sota.vision", 503)
    assert limiter.interval("sota.vision") == 2.0
    for _ in range(STOP_AFTER_CONSECUTIVE_FAILURES - 3):
        health.observe_status("sota.vision", 503)
    with pytest.raises(SourceStoppedError):
        health.check("sota.vision")
    health.check("ovd.info")


def test_success_resets_consecutive_failures() -> None:
    health = SourceHealth(DomainRateLimiter(0))
    for _ in range(STOP_AFTER_CONSECUTIVE_FAILURES - 1):
        health.observe_status("ovd.info", 500)
    health.observe_status("ovd.info", 200)
    health.observe_status("ovd.info", 500)
    health.check("ovd.info")


def test_published_date_hint_and_targets() -> None:
    assert published_date_hint("/express-news/2026/03/01/slug") == date(2026, 3, 1)
    assert published_date_hint("/some-sota-slug/") is None
    assert published_date_hint("/express-news/2026/13/01/slug") is None
    assert parse_targets(["ovd-info=600", "sota-vision=400"]) == {
        "ovd-info": 600,
        "sota-vision": 400,
    }
    with pytest.raises(ValueError):
        parse_targets(["ovd-info"])
