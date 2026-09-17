from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple

from extraction.events import RuleBasedEventExtractor
from extraction.models import (
    ArticleExtractionResult,
    EntityExtractor,
    ExtractionDocument,
    ExtractionPersistence,
    ExtractionSaveResult,
    MentionNormalizer,
    NormalizedMention,
    RawMention,
)
from extraction.validation import (
    ExtractionValidationError,
    validate_event,
    validate_normalized_mention,
    validate_raw_mention,
)


class ExtractionVersions(NamedTuple):
    extractor_name: str
    extractor_version: str
    normalizer_version: str


class ExtractionPipeline:
    def __init__(
        self,
        *,
        extractors: list[EntityExtractor],
        normalizers: list[MentionNormalizer],
        event_extractor: RuleBasedEventExtractor,
        persistence: ExtractionPersistence,
    ) -> None:
        self._extractors = extractors
        self._normalizers = normalizers
        self._event_extractor = event_extractor
        self._persistence = persistence

    @property
    def versions(self) -> ExtractionVersions:
        """The identity an extraction run of this pipeline is stored under.

        The event extractor is part of it: the run stores the events too, so a change to
        the event rules must make the stored runs stale.
        """
        return ExtractionVersions(
            extractor_name="+".join(
                [
                    *(extractor.extractor_name for extractor in self._extractors),
                    self._event_extractor.extractor_name,
                ]
            ),
            extractor_version="+".join(
                [
                    *(extractor.extractor_version for extractor in self._extractors),
                    self._event_extractor.extractor_version,
                ]
            ),
            normalizer_version="+".join(
                sorted({normalizer.normalizer_version for normalizer in self._normalizers})
            ),
        )

    def run(self, document: ExtractionDocument) -> ExtractionSaveResult:
        extractor_name, extractor_version, normalizer_version = self.versions
        try:
            raw_mentions = []
            for extractor in self._extractors:
                raw_mentions.extend(extractor.extract(document))
            for mention in raw_mentions:
                validate_raw_mention(document, mention)
            normalized_mentions = self._normalize(document, raw_mentions)
            normalized_mentions = self._deduplicate(normalized_mentions)
            for normalized_mention in normalized_mentions:
                validate_normalized_mention(document, normalized_mention)
            events = self._event_extractor.extract(document, normalized_mentions)
            for event in events:
                validate_event(document, event, mention_count=len(normalized_mentions))
            result = ArticleExtractionResult(
                document=document,
                mentions=normalized_mentions,
                events=events,
                extractor_name=extractor_name,
                extractor_version=extractor_version,
                normalizer_version=normalizer_version,
            )
            return self._persistence.save(result)
        except (ExtractionValidationError, ValueError, re.error) as exc:
            return self._persistence.save_failed(
                document,
                extractor_name=extractor_name,
                extractor_version=extractor_version,
                normalizer_version=normalizer_version,
                error_message=str(exc),
            )

    def _normalize(
        self,
        document: ExtractionDocument,
        raw_mentions: Iterable[RawMention],
    ) -> list[NormalizedMention]:
        normalized: list[NormalizedMention] = []
        for mention in raw_mentions:
            normalizer = next(
                candidate
                for candidate in self._normalizers
                if candidate.supports(mention.entity_type)
            )
            normalized.append(normalizer.normalize(mention, document))
        return sorted(
            normalized,
            key=lambda mention: (
                mention.start_offset,
                mention.end_offset,
                mention.entity_type.value,
                mention.normalized_text,
            ),
        )

    @staticmethod
    def _deduplicate(mentions: Iterable[NormalizedMention]) -> list[NormalizedMention]:
        seen: set[tuple[str, int, int, str]] = set()
        result: list[NormalizedMention] = []
        for mention in mentions:
            key = (
                mention.entity_type.value,
                mention.start_offset,
                mention.end_offset,
                mention.surface_text,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(mention)
        return result
