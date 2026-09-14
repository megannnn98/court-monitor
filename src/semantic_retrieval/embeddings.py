"""Embedding port and the sentence-transformers implementation.

`sentence-transformers` (and torch) live in the optional `semantic` uv group
and are imported lazily, so structured research never loads a model.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from semantic_retrieval.models import (
    EmbeddingError,
    SemanticConfigurationError,
    VectorSizeMismatchError,
)

logger = logging.getLogger("semantic_retrieval")

DEFAULT_EMBEDDING_MODEL_ID = "intfloat/multilingual-e5-base"
type DeviceSetting = Literal["auto", "cpu", "cuda"]


class TextEmbedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


def parse_device(value: str | None) -> DeviceSetting:
    normalized = (value or "auto").strip().lower()
    if normalized not in ("auto", "cpu", "cuda"):
        raise SemanticConfigurationError(f"Unsupported device {value!r}: use auto, cpu or cuda")
    return cast(DeviceSetting, normalized)


def resolve_device(setting: DeviceSetting) -> str:
    try:
        import torch
    except ImportError as exc:
        raise EmbeddingError(
            "torch is not installed; install the semantic group: uv sync --group semantic"
        ) from exc
    if setting == "cuda" and not torch.cuda.is_available():
        raise EmbeddingError("EMBEDDING_DEVICE/RERANKER_DEVICE=cuda but CUDA is not available")
    if setting == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return setting


@dataclass(frozen=True)
class EmbeddingConfig:
    model_id: str = DEFAULT_EMBEDDING_MODEL_ID
    device: DeviceSetting = "auto"
    batch_size: int = 32

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EmbeddingConfig:
        env = os.environ if env is None else env
        try:
            batch_size = int(env.get("EMBEDDING_BATCH_SIZE") or 32)
        except ValueError as exc:
            raise SemanticConfigurationError("EMBEDDING_BATCH_SIZE must be an integer") from exc
        if batch_size <= 0:
            raise SemanticConfigurationError("EMBEDDING_BATCH_SIZE must be greater than 0")
        return cls(
            model_id=env.get("EMBEDDING_MODEL_ID") or DEFAULT_EMBEDDING_MODEL_ID,
            device=parse_device(env.get("EMBEDDING_DEVICE")),
            batch_size=batch_size,
        )


def uses_e5_prefixes(model_id: str) -> bool:
    """E5 models are trained with "query: " / "passage: " prefixes."""
    return "e5" in model_id.lower()


class SentenceTransformerEmbedder:
    def __init__(self, config: EmbeddingConfig) -> None:
        self._config = config
        self._model: Any = None
        self._dimension: int | None = None

    @property
    def model_id(self) -> str:
        return self._config.model_id

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError(
                "sentence-transformers is not installed; run: uv sync --group semantic"
            ) from exc
        device = resolve_device(self._config.device)
        try:
            self._model = SentenceTransformer(self._config.model_id, device=device)
        except (OSError, ValueError, RuntimeError) as exc:  # not found, bad config, CUDA init
            raise EmbeddingError(f"Cannot load embedding model {self._config.model_id}") from exc
        dimension = self._model.get_embedding_dimension()
        if not isinstance(dimension, int):
            raise EmbeddingError(f"Model {self._config.model_id} does not report a dimension")
        self._dimension = dimension
        logger.info(
            "embedding_model_loaded model=%s device=%s dimension=%d",
            self.model_id,
            device,
            dimension,
        )
        return self._model

    @property
    def dimension(self) -> int:
        self._load()
        assert self._dimension is not None
        return self._dimension

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        try:
            vectors = model.encode(
                texts,
                batch_size=self._config.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except RuntimeError as exc:  # includes torch.cuda.OutOfMemoryError
            raise EmbeddingError(f"Embedding failed ({type(exc).__name__})") from exc
        result = [[float(value) for value in vector] for vector in vectors]
        if len(result) != len(texts):
            raise EmbeddingError("Embedding model returned a different number of vectors")
        for vector in result:
            if len(vector) != self._dimension:
                raise VectorSizeMismatchError(
                    f"Embedding size {len(vector)} differs from model dimension {self._dimension}"
                )
            if not all(math.isfinite(value) for value in vector):
                raise EmbeddingError("Embedding contains non-finite values")
        return result

    def embed_query(self, text: str) -> list[float]:
        prefix = "query: " if uses_e5_prefixes(self.model_id) else ""
        return self._encode([f"{prefix}{text}"])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        prefix = "passage: " if uses_e5_prefixes(self.model_id) else ""
        return self._encode([f"{prefix}{text}" for text in texts])
