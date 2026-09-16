"""Building the entity extractor for the configured person-extraction strategy.

The one place that decides who owns person detection, so no call site has to know.
"""

from __future__ import annotations

import logging

from extraction.extractors import RuleBasedEntityExtractor
from extraction.name_morphology import NameMorphology
from extraction.person_ner.config import PersonExtractionStrategy, PersonNerSettings
from extraction.person_ner.models import PersonNameRecognizer

logger = logging.getLogger("person_ner")


def build_entity_extractor(
    settings: PersonNerSettings | None = None,
    *,
    morphology: NameMorphology | None = None,
    recognizer: PersonNameRecognizer | None = None,
) -> RuleBasedEntityExtractor:
    """The extractor for this configuration.

    `recognizer` is for tests and for reusing one loaded model across articles; without
    it the model is loaded here when the strategy asks for it.
    """
    settings = settings or PersonNerSettings.from_env()
    if settings.strategy is PersonExtractionStrategy.RULE_BASED:
        return RuleBasedEntityExtractor(morphology)

    if recognizer is None:
        # Imported here so the rule-based path never needs transformers or torch.
        from extraction.person_ner.gliner_recognizer import build_person_recognizer

        recognizer = build_person_recognizer(settings)

    logger.info(
        "event=person_extraction_strategy strategy=%s model=%s min_score=%.2f",
        settings.strategy.value,
        settings.model_id,
        settings.min_score,
    )
    return RuleBasedEntityExtractor(
        morphology,
        person_recognizer=recognizer,
        blend_single_word_names=settings.strategy is PersonExtractionStrategy.HYBRID,
    )
