"""Fixture transport for case search — reads local HTML instead of the network.

Used when ``SourceConfig.backend == "fixture"``. The fixture layout follows the
press crawler convention: a directory with named HTML files that correspond to
well-known search outcomes.

Files are matched to request URLs by filename:

* ``search-result-article-{article}.html`` — article search result
* ``search-result-case-{case_number}.html`` — case_number search result
* ``case-card-{case_uid}.html`` — individual case card
* ``search-result-empty.html`` — fallback for no-match scenarios
* ``search-result-*.html`` — any other result, chosen by suffix match

This is the same pattern used for press fixtures: deterministic, offline,
reproducible. The fixture is selected purely by the request URL; the
transport never inspects hidden state.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from court_monitor.domain.models import FetchHealth
from court_monitor.observability import get_logger
from court_monitor.sources.http_client import HttpResponse

_log = get_logger(__name__)


_ARTICLE_RE = re.compile(r"U1_DEFENDANT__LAW_ARTICLESS=([^&]+)", re.IGNORECASE)
_CASE_NUMBER_RE = re.compile(r"U1_CASE__CASE_NUMBERSS=([^&]+)", re.IGNORECASE)


class FixtureTransport:
    """Drop-in for :class:`HttpClient` that serves request URLs from disk."""

    def __init__(self, fixture_path: str | Path) -> None:
        self.root = Path(fixture_path)

    # HttpClient interface — only ``get`` is used by the case search adapter,
    # but we implement the context-manager methods so callers use it unchanged.
    def get(self, url: str) -> HttpResponse:  # noqa: A003 — name matches HttpClient
        if not self.root.exists():
            _log.warning("case_search.fixture.missing", path=str(self.root))
            return HttpResponse(status=404, text="", url=url, health=FetchHealth.http_error)

        filename = self._select_filename(url)
        file_path = self.root / filename if filename else None

        if file_path is None or not file_path.exists():
            _log.warning(
                "case_search.fixture.no_match",
                url=url,
                tried=filename,
                available=sorted(p.name for p in self.root.glob("*.html")),
            )
            return HttpResponse(status=404, text="", url=url, health=FetchHealth.http_error)

        content = file_path.read_text(encoding="utf-8", errors="replace")
        return HttpResponse(status=200, text=content, url=url, health=FetchHealth.ok)

    def close(self) -> None:  # pragma: no cover - trivial
        return

    def __enter__(self) -> FixtureTransport:
        return self

    def __exit__(self, *_exc: object) -> None:
        return

    def _select_filename(self, url: str) -> str | None:  # noqa: PLR0911
        qs = urlparse(url).query
        params = {k.lower(): v[0] for k, v in parse_qs(qs).items()} if qs else {}

        # Case-card lookup: ``name_op=case`` + ``case_uid=...``
        case_uid = params.get("case_uid")
        if case_uid and params.get("name_op") == "case":
            candidate = f"case-card-{case_uid}.html"
            if (self.root / candidate).exists():
                return candidate
            return "case-card-example.html"

        # Article search
        article_match = _ARTICLE_RE.search(qs or "")
        if article_match:
            article = article_match.group(1).replace(".", "-")
            candidate = f"search-result-article-{article}.html"
            if (self.root / candidate).exists():
                return candidate

        # Case-number search
        case_number_match = _CASE_NUMBER_RE.search(qs or "")
        if case_number_match:
            case_number = case_number_match.group(1).replace("/", "_")
            candidate = f"search-result-case-{case_number}.html"
            if (self.root / candidate).exists():
                return candidate

        # Empty default
        if (self.root / "search-result-empty.html").exists():
            return "search-result-empty.html"

        # Last-resort: any search-result fixture
        for p in sorted(self.root.glob("search-result-*.html")):
            return p.name
        return None
