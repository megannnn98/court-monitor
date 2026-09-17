"""The GLiNER2 boundary extractor as the project's person-name recognizer.

`gliner2` and `torch` are imported in the constructor, so the rule-based extraction path
stays importable — and unit-testable with a stub — without them.

Chosen over a token-classification encoder on a comparison over 45 real corpus articles:
it made none of the rule extractor's semantic false positives (places, organizations,
slogans, channel names), kept cleaner name boundaries and read initials whole. See
`evaluation/person_ner/`.
"""

from __future__ import annotations

import logging
from typing import Any

from extraction.person_ner.config import PersonNerSettings
from extraction.person_ner.models import PersonNameSpan
from extraction.person_ner.spans import deduplicate

logger = logging.getLogger("person_ner")

PERSON_LABEL = "PERSON"


class GlinerPersonNameRecognizer:
    """Runs the recognizer model over an article and returns its person spans.

    The model is loaded once, in inference mode, on CUDA when it is available and on the
    CPU otherwise. It reads a whole article at a time, so no windowing is needed here.
    """

    def __init__(
        self,
        model_id: str,
        *,
        device: str | None = None,
        min_score: float = 0.5,
    ) -> None:
        import torch

        # `AutoExtractor`, not `GLiNER2`: the model is a boundary architecture and the
        # span-based loader fails on it.
        from gliner2 import AutoExtractor

        self._torch = torch
        self._model_id = model_id
        self._min_score = min_score
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        model = AutoExtractor.from_pretrained(model_id)
        move = getattr(model, "to", None)
        if callable(move):
            model = move(self._device)
        if hasattr(model, "eval"):
            model.eval()
        self._model = model
        logger.info(
            "event=person_ner_loaded model=%s device=%s min_score=%.2f",
            model_id,
            self._device,
            min_score,
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def device(self) -> str:
        return self._device

    def recognize(self, text: str) -> list[PersonNameSpan]:
        if not text.strip():
            return []

        with self._torch.inference_mode():
            result = self._model.extract_entities(
                text,
                [PERSON_LABEL],
                threshold=self._min_score,
                include_confidence=True,
                include_spans=True,
            )

        entities = result.get("entities", result)
        spans: list[PersonNameSpan] = []
        for entity in entities.get(PERSON_LABEL, entities.get(PERSON_LABEL.lower(), [])) or []:
            span = self._span(text, entity)
            if span is not None:
                spans.append(span)
        return deduplicate(spans)

    @staticmethod
    def _span(text: str, entity: Any) -> PersonNameSpan | None:
        """One entity as a span, or None when it carries no usable offsets.

        The surface form is read out of the text at those offsets rather than taken from
        the model's own string, so the span and the text can never disagree.
        """
        if not isinstance(entity, dict):
            return None
        start, end = entity.get("start"), entity.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            return None
        surface = text[start:end]
        if not surface.strip():
            return None
        return PersonNameSpan(
            start_offset=start,
            end_offset=end,
            surface_text=surface,
            confidence=round(float(entity.get("confidence", 1.0)), 4),
        )


def build_person_recognizer(settings: PersonNerSettings) -> GlinerPersonNameRecognizer:
    """The configured recognizer; the model id lives in settings, not in the call sites."""
    return GlinerPersonNameRecognizer(
        settings.model_id,
        device=settings.device,
        min_score=settings.min_score,
    )
