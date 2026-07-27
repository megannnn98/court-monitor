"""Extraction of УК РФ article references from free text.

Captures grammatical forms of "статья/ст." (статья, статьи, статье, статьёй,
статьей, статью, статьями, ...) and bare numbers adjacent to "УК"/"УК РФ":

  * ``ст. 205.1 УК РФ``         (explicit, high confidence)
  * ``статьей 282``             (prefix only, medium confidence)
  * ``205 УК РФ``               (bare near УК, lower confidence)

Each match is returned as an :class:`ExtractedFactDTO` with a quote (the
matched fragment) and a confidence reflecting how explicit the reference is.
"""

from __future__ import annotations

import re

from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import VerificationStatus

_ARTICLE_NUM = r"\d{1,3}(?:\.\d{1,2})?"
# Word forms of "статья" (статья/статьи/статье/статьёй/статьей/статью/статьями/...)
# plus the abbreviation "ст.". Bare "стат[а-яё]+" deliberately does NOT match
# unrelated words starting with other letters.
_PREFIX = r"(?:ст\.\s*|стат[а-яё]+\s+)"
_UK = r"(?:УК\s*РФ|УК(?![а-яёА-ЯЁ]))"

_EXPLICIT_RE = re.compile(
    rf"{_PREFIX}({_ARTICLE_NUM})(?=[^0-9.]).{{0,40}}?{_UK}",
    re.IGNORECASE | re.DOTALL,
)
_WITH_PREFIX_RE = re.compile(rf"{_PREFIX}({_ARTICLE_NUM})\b", re.IGNORECASE)
_BARE_NEAR_UK_RE = re.compile(rf"\b({_ARTICLE_NUM})\s+{_UK}", re.IGNORECASE)


def extract_articles(text: str, *, source_url: str | None = None) -> list[ExtractedFactDTO]:
    if not text:
        return []
    found: list[ExtractedFactDTO] = []
    seen_spans: set[tuple[int, int]] = set()

    for m in _EXPLICIT_RE.finditer(text):
        _add(
            found,
            _normalize_article(m.group(1)),
            _quote_around(text, m.start(), m.end()),
            source_url,
            confidence=0.95,
            method="regex:article:explicit",
            seen_spans=seen_spans,
            start=m.start(),
            end=m.end(),
        )

    for m in _WITH_PREFIX_RE.finditer(text):
        if _overlaps(seen_spans, m.start(), m.end()):
            continue
        _add(
            found,
            _normalize_article(m.group(1)),
            _quote_around(text, m.start(), m.end()),
            source_url,
            confidence=0.7,
            method="regex:article:prefix",
            seen_spans=seen_spans,
            start=m.start(),
            end=m.end(),
        )

    for m in _BARE_NEAR_UK_RE.finditer(text):
        if _overlaps(seen_spans, m.start(), m.end()):
            continue
        _add(
            found,
            _normalize_article(m.group(1)),
            _quote_around(text, m.start(), m.end()),
            source_url,
            confidence=0.6,
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
        a = str(dto.value)
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _normalize_article(raw: str) -> str:
    return raw.strip().rstrip(".")


def _quote_around(text: str, start: int, end: int, *, window: int = 40) -> str:
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()


def _overlaps(spans: set[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < e and end > s for s, e in spans)


def _add(
    out: list[ExtractedFactDTO],
    article: str,
    quote: str,
    source_url: str | None,
    *,
    confidence: float,
    method: str,
    seen_spans: set[tuple[int, int]],
    start: int,
    end: int,
) -> None:
    if not article:
        return
    seen_spans.add((start, end))
    out.append(
        ExtractedFactDTO(
            entity="case",
            field="criminal_article",
            value=article,
            verification_status=VerificationStatus.inferred,
            confidence=confidence,
            quote=quote,
            source_url=source_url,
            extraction_method=method,
        )
    )
