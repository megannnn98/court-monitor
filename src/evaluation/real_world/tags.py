"""Sampling tags: cheap text heuristics used only to stratify the evaluation sample.

Tags are not annotations and never a source of truth; they only help to pick
articles that are likely to contain the hard cases.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.models import EntityType, ExtractionDocument

_POLITICAL_MARKERS = (
    "иноагент",
    "экстремист",
    "террорис",
    "дискредитац",
    "фейк",
    "госизмен",
    "пикет",
    "митинг",
    "антивоен",
    "политзаключ",
    "свидетел",
    "лгбт",
    "навальн",
    "оправдани",
)
_HISTORICAL = re.compile(
    r"\b(ранее|в прошлом году|в (19|20)\d\d год|летом (19|20)\d\d|в (январе|феврале|марте|апреле|"
    r"мае|июне|июле|августе|сентябре|октябре|ноябре|декабре) (19|20)\d\d)",
    re.IGNORECASE,
)
_INITIALS = re.compile(
    r"\b[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.\s?)?[А-ЯЁ][а-яё]+|\b[А-ЯЁ][а-яё]+\s[А-ЯЁ]\.\s?[А-ЯЁ]\."
)
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")

_entity_extractor = RuleBasedEntityExtractor()
_event_extractor = RuleBasedEventExtractor()


def sampling_tags(title: str, text: str) -> list[str]:
    document = ExtractionDocument(
        article_id=0,
        title=title,
        text=text,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_name="sampling",
        source_url="https://sampling.invalid",
        content_hash="",
    )
    persons = [
        mention
        for mention in _entity_extractor.extract(document)
        if mention.entity_type is EntityType.PERSON
    ]
    surfaces = {mention.surface_text for mention in persons}
    tags: set[str] = set()
    tags.add("multi_person" if len(surfaces) >= 2 else "single_person" if surfaces else "no_person")
    if any("ё" in surface.lower() for surface in surfaces):
        tags.add("yo_letter")
    if any(re.search(r"[А-ЯЁ][а-яё]+-[А-ЯЁ][а-яё]+", surface) for surface in surfaces):
        tags.add("hyphenated_name")
    if _INITIALS.search(text):
        tags.add("initials")
    if len(_event_extractor.extract(document, [])) >= 2:
        tags.add("several_events")
    if _HISTORICAL.search(text):
        tags.add("historical_reference")
    lowered = f"{title}\n{text}".lower()
    tags.add(
        "political_keywords"
        if any(marker in lowered for marker in _POLITICAL_MARKERS)
        else "non_political"
    )
    if "суд" in lowered:
        tags.add("court")
    for match in _SENTENCE.finditer(text):
        start, end = match.span()
        inside = {m.surface_text for m in persons if start <= m.start_offset < end}
        if len(inside) >= 2:
            tags.add("shared_sentence")
            break
    return sorted(tags)
