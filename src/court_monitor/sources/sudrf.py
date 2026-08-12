"""Adapter and crawler for sudrf.ru courts (ГАС «Правосудие»).

Two components:

1. :class:`SudrfAdapter` — legacy simple adapter that fetches configured paths.
   Kept for backward compatibility with existing fixtures and tests.

2. :class:`SudrfPressCrawler` — full press release crawler that:
   - Walks monthly archive pages
   - Extracts publication links (did=...)
   - Fetches individual publications
   - Returns ``FetchResult`` with proper metadata (title, published_at, external_id)
   - Supports incremental fetch (stops at known external_id)
   - Handles both HTTP and fixture backends

Per-court specifics are expressed through :class:`SourceConfig` (paths, backend,
fixture_path, court_name, press_module, case_module).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Protocol

from selectolax.parser import HTMLParser

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import FetchHealth, SourceBackend, SourceType
from court_monitor.observability import get_logger
from court_monitor.parsers.sudrf_press import parse_press_release
from court_monitor.sources.base import FetchProblem, FetchResult
from court_monitor.sources.http_client import HttpClient

_log = get_logger(__name__)

ADAPTER_VERSION = "sudrf-press-crawler-0.1"

_DID_RE = re.compile(r"did=(\d+)")
_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")


class KnownExternalIds(Protocol):
    """Protocol for checking if an external_id is already known (for incremental fetch)."""

    def __contains__(self, external_id: str) -> bool: ...


class SudrfAdapter:
    """Legacy adapter for sudrf.ru-style courts.

    Two backends:
      * ``fixture`` — reads ``*.html`` from ``fixture_path`` (tests / no-network).
      * ``http``    — fetches ``base_url + path`` for each configured path.
    """

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    def fetch_new(self) -> Iterator[FetchResult | FetchProblem]:
        if self.config.backend == SourceBackend.fixture:
            yield from self._fetch_fixture()
        elif self.config.backend == SourceBackend.http:
            yield from self._fetch_http()
        else:  # pragma: no cover - guarded by config loader
            raise ValueError(f"sudrf adapter: unsupported backend {self.config.backend!r}")

    def _fetch_fixture(self) -> Iterator[FetchResult]:
        if not self.config.fixture_path:
            _log.warning("sudrf.fixture.no_path", source=self.config.name)
            return
        root = Path(self.config.fixture_path)
        if not root.exists():
            _log.warning("sudrf.fixture.missing", path=str(root), source=self.config.name)
            return
        for html_file in sorted(root.glob("*.html")):
            content = html_file.read_text(encoding="utf-8")
            url = self._fixture_url_for(html_file)
            yield FetchResult.from_content(
                url=url,
                content=content,
                source_type=SourceType.sudrf,
                source_name=self.config.name,
                http_status=200,
            )

    def _fetch_http(self) -> Iterator[FetchResult | FetchProblem]:
        base = (self.config.base_url or "").rstrip("/")
        if not base:
            _log.warning("sudrf.http.no_base_url", source=self.config.name)
            return
        paths = self.config.paths or ("/",)
        with HttpClient() as client:
            for path in paths:
                url = base + (path if path.startswith("/") else "/" + path)
                resp = client.get(url)
                _log.info(
                    "sudrf.http.fetched",
                    source=self.config.name,
                    url=url,
                    status=resp.status,
                    health=str(resp.health),
                )
                if resp.health == FetchHealth.not_modified:
                    continue
                if resp.health != FetchHealth.ok or not resp.text:
                    yield FetchProblem(
                        url=url,
                        health=resp.health,
                        http_status=resp.status,
                        source_id=self.config.name,
                        source_name=self.config.name,
                    )
                    continue
                yield FetchResult.from_content(
                    url=url,
                    content=resp.text,
                    source_type=SourceType.sudrf,
                    source_name=self.config.name,
                    http_status=resp.status,
                )

    def _fixture_url_for(self, path: Path) -> str:
        base = (self.config.base_url or "https://fixture.local").rstrip("/")
        return f"{base}/fixture/{path.name}"


class SudrfPressCrawler:
    """Full press release crawler for sudrf.ru courts.

    Walks monthly archive pages, extracts publication links, fetches individual
    releases, and yields ``FetchResult`` objects with proper metadata.

    Supports two backends:
      * ``fixture`` — reads archive/release HTML from ``fixture_path``.
      * ``http``    — fetches from live court website.

    Incremental fetch: stops when it encounters an ``external_id`` already in
    the provided ``known_ids`` set (typically from the database).
    """

    def __init__(
        self,
        config: SourceConfig,
        *,
        known_ids: KnownExternalIds | None = None,
        full_rescan: bool = False,
    ) -> None:
        self.config = config
        self.known_ids = known_ids
        self.full_rescan = full_rescan

    def fetch_new(self) -> Iterator[FetchResult | FetchProblem]:
        if self.config.backend == SourceBackend.fixture:
            yield from self._fetch_fixture()
        elif self.config.backend == SourceBackend.http:
            yield from self._fetch_http()
        else:  # pragma: no cover - guarded by config loader
            raise ValueError(f"sudrf crawler: unsupported backend {self.config.backend!r}")

    def _fetch_fixture(self) -> Iterator[FetchResult]:
        if not self.config.fixture_path:
            _log.warning("sudrf.crawler.fixture.no_path", source=self.config.name)
            return
        root = Path(self.config.fixture_path)
        if not root.exists():
            _log.warning("sudrf.crawler.fixture.missing", path=str(root), source=self.config.name)
            return

        archive_files: list[Path] = []
        release_files: list[Path] = []

        for html_file in sorted(root.glob("*.html")):
            content = html_file.read_text(encoding="utf-8")
            if self._is_archive_page(content):
                archive_files.append(html_file)
            else:
                release_files.append(html_file)

        for release_file in release_files:
            content = release_file.read_text(encoding="utf-8")
            url = self._fixture_url_for(release_file)
            did = self._extract_did_from_filename(release_file.name)
            result = self._parse_release(content, url, did=did)
            if result is not None:
                if (
                    not self.full_rescan
                    and self.known_ids is not None
                    and result.external_id is not None
                    and result.external_id in self.known_ids
                ):
                    _log.info(
                        "sudrf.crawler.skip_known", source=self.config.name, did=result.external_id
                    )
                    continue
                yield result

    def _fetch_http(self) -> Iterator[FetchResult | FetchProblem]:
        base = (self.config.base_url or "").rstrip("/")
        if not base:
            _log.warning("sudrf.crawler.http.no_base_url", source=self.config.name)
            return

        press_module = self.config.press_module or "press_dep"
        archive_url = f"{base}/modules.php?name={press_module}&op=12"

        _log.info("sudrf.crawler.fetching", source=self.config.name, url=archive_url)
        with HttpClient() as client:
            resp = client.get(archive_url)
            if resp.health != FetchHealth.ok or not resp.text:
                yield FetchProblem(
                    url=archive_url,
                    health=resp.health,
                    http_status=resp.status,
                    source_id=self.config.name,
                    source_name=self.config.name,
                )
                return

            yield from self._crawl_archive(resp.text, base, client, press_module)

    def _crawl_archive(
        self,
        archive_html: str,
        base_url: str,
        client: HttpClient,
        press_module: str,
    ) -> Iterator[FetchResult | FetchProblem]:
        months = self._extract_archive_months(archive_html)
        if not months:
            _log.warning("sudrf.crawler.no_months", source=self.config.name)
            return

        _log.info("sudrf.crawler.months_found", source=self.config.name, count=len(months))
        for month in months:
            month_url = f"{base_url}/modules.php?name={press_module}&op=12&arc_list={month}"
            _log.info("sudrf.crawler.fetching_month", source=self.config.name, month=month)
            resp = client.get(month_url)
            if resp.health != FetchHealth.ok or not resp.text:
                yield FetchProblem(
                    url=month_url,
                    health=resp.health,
                    http_status=resp.status,
                    source_id=self.config.name,
                    source_name=self.config.name,
                )
                continue

            yield from self._process_archive_html(
                resp.text, month_url, client, base_url, press_module
            )

    def _process_archive_html(
        self,
        html: str,
        archive_url: str,
        client: HttpClient | None = None,
        base_url: str | None = None,
        press_module: str | None = None,
    ) -> Iterator[FetchResult | FetchProblem]:
        links = self._extract_publication_links(html)
        if not links:
            _log.warning("sudrf.crawler.no_links", source=self.config.name, archive_url=archive_url)
            return

        for did, title in links:
            if not self.full_rescan and self.known_ids is not None and did in self.known_ids:
                _log.info("sudrf.crawler.skip_known", source=self.config.name, did=did)
                continue

            if client is not None and base_url is not None:
                release_url = (
                    f"{base_url}/modules.php?name={press_module or 'press_dep'}&op=1&did={did}"
                )
                _log.info("sudrf.crawler.fetching_release", source=self.config.name, did=did)
                resp = client.get(release_url)
                if resp.health != FetchHealth.ok or not resp.text:
                    yield FetchProblem(
                        url=release_url,
                        health=resp.health,
                        http_status=resp.status,
                        source_id=self.config.name,
                        source_name=self.config.name,
                    )
                    continue
                result = self._parse_release(resp.text, release_url, did=did, title=title)
                if result is not None:
                    yield result
            else:
                _log.warning("sudrf.crawler.no_client_for_fetch", source=self.config.name, did=did)

    def _parse_release(
        self,
        html: str,
        url: str,
        *,
        did: str | None = None,
        title: str | None = None,
    ) -> FetchResult | None:
        try:
            parsed = parse_press_release(html)
        except Exception as exc:
            _log.exception("sudrf.crawler.parse_failed", url=url, error=str(exc))
            return None

        external_id = did or self._extract_did_from_url(url)
        published_at_date = parsed.published_at
        published_at = (
            datetime(published_at_date.year, published_at_date.month, published_at_date.day)
            if published_at_date
            else None
        )

        return FetchResult.from_content(
            url=url,
            content=html,
            text=parsed.text,
            source_type=SourceType.sudrf,
            source_name=self.config.name,
            source_id=self.config.name,
            external_id=external_id,
            title=parsed.title or title,
            published_at=published_at,
            adapter_version=ADAPTER_VERSION,
        )

    def _is_archive_page(self, html: str) -> bool:
        return "divArchiveList" in html or "arc_list=" in html

    def _extract_archive_months(self, html: str) -> list[str]:
        tree = HTMLParser(html)
        months: list[str] = []
        for node in tree.css("a.newsArchMonth"):
            href = node.attributes.get("href", "") or ""
            match = re.search(r"arc_list=(\d{4}-\d{2})", href)
            if match:
                months.append(match.group(1))
        return months

    def _extract_publication_links(self, html: str) -> list[tuple[str, str]]:
        tree = HTMLParser(html)
        links: list[tuple[str, str]] = []
        press_mod = self.config.press_module or "press_dep"
        for node in tree.css(f"a[href*='{press_mod}'][href*='did=']"):
            href = node.attributes.get("href", "") or ""
            match = _DID_RE.search(href)
            if not match:
                continue
            did = match.group(1)
            title = node.text(strip=True)
            if title:
                links.append((did, title))
        return links

    def _extract_did_from_url(self, url: str) -> str | None:
        if not url:
            return None
        match = _DID_RE.search(url)
        return match.group(1) if match else None

    def _extract_did_from_filename(self, filename: str) -> str | None:
        if not filename:
            return None
        match = re.search(r"release-(\d+)", filename)
        return match.group(1) if match else None

    def _fixture_url_for(self, path: Path) -> str:
        base = (self.config.base_url or "https://fixture.local").rstrip("/")
        return f"{base}/fixture/{path.name}"


def get_press_crawler(
    config: SourceConfig,
    *,
    known_ids: KnownExternalIds | None = None,
    full_rescan: bool = False,
) -> SudrfPressCrawler:
    """Factory for creating a press crawler."""
    return SudrfPressCrawler(config, known_ids=known_ids, full_rescan=full_rescan)
