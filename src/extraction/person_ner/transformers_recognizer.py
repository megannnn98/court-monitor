"""A token-classification model as the project's person-name recognizer.

`transformers` and `torch` are imported inside the constructor: the pipeline is typed
against the `PersonNameRecognizer` protocol, so unit tests run with a stub and neither
library nor any model download is needed to exercise the extraction path.
"""

from __future__ import annotations

import logging

from extraction.person_ner.config import PersonNerSettings
from extraction.person_ner.models import PersonNameSpan
from extraction.person_ner.spans import (
    TokenPrediction,
    deduplicate,
    merge_person_tokens,
    window_bounds,
)

logger = logging.getLogger("person_ner")

# Characters, not tokens: a window this size stays well under the 512-token limit of the
# encoders considered, so a window is never silently truncated.
DEFAULT_WINDOW_CHARS = 600
DEFAULT_STRIDE_CHARS = 150
DEFAULT_BATCH_SIZE = 8
DEFAULT_MIN_SCORE = 0.5


class TransformersPersonNameRecognizer:
    """Runs a token-classification model over an article and returns its person spans.

    The model is loaded once, in inference mode, on CUDA when it is available and on the
    CPU otherwise. Windows of one article are batched together.
    """

    def __init__(
        self,
        model_id: str,
        *,
        revision: str | None = None,
        device: str | None = None,
        min_score: float = DEFAULT_MIN_SCORE,
        window_chars: int = DEFAULT_WINDOW_CHARS,
        stride_chars: int = DEFAULT_STRIDE_CHARS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        self._torch = torch
        self._model_id = model_id
        self._revision = revision
        self._min_score = min_score
        self._window_chars = window_chars
        self._stride_chars = stride_chars
        self._batch_size = batch_size
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        self._tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
        model = AutoModelForTokenClassification.from_pretrained(model_id, revision=revision)
        self._model = model.to(self._device).eval()
        self._labels: dict[int, str] = dict(model.config.id2label)
        logger.info(
            "event=person_ner_loaded model=%s revision=%s device=%s labels=%d",
            model_id,
            revision or "-",
            self._device,
            len(self._labels),
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

        windows = window_bounds(len(text), window=self._window_chars, stride=self._stride_chars)
        spans: list[PersonNameSpan] = []
        for batch_start in range(0, len(windows), self._batch_size):
            batch = windows[batch_start : batch_start + self._batch_size]
            spans.extend(self._recognize_batch(text, batch))
        return deduplicate(spans)

    def _recognize_batch(self, text: str, windows: list[tuple[int, int]]) -> list[PersonNameSpan]:
        chunks = [text[start:end] for start, end in windows]
        encoded = self._tokenizer(
            chunks,
            return_offsets_mapping=True,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=self._tokenizer.model_max_length,
        )
        offset_mapping = encoded.pop("offset_mapping")
        special_tokens = encoded.pop("special_tokens_mask", None)
        inputs = {key: value.to(self._device) for key, value in encoded.items()}

        with self._torch.inference_mode():
            logits = self._model(**inputs).logits
        probabilities = self._torch.softmax(logits, dim=-1)
        scores, label_ids = probabilities.max(dim=-1)

        spans: list[PersonNameSpan] = []
        for index, (window_start, _) in enumerate(windows):
            predictions = self._window_predictions(
                offset_mapping[index].tolist(),
                label_ids[index].tolist(),
                scores[index].tolist(),
                None if special_tokens is None else special_tokens[index].tolist(),
            )
            spans.extend(
                merge_person_tokens(
                    chunks[index],
                    predictions,
                    min_score=self._min_score,
                    offset=window_start,
                )
            )
        return spans

    def _window_predictions(
        self,
        offsets: list[list[int]],
        label_ids: list[int],
        scores: list[float],
        special_tokens: list[int] | None,
    ) -> list[TokenPrediction]:
        """Token predictions of one window, in offsets of that window's text.

        Offsets come from the tokenizer, never from searching the decoded string back in
        the text: a repeated surname would otherwise be located at its first occurrence.
        """
        predictions: list[TokenPrediction] = []
        for position, (start, end) in enumerate(offsets):
            if end <= start:
                # Padding and special tokens carry an empty offset span.
                continue
            if special_tokens is not None and special_tokens[position]:
                continue
            predictions.append(
                TokenPrediction(
                    label=self._labels.get(label_ids[position], "O"),
                    score=float(scores[position]),
                    start=int(start),
                    end=int(end),
                )
            )
        return predictions


def build_person_recognizer(settings: PersonNerSettings) -> TransformersPersonNameRecognizer:
    """The configured recognizer; the model id lives in settings, not in the call sites."""
    return TransformersPersonNameRecognizer(
        settings.model_id,
        revision=settings.revision,
        device=settings.device,
        min_score=settings.min_score,
    )
