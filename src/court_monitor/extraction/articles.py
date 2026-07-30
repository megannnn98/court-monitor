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
from dataclasses import dataclass

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


@dataclass(frozen=True)
class _Pass:
    """One regex sweep over the text, most specific first.

    Order matters: every pass skips spans an earlier one already claimed, so
    "п. «а» ч. 2 ст. 205 УК РФ" is recorded once at 0.98 rather than five
    times at descending confidence.
    """

    pattern: re.Pattern[str]
    article_group: int
    part_group: int | None
    point_group: int | None
    code: str | None
    confidence: float
    method: str


_PASSES: tuple[_Pass, ...] = (
    _Pass(_FULL_RE, 3, 2, 1, "УК РФ", 0.98, "full_structured"),
    _Pass(_PART_ART_RE, 2, 1, None, "УК РФ", 0.95, "part_article"),
    _Pass(_EXPLICIT_RE, 1, None, None, "УК РФ", 0.90, "explicit"),
    _Pass(_WITH_PREFIX_RE, 1, None, None, None, 0.70, "prefix"),
    _Pass(_BARE_NEAR_UK_RE, 1, None, None, "УК РФ", 0.60, "bare_near_uk"),
)


def extract_articles(text: str, *, source_url: str | None = None) -> list[ExtractedFactDTO]:
    if not text:
        return []
    found: list[ExtractedFactDTO] = []
    seen_spans: set[tuple[int, int]] = set()

    for sweep in _PASSES:
        for m in sweep.pattern.finditer(text):
            if _overlaps(seen_spans, m.start(), m.end()):
                continue
            _add(
                found,
                article=_normalize(m.group(sweep.article_group)),
                part=m.group(sweep.part_group) if sweep.part_group else None,
                point=(_normalize_point(m.group(sweep.point_group)) if sweep.point_group else None),
                code=sweep.code,
                quote=_quote_around(text, m.start(), m.end()),
                source_url=source_url,
                confidence=sweep.confidence,
                method=f"regex:article:{sweep.method}",
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
