"""Fixture transport for case search — reads local HTML instead of the network.

Used when ``SourceConfig.backend == "fixture"``. The fixture layout follows the
press crawler convention: a directory with named HTML files that correspond to
well-known search outcomes.

Files are matched to request URLs by exact filename:

* ``search-result-article-{article}.html`` — article search result
  (article ``205.1`` → ``search-result-article-205-1.html``)
* ``search-result-case-{case_number}.html`` — case_number search result
  (case number ``1-1688/2026`` → ``search-result-case-1-1688_2026.html``)
* ``case-card-{case_uid}.html`` — individual case card
* ``search-result-article-{article}.html`` — can serve as a known-empty result
  if it contains no case rows

Unknown requests return 404 — there are NO fallbacks to arbitrary fixtures.
The fixture is selected purely by the decoded request URL parameters; the
transport never inspects hidden state.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from court_monitor.domain.models import FetchHealth
from court_monitor.observability import get_logger
from court_monitor.sources.http_client import HttpResponse

_log = get_logger(__name__)


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
        """Select a fixture file by exact match on decoded URL parameters.

        Uses ``parse_qs`` to decode percent-encoded values (e.g. ``%2F`` →
        ``/``) before matching, so ``urlencode({"U1_CASE__CASE_NUMBERSS":
        "1-1688/2026"})`` correctly maps to
        ``search-result-case-1-1688_2026.html``.
        """
        qs = urlparse(url).query
        params = {k.lower(): v[0] for k, v in parse_qs(qs).items()} if qs else {}

        # Case-card lookup: ``name_op=case`` + ``case_uid=...``
        case_uid = params.get("case_uid")
        if case_uid and params.get("name_op") == "case":
            candidate = f"case-card-{case_uid}.html"
            if (self.root / candidate).exists():
                return candidate
            return None

        # Article search — exact match only.
        article = params.get("u1_defendant__law_articless")
        if article:
            normalised = article.replace(".", "-")
            candidate = f"search-result-article-{normalised}.html"
            if (self.root / candidate).exists():
                return candidate
            return None

        # Case-number search — exact match only.
        case_number = params.get("u1_case__case_numberss")
        if case_number:
            normalised = case_number.replace("/", "_")
            candidate = f"search-result-case-{normalised}.html"
            if (self.root / candidate).exists():
                return candidate
            return None

        return None
