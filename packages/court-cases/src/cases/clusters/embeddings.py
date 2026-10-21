"""The optional e5 signal: how close the words of two publications are.

The signal only ranks the candidates: the similarity of the words is never enough on
its own to put two events in one cluster (the weight it adds is a small part of the
score, and the matching needs the person, the step and the day anyway).

The vectors are never written to the database. At most a cache file outside it keeps
the vectors of the quotes that were compared, so that a repeated run does not embed
them again. The signal is off by default: the e5 model is loaded only when it is
asked for.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("cases.clusters.embeddings")

DEFAULT_MODEL = "intfloat/multilingual-e5-base"
# The cache of the vectors, outside the database.
DEFAULT_CACHE = Path("var/cases/cluster_text_vectors.json")


class TextSimilarity:
    """The cosine of the e5 vectors of two quotes, by their hashes.

    `enabled` is false by default: nothing is embedded and every pair scores None.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        model_id: str = DEFAULT_MODEL,
        cache_path: Path = DEFAULT_CACHE,
        device: str = "auto",
    ) -> None:
        self.enabled = enabled
        self.model_id = model_id
        self.cache_path = cache_path
        self.device = device
        self._vectors: dict[str, list[float]] = {}
        self._embedder: Any = None
        if enabled:
            self._vectors = _read_cache(cache_path)
            self._embedder = self._load()

    def _load(self) -> Any:
        try:
            from processing.junk.embedder import (
                EmbeddingConfig,
                SentenceTransformerEmbedder,
                parse_device,
            )
        except ImportError as exc:  # the semantic extra is not installed
            raise RuntimeError(
                "the e5 signal needs the semantic extra: uv sync --group semantic"
            ) from exc
        config = EmbeddingConfig(
            model_id=self.model_id, device=parse_device(self.device), batch_size=64
        )
        return SentenceTransformerEmbedder(config)

    def vector(self, quote_hash: str, quote: str) -> list[float] | None:
        """The vector of a quote, from the cache or from the model."""
        if not self.enabled:
            return None
        found = self._vectors.get(quote_hash)
        if found is not None:
            return found
        embedded = self._embedder.embed_documents([quote])
        vector = [float(value) for value in embedded[0]]
        self._vectors[quote_hash] = vector
        return vector

    def __call__(self, left_hash: str, right_hash: str) -> float | None:
        """The cosine of the two vectors, or None when the signal is off."""
        if not self.enabled:
            return None
        left = self._vectors.get(left_hash)
        right = self._vectors.get(right_hash)
        if left is None or right is None:
            return None
        return _cosine(left, right)

    def save(self) -> None:
        """Write the cache outside the database."""
        if not self.enabled or not self._vectors:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self._vectors, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:  # a cache that cannot be written is not a failure
            logger.warning("the vector cache was not written: %s", exc)


def _read_cache(path: Path) -> dict[str, list[float]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): [float(value) for value in vector] for key, vector in payload.items()}


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    product: float = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm: float = sum(a * a for a in left) ** 0.5
    right_norm: float = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return product / (left_norm * right_norm)
