"""Parse the live Rosfinmonitoring list published as HTML at fedsfm.ru.

The file exports (XML/DBF/ZIP) handled by :mod:`fedsfm` require an operator to
download them by hand. The public web page carries the same list inline — one
``<li>`` per entry, ~23k of them — so it can be fetched unattended.

Entries are heterogeneous. Physical persons look like::

    1. АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ, 08.06.1996 г.р. , П. МАМЕДКАЛА …;
    742. ИВАНОВ ИВАН АЛЕКСАНДРОВИЧ*, 01.01.1990 г.р. , Г. МОСКВА;

but the same list also holds organizations (with ``ИНН``/``ОГРН``, or none at
all) and "ОБЪЕДИНЕНИЕ" entries naming several people inside one record. Only
single physical persons are extracted — ``PersonRecord`` has no meaningful
shape for an organization, and splitting a multi-person entry is a separate
problem. Everything skipped is counted in ``ParseResult.unrecognized`` rather
than dropped silently, so a change in the page's wording shows up as a jump in
that number instead of as quietly missing people.

The trailing ``*`` on a name is a footnote marker used by the registry, not
part of the name.
"""

from __future__ import annotations

import re
from pathlib import Path

from selectolax.parser import HTMLParser

from court_monitor.config.settings import settings
from court_monitor.domain.models import FetchHealth
from court_monitor.sources.fedsfm import (
    ParseResult,
    PersonRow,
    _normalize_date,
    _normalize_name,
)
from court_monitor.sources.http_client import HttpClient

LIST_URL = "https://fedsfm.ru/documents/terrorists-catalog-portal-act"
ADAPTER_VERSION = "rfm-html-0.1"


class FedsfmFetchError(RuntimeError):
    """The list page could not be retrieved."""


def ca_bundle_path() -> Path:
    """Trust anchors for fedsfm.ru — see config/ca/README.md for provenance."""
    return settings.config_path / "ca" / "fedsfm_ru_chain.pem"


def fetch_live_html(*, url: str = LIST_URL, ca_bundle: Path | None = None) -> str:
    """Download the published list page with fedsfm's CA chain pinned.

    Raises :class:`FedsfmFetchError` rather than returning a partial page, so a
    blocked or failed fetch can never be mistaken for "the list shrank".
    """
    bundle = ca_bundle or ca_bundle_path()
    if not bundle.exists():
        raise FedsfmFetchError(
            f"CA bundle not found: {bundle}. fedsfm.ru is signed by the Russian "
            "Ministry of Digital Development CA, which is absent from the system "
            "trust store — see config/ca/README.md."
        )

    with HttpClient(verify=str(bundle)) as client:
        response = client.get(url)

    if response.health is not FetchHealth.ok:
        raise FedsfmFetchError(
            f"fetch failed: health={response.health} status={response.status} url={url}"
        )
    if not response.text.strip():
        raise FedsfmFetchError(f"empty body from {url}")
    return response.text


_CAPS_WORD = r"[А-ЯЁ][А-ЯЁ\-]*"

# "<n>. SURNAME NAME [PATRONYMIC [OGLY]]<*>, DD.MM.YYYY [г.р.] , <birthplace>;"
_PERSON_RE = re.compile(
    r"^(?P<num>\d+)\.\s+"
    rf"(?P<name>{_CAPS_WORD}(?:\s+{_CAPS_WORD}){{1,3}})"
    r"\s*\*?\s*,\s*"
    r"(?P<dob>\d{2}\.\d{2}\.\d{4})"
    r"\s*(?:г\.?\s*р\.?|года\s+рождения)?\s*,?\s*"
    r"(?P<place>.*?)\s*;?\s*$",
    re.IGNORECASE,
)

_NUMBERED_RE = re.compile(r"^\d+\.\s")


def parse_terrorists_html(html: str, *, source_url: str = LIST_URL) -> ParseResult:
    """Extract physical persons from the published HTML list."""
    result = ParseResult(format_detected="html")
    if not html:
        result.errors.append("Empty HTML")
        return result

    entries = [
        text for li in HTMLParser(html).css("li") if _NUMBERED_RE.match(text := li.text(strip=True))
    ]
    result.total_records = len(entries)
    if not entries:
        result.errors.append("No numbered <li> entries found — page layout may have changed")
        return result

    for entry in entries:
        row = _entry_to_row(entry, source_url=source_url)
        if row is None:
            result.unrecognized += 1
            continue
        result.rows.append(row)
    result.recognized = len(result.rows)
    return result


def _entry_to_row(entry: str, *, source_url: str) -> PersonRow | None:
    match = _PERSON_RE.match(entry)
    if match is None:
        return None

    name = match.group("name").strip()
    # "ОБЪЕДИНЕНИЕ, ЧЛЕНАМИ КОТОРОГО ЯВЛЯЮТСЯ: …" and organization names can
    # still satisfy the shape above; a person is never described by these.
    if any(marker in name for marker in _NON_PERSON_MARKERS):
        return None

    normalized, confidence = _normalize_name(name)
    if len(normalized.split()) < 2:
        return None

    place = match.group("place").strip().rstrip(";,").strip() or None
    return PersonRow(
        raw_name=name,
        normalized_name=normalized,
        search_name=normalized,
        normalization_confidence=confidence,
        normalization_method="rfm-html",
        birth_date=_normalize_date(match.group("dob")),
        birth_place=place,
        category=None,
        source_ref=match.group("num"),
        added_date=None,
        raw_line=entry,
    )


# Words that mark an entry as something other than one named individual.
_NON_PERSON_MARKERS = (
    "ОБЪЕДИНЕНИЕ",
    "ОРГАНИЗАЦИЯ",
    "ДВИЖЕНИЕ",
    "ГРУППИРОВКА",
    "ОБЩЕСТВО",
    "ФОНД",
    "ЦЕНТР",
    "АССОЦИАЦИЯ",
    "УЧРЕЖДЕНИЕ",
    "ПАРТИЯ",
    "СООБЩЕСТВО",
)
