"""Generic adapter for the sudrf.ru platform (ГАС «Правосудие»).

Per-court specifics are expressed through :class:`SourceConfig` (paths, backend,
fixture_path). The adapter yields :class:`FetchResult` objects; parsing is done
downstream by the parser named in ``config.parser`` (default ``sudrf_press``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import SourceBackend, SourceType
from court_monitor.observability import get_logger
from court_monitor.sources.base import FetchResult
from court_monitor.sources.http_client import HttpClient

_log = get_logger(__name__)


class SudrfAdapter:
    """Adapter for sudrf.ru-style courts.

    Two backends:
      * ``fixture`` — reads ``*.html`` from ``fixture_path`` (tests / no-network).
      * ``http``    — fetches ``base_url + path`` for each configured path.
    """

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    def fetch_new(self) -> Iterator[FetchResult]:
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

    def _fetch_http(self) -> Iterator[FetchResult]:
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
                if resp.health.value != "ok" or not resp.text:
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
