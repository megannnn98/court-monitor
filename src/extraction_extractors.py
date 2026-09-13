from __future__ import annotations

import re
from collections.abc import Iterable

from extraction_models import EntityType, ExtractionDocument, RawMention

_LEGAL_CODE = r"(?:УК\s+РФ|КоАП\s+РФ|Уголовного\s+кодекса\s+РФ|Уголовный\s+кодекс\s+РФ)"
_LEGAL_REFERENCE_PATTERN = re.compile(
    rf"""
    (?:
        (?:(?:п\.|пункт)\s*[«"]?(?P<clause>[а-яa-z])["»]?\s*)?
        (?:(?:ч\.|част[ьи])\s*(?P<part>\d+(?:\.\d+)?)\s*)?
        (?:(?:ст\.|стать[еяи])\s*(?P<article>\d+(?:\.\d+)?)\s*)?
        (?P<code>{_LEGAL_CODE})
    )
    |
    (?:
        (?:(?:п\.|пункт)\s*[«"]?(?P<clause2>[а-яa-z])["»]?\s*)?
        (?:(?:ч\.|част[ьи])\s*(?P<part2>\d+(?:\.\d+)?)\s*)?
        (?:ст\.|стать[еяи])\s*(?P<article2>\d+(?:\.\d+)?)(?:\s*и\s*\d+(?:\.\d+)?)?\s*
        (?P<code2>{_LEGAL_CODE})
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CAPITALIZED_WORD = r"(?:[А-ЯЁA-Z][а-яёa-z]+(?:-[А-ЯЁA-Z][а-яёa-z]+)?)"
_COURT_PREFIX_WORD = r"(?:[А-ЯЁA-Z][а-яёa-z]+|[а-яё]+)"
_INITIALS = r"(?:[А-ЯЁA-Z]\.\s*){1,2}"
_PERSON_PATTERNS = [
    re.compile(rf"\b{_CAPITALIZED_WORD}\s+{_CAPITALIZED_WORD}\s+{_CAPITALIZED_WORD}\b"),
    re.compile(rf"\b{_CAPITALIZED_WORD}\s+{_CAPITALIZED_WORD}\b"),
    re.compile(rf"\b{_INITIALS}{_CAPITALIZED_WORD}\b"),
]
_PERSON_STOP_WORDS = {
    "Басманный районный",
    "Московский городской",
    "Следственный комитет",
    "Уголовного кодекса",
    "Верховный суд",
    "Европейский суд",
}

_COURT_PATTERN = re.compile(
    rf"\b(?:{_COURT_PREFIX_WORD}\s+){{0,4}}(?:суд|суда|суде|судом)"
    rf"(?:\s+(?:Москвы|Петербурга|России|Татарстана|{_CAPITALIZED_WORD}))?",
)
_ORG_PATTERN = re.compile(
    r"\b(?:ОВД-Инфо|SOTA|Медиазона|Роскомсвобода|Мемориал|Следственный комитет|"
    r"СК РФ|МВД|ФСБ|ФСИН|прокуратура|Генпрокуратура|Минюст|полиция|"
    r"Комитет против пыток|Росфинмониторинг)\b",
    re.IGNORECASE,
)
_LOCATION_PATTERN = re.compile(
    r"\b(?:Россия|Беларусь|Украина|Москва|Москве|Москвы|Санкт-Петербург|Петербург|"
    r"Санкт-Петербурге|Казань|Казани|Екатеринбург|Екатеринбурге|Новосибирск|Новосибирске|"
    r"Татарстан|Дагестан|Чечня|"
    r"Краснодарский край|Московская область|Ленинградская область)\b"
)


class RuleBasedEntityExtractor:
    extractor_name = "rule-based-entity-extractor"
    extractor_version = "1.0.0"

    def extract(self, document: ExtractionDocument) -> list[RawMention]:
        mentions: list[RawMention] = []
        mentions.extend(self._legal_references(document.text))
        mentions.extend(self._courts(document.text))
        mentions.extend(self._organizations(document.text))
        mentions.extend(self._locations(document.text))
        occupied = [(mention.start_offset, mention.end_offset) for mention in mentions]
        mentions.extend(self._people(document.text, occupied))
        return sorted(
            self._deduplicate(mentions),
            key=lambda mention: (
                mention.start_offset,
                mention.end_offset,
                mention.entity_type.value,
            ),
        )

    def _legal_references(self, text: str) -> list[RawMention]:
        return [
            self._mention(EntityType.LEGAL_REFERENCE, text, match.start(), match.end(), 0.95)
            for match in _LEGAL_REFERENCE_PATTERN.finditer(text)
            if match.group(0).strip()
        ]

    def _courts(self, text: str) -> list[RawMention]:
        return [
            self._mention(EntityType.COURT, text, match.start(), match.end(), 0.88)
            for match in _COURT_PATTERN.finditer(text)
            if self._is_specific_court(match.group(0))
        ]

    def _organizations(self, text: str) -> list[RawMention]:
        return [
            self._mention(EntityType.ORGANIZATION, text, match.start(), match.end(), 0.82)
            for match in _ORG_PATTERN.finditer(text)
        ]

    def _locations(self, text: str) -> list[RawMention]:
        return [
            self._mention(EntityType.LOCATION, text, match.start(), match.end(), 0.78)
            for match in _LOCATION_PATTERN.finditer(text)
        ]

    def _people(
        self,
        text: str,
        occupied: Iterable[tuple[int, int]],
    ) -> list[RawMention]:
        occupied_spans = list(occupied)
        mentions: list[RawMention] = []
        for pattern in _PERSON_PATTERNS:
            for match in pattern.finditer(text):
                surface = match.group(0)
                if self._is_person_stop_word(surface):
                    continue
                if self._overlaps(match.start(), match.end(), occupied_spans):
                    continue
                mentions.append(
                    self._mention(EntityType.PERSON, text, match.start(), match.end(), 0.72)
                )
        return mentions

    @staticmethod
    def _is_person_stop_word(surface: str) -> bool:
        if surface in _PERSON_STOP_WORDS:
            return True
        lowered = surface.lower()
        return any(
            marker in lowered for marker in ("суд", "кодекс", "область", "край", "республика")
        )

    @staticmethod
    def _is_specific_court(surface: str) -> bool:
        lowered = surface.lower()
        if "суд" not in lowered:
            return False
        if lowered in {"суд", "позже суд"}:
            return False
        if lowered.endswith(" суд") and not any(
            marker in lowered
            for marker in ("районный", "городской", "областной", "верховный", "басманный")
        ):
            return False
        return not (lowered.startswith("в ") and lowered.endswith(" суд"))

    @staticmethod
    def _overlaps(
        start: int,
        end: int,
        spans: Iterable[tuple[int, int]],
    ) -> bool:
        return any(start < span_end and end > span_start for span_start, span_end in spans)

    def _mention(
        self,
        entity_type: EntityType,
        text: str,
        start: int,
        end: int,
        confidence: float,
    ) -> RawMention:
        return RawMention(
            entity_type=entity_type,
            surface_text=text[start:end],
            start_offset=start,
            end_offset=end,
            confidence=confidence,
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
        )

    @staticmethod
    def _deduplicate(mentions: Iterable[RawMention]) -> list[RawMention]:
        seen: set[tuple[EntityType, int, int, str]] = set()
        result: list[RawMention] = []
        for mention in mentions:
            key = (
                mention.entity_type,
                mention.start_offset,
                mention.end_offset,
                mention.surface_text,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(mention)
        return result
