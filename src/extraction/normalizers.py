from __future__ import annotations

import re

from extraction.models import (
    EntityType,
    ExtractionDocument,
    LegalReferenceNormalizedData,
    LocationNormalizedData,
    NormalizedMention,
    OrganizationNormalizedData,
    PersonNormalizedData,
    RawMention,
)
from extraction.name_morphology import NameMorphology

# 1.1.0: personal names are brought to the nominative case with a morphological
# dictionary instead of a suffix table («Ольгу Комлеву» → «Ольга Комлева»).
NORMALIZER_VERSION = "1.1.0"

_LEGAL_ARTICLE_PATTERN = re.compile(r"(?:ст\.|стать[еяи])\s*(\d+(?:\.\d+)*)", re.IGNORECASE)
_LEGAL_PART_PATTERN = re.compile(r"(?:ч\.|част[ьи])\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_LEGAL_CLAUSE_PATTERN = re.compile(r"(?:п\.|пункт)\s*[«\"]?([а-яa-z])[\"»]?", re.IGNORECASE)
_LEGAL_CODES = (
    ("Уголовного кодекса РФ", "УК РФ"),
    ("Уголовный кодекс РФ", "УК РФ"),
    ("УК РФ", "УК РФ"),
    ("КоАП РФ", "КоАП РФ"),
    ("УК", "УК РФ"),
    ("КоАП", "КоАП РФ"),
    # Spelled out without a country the code may be another state's (Telegram channels of
    # courts in occupied territories cite the Ukrainian code): not guessed. After the short
    # «УК» alias above, which only matches the OVD-Info abbreviation style.
    ("Уголовного кодекса", "УК"),
    ("Уголовный кодекс", "УК"),
)
_LOCATION_ALIASES = {
    "москве": "Москва",
    "москвы": "Москва",
    "казани": "Казань",
    "екатеринбурге": "Екатеринбург",
    "санкт-петербурге": "Санкт-Петербург",
    "новосибирске": "Новосибирск",
}


class RuleBasedMentionNormalizer:
    normalizer_version = NORMALIZER_VERSION

    def __init__(self, name_morphology: NameMorphology | None = None) -> None:
        self._name_morphology = name_morphology or NameMorphology()

    def supports(self, entity_type: EntityType) -> bool:
        return entity_type in set(EntityType)

    def normalize(
        self,
        mention: RawMention,
        document: ExtractionDocument,
    ) -> NormalizedMention:
        normalized_data: (
            PersonNormalizedData
            | LegalReferenceNormalizedData
            | OrganizationNormalizedData
            | LocationNormalizedData
        )
        if mention.entity_type is EntityType.PERSON:
            normalized_text, normalized_data = self._normalize_person(mention.surface_text)
        elif mention.entity_type is EntityType.LEGAL_REFERENCE:
            normalized_text, normalized_data = self._normalize_legal_reference(mention.surface_text)
        elif mention.entity_type is EntityType.COURT:
            normalized_text, normalized_data = self._normalize_organization(
                mention.surface_text,
                "court",
            )
        elif mention.entity_type is EntityType.ORGANIZATION:
            normalized_text, normalized_data = self._normalize_organization(
                mention.surface_text,
                self._organization_type(mention.surface_text),
            )
        else:
            normalized_text, normalized_data = self._normalize_location(mention.surface_text)

        return NormalizedMention(
            entity_type=mention.entity_type,
            surface_text=mention.surface_text,
            normalized_text=normalized_text,
            start_offset=mention.start_offset,
            end_offset=mention.end_offset,
            confidence=mention.confidence,
            normalized_data=normalized_data,
            extractor_name=mention.extractor_name,
            extractor_version=mention.extractor_version,
            normalizer_version=self.normalizer_version,
        )

    def normalize_person(self, surface_text: str) -> tuple[str, PersonNormalizedData]:
        """Person name as the pipeline stores it (used by ER v2 dry-run/evaluation)."""
        return self._normalize_person(surface_text)

    def _normalize_person(self, surface_text: str) -> tuple[str, PersonNormalizedData]:
        words = surface_text.replace("ё", "е").replace("Ё", "Е").split()
        if words and "." in words[0]:
            normalized = " ".join(words)
            matching_key = self._matching_key(normalized)
            return normalized, PersonNormalizedData(
                full_name=normalized,
                last_name=words[-1] if words else None,
                first_name=None,
                patronymic=None,
                matching_key=matching_key,
            )

        normalized_words = self._name_morphology.to_nominative(" ".join(words)).split()
        normalized = " ".join(normalized_words)
        first_name = normalized_words[0] if len(normalized_words) >= 2 else None
        last_name = normalized_words[1] if len(normalized_words) == 2 else normalized_words[0]
        patronymic = normalized_words[2] if len(normalized_words) >= 3 else None
        return normalized, PersonNormalizedData(
            full_name=normalized,
            last_name=last_name,
            first_name=first_name,
            patronymic=patronymic,
            matching_key=self._matching_key(normalized),
        )

    def _normalize_legal_reference(
        self,
        surface_text: str,
    ) -> tuple[str, LegalReferenceNormalizedData]:
        code = next(
            normalized
            for alias, normalized in _LEGAL_CODES
            if alias.lower() in surface_text.lower()
        )
        article_match = _LEGAL_ARTICLE_PATTERN.search(surface_text)
        part_match = _LEGAL_PART_PATTERN.search(surface_text)
        clause_match = _LEGAL_CLAUSE_PATTERN.search(surface_text)
        data = LegalReferenceNormalizedData(
            code=code,
            article=article_match.group(1) if article_match else None,
            part=part_match.group(1) if part_match else None,
            clause=clause_match.group(1).lower() if clause_match else None,
        )
        parts = [code]
        if data.article is not None:
            parts.append(f"ст. {data.article}")
        if data.part is not None:
            parts.append(f"ч. {data.part}")
        if data.clause is not None:
            parts.append(f"п. {data.clause}")
        return " ".join(parts), data

    def _normalize_organization(
        self,
        surface_text: str,
        organization_type: str,
    ) -> tuple[str, OrganizationNormalizedData]:
        name = " ".join(surface_text.split())
        data = OrganizationNormalizedData(
            name=name,
            organization_type=organization_type,
            location=self._extract_location_from_name(name),
            matching_key=self._matching_key(name),
        )
        return name, data

    def _normalize_location(self, surface_text: str) -> tuple[str, LocationNormalizedData]:
        lowered = surface_text.lower()
        name = _LOCATION_ALIASES.get(lowered, surface_text)
        data = LocationNormalizedData(
            name=name,
            location_type=None,
            matching_key=self._matching_key(name),
        )
        return name, data

    @staticmethod
    def _matching_key(value: str) -> str:
        return re.sub(r"[^а-яa-z0-9]+", "", value.lower().replace("ё", "е"))

    @staticmethod
    def _organization_type(surface_text: str) -> str:
        lowered = surface_text.lower()
        if lowered in {"мвд", "фсб", "фсин", "ск рф"} or "комитет" in lowered:
            return "state_body"
        if "полици" in lowered or "прокуратур" in lowered:
            return "law_enforcement"
        if surface_text in {"SOTA", "Медиазона"}:
            return "media"
        return "organization"

    @staticmethod
    def _extract_location_from_name(name: str) -> str | None:
        for marker in ("Москвы", "Москве", "Москва", "Петербурга", "Казани"):
            if marker in name:
                return _LOCATION_ALIASES.get(marker.lower(), marker)
        return None
