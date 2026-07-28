"""Source adapter for Telegram public channel previews.

Reachable without the Telegram client and without any credentials: the public
web preview at ``https://t.me/s/<username>`` returns a static HTML page with
the most recent posts. We parse it (see :mod:`court_monitor.parsers.telegram_post`)
and emit one :class:`FetchResult` per post.

This adapter is intentionally HTTP-only. Playwright / browser automation is NOT
used — the preview is plain HTML. Individual post permalinks (``t.me/<u>/<id>``)
are JS-rendered when fetched directly, so the preview is the canonical
structured source and we do not fetch permalinks one by one.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from court_monitor.domain.models import FetchHealth, SourceType
from court_monitor.observability import get_logger
from court_monitor.parsers.telegram_post import parse_channel_preview
from court_monitor.sources.base import FetchProblem, FetchResult
from court_monitor.sources.http_client import HttpClient

_log = get_logger(__name__)

ADAPTER_VERSION = "telegram-channel-0.1"
PREVIEW_CONTENT_TYPE = "text/html; charset=utf-8"


class TelegramChannelAdapter:
    """Adapter for ``t.me/s/<username>`` public previews.

    Two modes:

    * ``live=False`` (default) — read a saved preview fixture. Used for tests
      and the no-network default.
    * ``live=True`` — fetch ``t.me/s/<username>`` over HTTP via the polite
      :class:`HttpClient` (UA, timeout, per-host delay, retries, block detector).
    """

    def __init__(
        self,
        *,
        source_id: str,
        name: str,
        username: str,
        url: str,
        fixture_path: str | Path | None = None,
    ) -> None:
        self.source_id = source_id
        self.name = name
        self.username = username.lstrip("@")
        self.url = url
        self.fixture_path = Path(fixture_path) if fixture_path else None

    def fetch_new(
        self, *, live: bool = False, limit: int | None = None
    ) -> Iterator[FetchResult | FetchProblem]:
        html, fetched_via, http_status, health = self._load_html(live=live)
        if html is None:
            if live and health is not None:
                yield FetchProblem(
                    url=self._preview_url(),
                    health=health,
                    http_status=http_status,
                    source_id=self.source_id,
                    source_name=self.name,
                )
            return

        posts = parse_channel_preview(html)
        if not posts:
            _log.warning(
                "telegram.empty_preview",
                source=self.source_id,
                live=live,
                url=self._preview_url(),
            )
            return
        _log.info(
            "telegram.preview_parsed",
            source=self.source_id,
            posts=len(posts),
            live=live,
        )

        chosen = posts if limit is None else posts[: max(0, limit)]
        for post in chosen:
            yield self._to_fetch_result(post, html_source=fetched_via, http_status=http_status)

    def _load_html(self, *, live: bool) -> tuple[str | None, str, int, FetchHealth | None]:
        """Return ``(html, html_source, http_status, health)``.

        ``health`` is ``None`` whenever the caller should NOT raise a
        ReviewItem: fixture mode (a local dev/test setup issue, not a
        production source problem) and ``not_modified`` (no new content is a
        normal outcome, not a failure). It carries the actual
        :class:`FetchHealth` only for a real live-fetch problem.
        """
        if not live:
            if self.fixture_path is None or not self.fixture_path.exists():
                _log.warning(
                    "telegram.fixture.missing",
                    source=self.source_id,
                    path=str(self.fixture_path),
                )
                return None, "fixture-missing", 0, None
            return self.fixture_path.read_text(encoding="utf-8"), "fixture", 200, None

        url = self._preview_url()
        with HttpClient() as client:
            resp = client.get(url)
        _log.info(
            "telegram.http.fetched",
            source=self.source_id,
            url=url,
            status=resp.status,
            health=str(resp.health),
        )
        if resp.health == FetchHealth.not_modified:
            return None, f"http:{resp.health}", resp.status, None
        if resp.health != FetchHealth.ok or not resp.text:
            return None, f"http:{resp.health}", resp.status, resp.health
        return resp.text, f"http:{resp.health}", resp.status, None

    def _preview_url(self) -> str:
        return f"https://t.me/s/{self.username}"

    def _to_fetch_result(self, post, *, html_source: str, http_status: int) -> FetchResult:
        permalink = post.permalink or self.url
        return FetchResult.from_content(
            url=permalink,
            canonical_url=permalink,
            content=post.html,
            text=post.text,
            source_type=SourceType.telegram,
            source_name=self.name,
            source_id=self.source_id,
            external_id=post.post_id,
            content_type=PREVIEW_CONTENT_TYPE,
            adapter_version=ADAPTER_VERSION,
            http_status=http_status,
            published_at=post.published_at,
            title=post.author or self.name,
        )


def adapter_from_registry_entry(entry, *, fixture_path: str | Path | None = None):
    """Build a :class:`TelegramChannelAdapter` from a registry entry.

    Accepts any object exposing ``id``, ``name``, ``username`` and ``url``.
    """
    return TelegramChannelAdapter(
        source_id=entry.id,
        name=entry.name,
        username=entry.username or entry.id,
        url=entry.url,
        fixture_path=fixture_path,
    )
