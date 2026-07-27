"""Relevance filtering against configured articles/keywords — spec §5.

A material is relevant if it matches ANY monitored article OR keyword. Absence
of an explicit article does NOT discard a material that matches a keyword.
"""

from __future__ import annotations

from dataclasses import dataclass

from court_monitor.config.loader import MonitoringConfig
from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import VerificationStatus
from court_monitor.extraction.articles import extract_articles


@dataclass(frozen=True)
class Relevance:
    relevant: bool
    matched_articles: list[str]
    matched_keywords: list[str]


def evaluate_relevance(
    text: str, monitoring: MonitoringConfig, *, source_url: str | None = None
) -> Relevance:
    if not text:
        return Relevance(False, [], [])

    found_articles = {str(dto.value) for dto in extract_articles(text, source_url=source_url)}
    monitored = monitoring.article_set()
    matched_articles = sorted(a for a in found_articles if _article_in(a, monitored))

    lowered = text.lower()
    matched_keywords = sorted(kw for kw in monitoring.keyword_set() if kw and kw in lowered)

    return Relevance(
        relevant=bool(matched_articles or matched_keywords),
        matched_articles=matched_articles,
        matched_keywords=matched_keywords,
    )


def relevance_as_facts(rel: Relevance, source_url: str | None) -> list[ExtractedFactDTO]:
    out: list[ExtractedFactDTO] = []
    for art in rel.matched_articles:
        out.append(
            ExtractedFactDTO(
                entity="document",
                field="matched_article",
                value=art,
                verification_status=VerificationStatus.inferred,
                confidence=0.8,
                source_url=source_url,
                extraction_method="filter:article",
            )
        )
    for kw in rel.matched_keywords:
        out.append(
            ExtractedFactDTO(
                entity="document",
                field="matched_keyword",
                value=kw,
                verification_status=VerificationStatus.unverified,
                confidence=0.5,
                source_url=source_url,
                extraction_method="filter:keyword",
            )
        )
    return out


def _article_in(found: str, monitored: set[str]) -> bool:
    if found in monitored:
        return True
    # 205.1 should match a monitored "205" (parent article).
    return any(found == p or found.startswith(p + ".") for p in monitored)
