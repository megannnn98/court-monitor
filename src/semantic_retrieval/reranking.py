"""Reranker port and the cross-encoder implementation.

The cross-encoder scores (query, entity semantic document) pairs — never a
whole article.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from semantic_retrieval.embeddings import DeviceSetting, parse_device, resolve_device
from semantic_retrieval.models import (
    EmbeddingError,
    RerankerError,
    RetrievalBackend,
    RetrievalHit,
)

logger = logging.getLogger("semantic_retrieval")

DEFAULT_RERANKER_MODEL_ID = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


@dataclass(frozen=True)
class RetrievalCandidate:
    hit: RetrievalHit
    # The entity's semantic document text.
    text: str


class Reranker(Protocol):
    def rerank(
        self, query: str, candidates: Sequence[RetrievalCandidate], *, limit: int
    ) -> list[RetrievalHit]:
        """At most `limit` hits ordered by reranker score (ties keep input order).

        Returned hits have backend HYBRID_RERANKED, the reranker score and the
        new rank; the input rank is kept in `component_ranks["hybrid"]`.
        """
        ...


def order_by_scores(
    candidates: Sequence[RetrievalCandidate], scores: Sequence[float], *, limit: int
) -> list[RetrievalHit]:
    if limit <= 0:
        raise ValueError("limit must be greater than 0")
    if len(scores) != len(candidates):
        raise RerankerError("reranker returned a different number of scores than candidates")
    ordered = sorted(range(len(candidates)), key=lambda position: (-scores[position], position))[
        :limit
    ]
    return [
        candidates[position].hit.model_copy(
            update={
                "score": float(scores[position]),
                "backend": RetrievalBackend.HYBRID_RERANKED,
                "rank": rank,
                "component_ranks": {
                    **candidates[position].hit.component_ranks,
                    candidates[position].hit.backend.value: candidates[position].hit.rank,
                },
            }
        )
        for rank, position in enumerate(ordered, start=1)
    ]


@dataclass(frozen=True)
class RerankerConfig:
    model_id: str = DEFAULT_RERANKER_MODEL_ID
    device: DeviceSetting = "auto"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RerankerConfig:
        env = os.environ if env is None else env
        return cls(
            model_id=env.get("RERANKER_MODEL_ID") or DEFAULT_RERANKER_MODEL_ID,
            device=parse_device(env.get("RERANKER_DEVICE")),
        )


class CrossEncoderReranker:
    def __init__(self, config: RerankerConfig) -> None:
        self._config = config
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RerankerError(
                "sentence-transformers is not installed; run: uv sync --group semantic"
            ) from exc
        try:
            device = resolve_device(self._config.device)
        except EmbeddingError as exc:
            raise RerankerError(str(exc)) from exc
        try:
            self._model = CrossEncoder(self._config.model_id, device=device)
        except (OSError, ValueError, RuntimeError) as exc:  # not found, bad config, CUDA init
            raise RerankerError(f"Cannot load reranker model {self._config.model_id}") from exc
        logger.info("reranker_model_loaded model=%s device=%s", self._config.model_id, device)
        return self._model

    def rerank(
        self, query: str, candidates: Sequence[RetrievalCandidate], *, limit: int
    ) -> list[RetrievalHit]:
        if not candidates:
            return []
        model = self._load()
        try:
            raw = model.predict(
                [(query, candidate.text) for candidate in candidates], show_progress_bar=False
            )
        except RuntimeError as exc:
            raise RerankerError(f"Reranking failed ({type(exc).__name__})") from exc
        return order_by_scores(candidates, [float(score) for score in raw], limit=limit)
