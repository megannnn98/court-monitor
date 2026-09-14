"""Semantic relevance acceptance: which retrieved candidates satisfy semantic_query.

Retrieval ranks candidates; it always returns the nearest neighbours, even for
a query unrelated to every entity. This policy decides separately whether a
candidate is similar enough to count as matching the semantic criterion.

Rule: accept a hit only if its dense cosine similarity (the hit's own score for
the dense backend, the dense component otherwise) reaches a threshold
calibrated for the embedding model. Lexical matches, RRF scores and reranker
scores influence ranking only; a hit without a dense score is never accepted.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict, computed_field

from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    SemanticConfigurationError,
)

logger = logging.getLogger("semantic_retrieval")

# Calibrated with `evaluate-retrieval` on the entity retrieval corpus: no
# accepted entity for the 14 off-topic calibration queries (highest similarity
# 0.789) plus a small margin, at relevant recall 0.40. A word-play negative added
# afterwards ("задержание кометы телескопом", 0.815) still leaks: a cosine
# threshold cannot reject shared-keyword nonsense without losing most recall.
# Only valid for this model: other models need SEMANTIC_DENSE_MIN_SCORE.
CALIBRATED_EMBEDDING_MODEL_ID = "intfloat/multilingual-e5-base"
DEFAULT_DENSE_MIN_SCORE = 0.80


class SemanticRetrievalDecision(BaseModel):
    """Both levels of semantic retrieval: what was found and what was accepted."""

    model_config = ConfigDict(use_enum_values=False)

    retrieved: RetrievalResult
    accepted: RetrievalResult
    dense_min_score: float
    embedding_model_id: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rejected_count(self) -> int:
        return len(self.retrieved.hits) - len(self.accepted.hits)


class SemanticRelevancePolicy(Protocol):
    def accept(
        self, query: RetrievalQuery, result: RetrievalResult
    ) -> SemanticRetrievalDecision: ...


def dense_similarity(hit: RetrievalHit) -> float | None:
    """The hit's cosine similarity to the query, or None if dense did not find it."""
    if hit.backend is RetrievalBackend.DENSE:
        return hit.score
    return hit.component_scores.get(RetrievalBackend.DENSE.value)


class DenseSimilarityRelevancePolicy:
    def __init__(self, *, dense_min_score: float, embedding_model_id: str | None = None) -> None:
        self._dense_min_score = dense_min_score
        self._embedding_model_id = embedding_model_id

    def accept(self, query: RetrievalQuery, result: RetrievalResult) -> SemanticRetrievalDecision:
        accepted_hits: list[RetrievalHit] = []
        for hit in result.hits:
            similarity = dense_similarity(hit)
            if similarity is None or similarity < self._dense_min_score:
                continue
            accepted_hits.append(
                hit.model_copy(
                    update={
                        "rank": len(accepted_hits) + 1,
                        "component_ranks": {**hit.component_ranks, hit.backend.value: hit.rank},
                    }
                )
            )
        decision = SemanticRetrievalDecision(
            retrieved=result,
            accepted=RetrievalResult(
                entity_type=result.entity_type, backend=result.backend, hits=accepted_hits
            ),
            dense_min_score=self._dense_min_score,
            embedding_model_id=self._embedding_model_id,
        )
        logger.info(
            "semantic_acceptance retrieved=%d accepted=%d rejected=%d dense_min_score=%.3f",
            len(result.hits),
            len(accepted_hits),
            decision.rejected_count,
            self._dense_min_score,
        )
        return decision


def resolve_dense_min_score(env: Mapping[str, str], embedding_model_id: str) -> float:
    """SEMANTIC_DENSE_MIN_SCORE, or the calibrated default for the calibrated model.

    A different embedding model has a different similarity distribution, so its
    threshold must be set explicitly (fail closed).
    """
    raw = env.get("SEMANTIC_DENSE_MIN_SCORE")
    if not raw:
        if embedding_model_id != CALIBRATED_EMBEDDING_MODEL_ID:
            raise SemanticConfigurationError(
                f"SEMANTIC_DENSE_MIN_SCORE must be set for EMBEDDING_MODEL_ID={embedding_model_id}: "
                f"the default {DEFAULT_DENSE_MIN_SCORE} is calibrated only for "
                f"{CALIBRATED_EMBEDDING_MODEL_ID} (calibrate with evaluate-retrieval)"
            )
        return DEFAULT_DENSE_MIN_SCORE
    try:
        value = float(raw)
    except ValueError as exc:
        raise SemanticConfigurationError("SEMANTIC_DENSE_MIN_SCORE must be a number") from exc
    if not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise SemanticConfigurationError("SEMANTIC_DENSE_MIN_SCORE must be between -1 and 1")
    return value
