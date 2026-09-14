"""Typed retrieval contracts: queries, hits, semantic documents, errors."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_RETRIEVAL_LIMIT = 1000


class RetrievalEntityType(StrEnum):
    PERSON = "person"
    EVENT = "event"


class RetrievalBackend(StrEnum):
    """How candidates were produced. Evaluation/debug information, not a fact."""

    STRUCTURED = "structured"
    LEXICAL = "lexical"
    DENSE = "dense"
    HYBRID = "hybrid"
    HYBRID_RERANKED = "hybrid_reranked"


class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Restrict retrieval to these entity ids (e.g. a structured candidate set).
    entity_ids: list[int] | None = Field(default=None, max_length=MAX_RETRIEVAL_LIMIT)


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    text: str = Field(min_length=1, max_length=2000)
    entity_type: RetrievalEntityType
    limit: int = Field(default=20, ge=1, le=MAX_RETRIEVAL_LIMIT)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class RetrievalHit(BaseModel):
    """A candidate entity. Holds no facts: load them from PostgreSQL.

    `score` is backend-specific (ts_rank, cosine similarity, RRF, cross-encoder
    logit) and is only comparable within one result; it is never a confidence
    that a domain fact holds.
    """

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    entity_type: RetrievalEntityType
    entity_id: int
    score: float
    backend: RetrievalBackend
    rank: int = Field(ge=1)
    # Per-backend ranks for fused/reranked hits, e.g. {"lexical": 3, "dense": 1}.
    component_ranks: dict[str, int] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    entity_type: RetrievalEntityType
    backend: RetrievalBackend
    hits: list[RetrievalHit] = Field(default_factory=list)

    @property
    def entity_ids(self) -> list[int]:
        return [hit.entity_id for hit in self.hits]


class SemanticDocument(BaseModel):
    """Deterministic text representation of one canonical entity."""

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    entity_type: RetrievalEntityType
    entity_id: int
    text: str
    representation_version: int
    # sha256 of version + text: equal hash means an equal embedding input.
    content_hash: str
    source_updated_at: datetime | None = None


class RetrievalError(Exception):
    """Retrieval could not run. Never the same as "no relevant entities"."""


class RetrievalUnavailableError(RetrievalError):
    """The vector store (or another retrieval dependency) is unreachable."""


class RetrievalNotConfiguredError(RetrievalError):
    """Semantic retrieval is needed but not configured (e.g. no QDRANT_URL)."""


class SemanticConfigurationError(RetrievalNotConfiguredError):
    """Semantic retrieval environment variables are invalid."""


class EmbeddingError(RetrievalError):
    """The embedding model failed (not installed, not found, OOM, bad output)."""


class VectorSizeMismatchError(EmbeddingError):
    """Embedding dimension differs from the collection's vector size."""


class RerankerError(RetrievalError):
    """The cross-encoder failed."""
