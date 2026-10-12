"""The embedding model the junk screen scores articles with.

`sentence-transformers` (and torch) live in the optional `semantic` uv group and are
imported lazily, so nothing loads a model until the screen is turned on.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

logger = logging.getLogger("monitoring.embedder")

type DeviceSetting = Literal["auto", "cpu", "cuda"]


class EmbeddingConfigurationError(ValueError):
    """The embedding environment variables are invalid."""


class EmbeddingError(RuntimeError):
    """The embedding model failed (not installed, not found, OOM, bad output)."""


class TextEmbedder(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


def parse_device(value: str | None) -> DeviceSetting:
    normalized = (value or "auto").strip().lower()
    if normalized not in ("auto", "cpu", "cuda"):
        raise EmbeddingConfigurationError(f"Unsupported device {value!r}: use auto, cpu or cuda")
    return cast(DeviceSetting, normalized)


def resolve_device(setting: DeviceSetting) -> str:
    try:
        import torch
    except ImportError as exc:
        raise EmbeddingError(
            "torch is not installed; install the semantic group: uv sync --group semantic"
        ) from exc
    if setting == "cuda" and not torch.cuda.is_available():
        raise EmbeddingError("EMBEDDING_DEVICE=cuda but CUDA is not available")
    if setting == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return setting


@dataclass(frozen=True)
class EmbeddingConfig:
    model_id: str
    device: DeviceSetting = "auto"
    batch_size: int = 32

    @classmethod
    def from_env(cls, model_id: str, env: Mapping[str, str] | None = None) -> EmbeddingConfig:
        """The model is the screen's own; the environment picks only where and how fast."""
        env = os.environ if env is None else env
        try:
            batch_size = int(env.get("EMBEDDING_BATCH_SIZE") or 32)
        except ValueError as exc:
            raise EmbeddingConfigurationError("EMBEDDING_BATCH_SIZE must be an integer") from exc
        if batch_size <= 0:
            raise EmbeddingConfigurationError("EMBEDDING_BATCH_SIZE must be greater than 0")
        return cls(
            model_id=model_id,
            device=parse_device(env.get("EMBEDDING_DEVICE")),
            batch_size=batch_size,
        )


def document_prefix(model_id: str) -> str:
    """E5 models are trained with a "passage: " prefix on documents."""
    return "passage: " if "e5" in model_id.lower() else ""


class SentenceTransformerEmbedder:
    def __init__(self, config: EmbeddingConfig) -> None:
        self._config = config
        self._model: Any = None
        self._dimension: int | None = None
        # FastAPI runs sync endpoints in a thread pool: without the lock two
        # first requests would each load the model (double GPU memory).
        self._load_lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return self._config.model_id

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                self._model = self._load_locked()
        return self._model

    def _load_locked(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError(
                "sentence-transformers is not installed; run: uv sync --group semantic"
            ) from exc
        device = resolve_device(self._config.device)
        try:
            model = SentenceTransformer(self._config.model_id, device=device)
        except (OSError, ValueError, RuntimeError) as exc:  # not found, bad config, CUDA init
            raise EmbeddingError(f"Cannot load embedding model {self._config.model_id}") from exc
        dimension = model.get_embedding_dimension()
        if not isinstance(dimension, int):
            raise EmbeddingError(f"Model {self._config.model_id} does not report a dimension")
        # Published only after every check passed (see _load).
        self._dimension = dimension
        logger.info(
            "embedding_model_loaded model=%s device=%s dimension=%d",
            self.model_id,
            device,
            dimension,
        )
        return model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        prefix = document_prefix(self.model_id)
        try:
            vectors = model.encode(
                [f"{prefix}{text}" for text in texts],
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
                raise EmbeddingError(
                    f"Embedding size {len(vector)} differs from model dimension {self._dimension}"
                )
            if not all(math.isfinite(value) for value in vector):
                raise EmbeddingError("Embedding contains non-finite values")
        return result
