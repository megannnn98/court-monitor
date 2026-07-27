"""Extraction of УК РФ article references from free text.

Captures full structured references including article (статья/ст.), part (часть/ч.),
and point (пункт/п.):

  * ``ст. 205.1 УК РФ``                          → article=205.1
  * ``ч. 2 ст. 205.1 УК РФ``                      → article=205.1, part=2
  * ``п. «а» ч. 2 ст. 205 УК РФ``                 → article=205, part=2, point=а
  * ``часть 2 статьи 205.2 УК РФ``                 → article=205.2, part=2

Each match is returned as an :class:`ExtractedFactDTO` with a structured dict value,
a quote (the matched fragment), and a confidence reflecting how explicit the reference is.
"""

from __future__ import annotations

import re

from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import VerificationStatus

_ARTICLE_NUM = r"\d{1,3}(?:\.\d{1,2})?"
_PART_NUM = r"\d{1,2}"
# Point: letter in quotes like «а» or bare letter
_POINT = r"[а-яёa-z]"

# Prefixes
_ART_PREFIX = r"(?:ст\.\s*|стат[а-яё]+\s+)"
_PART_PREFIX = r"(?:ч\.\s*|част[а-яё]+\s+)"
_POINT_PREFIX = r"(?:п\.\s*|пункт[а-яё]*\s+)"

_UK = r"(?:УК\s*РФ|УК(?![а-яёА-ЯЁ]))"

# Full structured: point + part + article + UK
_FULL_RE = re.compile(
    rf"(?:{_POINT_PREFIX}[«\"]?({_POINT})[»\"]?\s*)?"
    rf"(?:{_PART_PREFIX}({_PART_NUM})\s*)"
    rf"{_ART_PREFIX}({_ARTICLE_NUM})"
    rf"(?=[^0-9.]).{{0,40}}?{_UK}",
    re.IGNORECASE | re.DOTALL,
)

# Part + article + UK
_PART_ART_RE = re.compile(
    rf"(?:{_PART_PREFIX}({_PART_NUM})\s*)"
    rf"{_ART_PREFIX}({_ARTICLE_NUM})"
    rf"(?=[^0-9.]).{{0,40}}?{_UK}",
    re.IGNORECASE | re.DOTALL,
)

# Explicit: article + UK (no part/point)
_EXPLICIT_RE = re.compile(
    rf"{_ART_PREFIX}({_ARTICLE_NUM})(?=[^0-9.]).{{0,40}}?{_UK}",
    re.IGNORECASE | re.DOTALL,
)

# Prefix only: статья/ст. + number (no UK required)
_WITH_PREFIX_RE = re.compile(rf"{_ART_PREFIX}({_ARTICLE_NUM})\b", re.IGNORECASE)

# Bare number near УК
_BARE_NEAR_UK_RE = re.compile(rf"\b({_ARTICLE_NUM})\s+{_UK}", re.IGNORECASE)


def extract_articles(text: str, *, source_url: str | None = None) -> list[ExtractedFactDTO]:
    if not text:
        return []
    found: list[ExtractedFactDTO] = []
    seen_spans: set[tuple[int, int]] = set()

    # 1. Full structured: point + part + article + UK
    for m in _FULL_RE.finditer(text):
        _add(
            found,
            article=_normalize(m.group(3)),
            part=m.group(2),
            point=_normalize_point(m.group(1)),
            code="УК РФ",
            quote=_quote_around(text, m.start(), m.end()),
            source_url=source_url,
            confidence=0.98,
            method="regex:article:full_structured",
            seen_spans=seen_spans,
            start=m.start(),
            end=m.end(),
        )

    # 2. Part + article + UK
    for m in _PART_ART_RE.finditer(text):
        if _overlaps(seen_spans, m.start(), m.end()):
            continue
        _add(
            found,
            article=_normalize(m.group(2)),
            part=m.group(1),
            point=None,
            code="УК РФ",
            quote=_quote_around(text, m.start(), m.end()),
            source_url=source_url,
            confidence=0.95,
            method="regex:article:part_article",
            seen_spans=seen_spans,
            start=m.start(),
            end=m.end(),
        )

    # 3. Explicit: article + UK
    for m in _EXPLICIT_RE.finditer(text):
        if _overlaps(seen_spans, m.start(), m.end()):
            continue
        _add(
            found,
            article=_normalize(m.group(1)),
            part=None,
            point=None,
            code="УК РФ",
            quote=_quote_around(text, m.start(), m.end()),
            source_url=source_url,
            confidence=0.90,
            method="regex:article:explicit",
            seen_spans=seen_spans,
            start=m.start(),
            end=m.end(),
        )

    # 4. Prefix only: статья/ст. + number
    for m in _WITH_PREFIX_RE.finditer(text):
        if _overlaps(seen_spans, m.start(), m.end()):
            continue
        _add(
            found,
            article=_normalize(m.group(1)),
            part=None,
            point=None,
            code=None,
            quote=_quote_around(text, m.start(), m.end()),
            source_url=source_url,
            confidence=0.70,
            method="regex:article:prefix",
            seen_spans=seen_spans,
            start=m.start(),
            end=m.end(),
        )

    # 5. Bare number near УК
    for m in _BARE_NEAR_UK_RE.finditer(text):
        if _overlaps(seen_spans, m.start(), m.end()):
            continue
        _add(
            found,
            article=_normalize(m.group(1)),
            part=None,
            point=None,
            code="УК РФ",
            quote=_quote_around(text, m.start(), m.end()),
            source_url=source_url,
            confidence=0.60,
            method="regex:article:bare_near_uk",
            seen_spans=seen_spans,
            start=m.start(),
            end=m.end(),
        )

    return found


def extract_article_strings(text: str) -> list[str]:
    """Return just the normalized article numbers found in ``text`` (unique, ordered)."""
    out: list[str] = []
    seen: set[str] = set()
    for dto in extract_articles(text):
        a = str(dto.value.get("article", ""))
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _normalize(raw: str | None) -> str | None:
    if raw is None:
        return None
    return raw.strip().rstrip(".")


def _normalize_point(raw: str | None) -> str | None:
    if raw is None:
        return None
    return raw.strip().strip("«»\"'").lower()


from court_monitor.extraction._utils import quote_around as _quote_around  # noqa: E402


def _overlaps(spans: set[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < e and end > s for s, e in spans)


def _add(
    out: list[ExtractedFactDTO],
    *,
    article: str | None,
    part: str | None,
    point: str | None,
    code: str | None,
    quote: str,
    source_url: str | None,
    confidence: float,
    method: str,
    seen_spans: set[tuple[int, int]],
    start: int,
    end: int,
) -> None:
    if not article:
        return
    seen_spans.add((start, end))
    value: dict[str, str | None] = {"article": article}
    if part is not None:
        value["part"] = part
    if point is not None:
        value["point"] = point
    if code is not None:
        value["code"] = code
    out.append(
        ExtractedFactDTO(
            entity="case",
            field="criminal_article",
            value=value,
            verification_status=VerificationStatus.inferred,
            confidence=confidence,
            quote=quote,
            source_url=source_url,
            extraction_method=method,
        )
    )
