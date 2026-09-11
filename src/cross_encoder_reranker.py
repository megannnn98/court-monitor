from collections.abc import Sequence
from typing import Any, Protocol, Self, cast

import torch
from sentence_transformers import CrossEncoder

from models import SearchHit


class CrossEncoderModel(Protocol):
    def predict(
        self,
        inputs: list[tuple[str, str]],
        *,
        show_progress_bar: bool = False,
    ) -> Any: ...


class CrossEncoderReranker:
    def __init__(self, model: CrossEncoderModel) -> None:
        self._model = model

    @classmethod
    def from_model_id(cls, model_id: str) -> Self:
        device = "cuda" if torch.cuda.is_available() else "cpu"

        model = CrossEncoder(
            model_id,
            device=device,
        )

        return cls(cast(CrossEncoderModel, model))

    def rerank(
        self,
        query: str,
        candidates: Sequence[SearchHit],
        *,
        limit: int,
    ) -> list[SearchHit]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0")

        if not candidates:
            return []

        pairs = [(query, candidate.text) for candidate in candidates]

        raw_scores = self._model.predict(
            pairs,
            show_progress_bar=False,
        )

        scores = [float(score) for score in raw_scores]

        if len(scores) != len(candidates):
            raise RuntimeError(
                "cross-encoder returned a different number of scores than candidates"
            )

        scored_hits = [
            (
                position,
                candidate.model_copy(
                    update={"score": score},
                ),
            )
            for position, (candidate, score) in enumerate(zip(candidates, scores, strict=True))
        ]

        scored_hits.sort(
            key=lambda item: (
                -item[1].score,
                item[0],
            )
        )

        return [hit for _, hit in scored_hits[:limit]]
