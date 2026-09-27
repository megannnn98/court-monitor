"""Embedding port and the sentence-transformers implementation.

`sentence-transformers` (and torch) live in the optional `semantic` uv group
and are imported lazily, so structured research never loads a model.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

import httpx

from semantic_retrieval.models import (
    EmbeddingError,
    SemanticConfigurationError,
    VectorSizeMismatchError,
)

logger = logging.getLogger("semantic_retrieval")

DEFAULT_EMBEDDING_MODEL_ID = "intfloat/multilingual-e5-base"
OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
# OpenRouter catalog prices on 2026-09-27. Embedding responses always carry token usage,
# but may omit `usage.cost`; these four prices keep the requested evaluation accountable.
OPENROUTER_USD_PER_MILLION_TOKENS: Mapping[str, float] = {
    "intfloat/multilingual-e5-large": 0.01,
    "baai/bge-m3": 0.01,
    "qwen/qwen3-embedding-8b": 0.01,
    "openai/text-embedding-3-small": 0.02,
}
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


@dataclass(frozen=True)
class EmbeddingProfile:
    """How a model wants its input: what it was trained with, not a guess from its name."""

    query_prefix: str
    document_prefix: str
    # None: the model's own maximum sequence length.
    max_seq_length: int | None
    # Needed before an API-backed model has produced its first vector.
    dimension: int | None = None


KNOWN_EMBEDDING_PROFILES: Mapping[str, EmbeddingProfile] = {
    "intfloat/multilingual-e5-base": EmbeddingProfile("query: ", "passage: ", None, 768),
    "intfloat/multilingual-e5-large": EmbeddingProfile("query: ", "passage: ", None, 1024),
    # BGE-M3's dense embedding needs no instruction on either side. Its window is 8192
    # tokens; cut to E5's 512 so a comparison feeds both models the same document text.
    "baai/bge-m3": EmbeddingProfile("", "", 512, 1024),
    "qwen/qwen3-embedding-8b": EmbeddingProfile("", "", None, 4096),
    "openai/text-embedding-3-small": EmbeddingProfile("", "", None, 1536),
}


def embedding_profile(model_id: str) -> EmbeddingProfile:
    known = KNOWN_EMBEDDING_PROFILES.get(model_id.lower())
    if known is not None:
        return known
    # Unknown models keep the earlier rule.
    prefixes = ("query: ", "passage: ") if uses_e5_prefixes(model_id) else ("", "")
    return EmbeddingProfile(*prefixes, None)


@dataclass(frozen=True)
class OpenRouterEmbeddingConfig:
    model_id: str
    api_key: str = field(repr=False)
    batch_size: int = 32
    dimension: int | None = None
    timeout_seconds: float = 90.0
    max_retries: int = 2

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise SemanticConfigurationError("EMBEDDING_MODEL_ID must not be empty")
        if not self.api_key.strip():
            raise SemanticConfigurationError(
                "OPENROUTER_API_KEY is required when EMBEDDING_PROVIDER=openrouter"
            )
        if self.batch_size <= 0:
            raise SemanticConfigurationError("EMBEDDING_BATCH_SIZE must be greater than 0")
        if self.dimension is not None and self.dimension <= 0:
            raise SemanticConfigurationError("The embedding dimension must be greater than 0")
        if self.timeout_seconds <= 0:
            raise SemanticConfigurationError("EMBEDDING_TIMEOUT_SECONDS must be greater than 0")
        if self.max_retries < 0:
            raise SemanticConfigurationError("EMBEDDING_MAX_RETRIES must not be negative")

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        model_id: str | None = None,
    ) -> OpenRouterEmbeddingConfig:
        try:
            batch_size = int(env.get("EMBEDDING_BATCH_SIZE") or 32)
            timeout_seconds = float(env.get("EMBEDDING_TIMEOUT_SECONDS") or 90)
            max_retries = int(env.get("EMBEDDING_MAX_RETRIES") or 2)
        except ValueError as exc:
            raise SemanticConfigurationError(
                "Embedding batch size, timeout, and retry settings must be numbers"
            ) from exc
        chosen_model = (model_id or env.get("EMBEDDING_MODEL_ID") or "").strip()
        return cls(
            model_id=chosen_model,
            api_key=env.get("OPENROUTER_API_KEY", "").strip(),
            batch_size=batch_size,
            dimension=embedding_profile(chosen_model).dimension,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )


class EmbeddingSpend:
    """Successful OpenRouter embedding calls and their reported usage."""

    def __init__(self, budget_usd: float | None = None) -> None:
        self.budget_usd = budget_usd
        self._cost_usd = 0.0
        self._calls = 0
        self._prompt_tokens = 0
        self._lock = threading.Lock()

    def add(self, *, cost_usd: float, prompt_tokens: int) -> None:
        with self._lock:
            self._cost_usd += cost_usd
            self._prompt_tokens += prompt_tokens
            self._calls += 1

    @property
    def cost_usd(self) -> float:
        with self._lock:
            return self._cost_usd

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls

    @property
    def prompt_tokens(self) -> int:
        with self._lock:
            return self._prompt_tokens

    def exhausted(self) -> bool:
        return self.budget_usd is not None and self.cost_usd >= self.budget_usd


class OpenRouterEmbedder:
    """Normalized text embeddings from OpenRouter's OpenAI-compatible endpoint."""

    def __init__(
        self,
        config: OpenRouterEmbeddingConfig,
        *,
        http_client: httpx.Client | None = None,
        spend: EmbeddingSpend | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._http_client = http_client or httpx.Client()
        self._spend = spend or EmbeddingSpend()
        self._sleep = sleep
        self._dimension = config.dimension
        self._dimension_lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return self._config.model_id

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise EmbeddingError(
                f"Embedding dimension for {self.model_id} is unknown until its first response"
            )
        return self._dimension

    def embed_query(self, text: str) -> list[float]:
        prefix = embedding_profile(self.model_id).query_prefix
        return self._encode([f"{prefix}{text}"])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        prefix = embedding_profile(self.model_id).document_prefix
        return self._encode([f"{prefix}{text}" for text in texts])

    def _encode(self, prepared: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(prepared), self._config.batch_size):
            vectors.extend(self._request(prepared[start : start + self._config.batch_size]))
        return vectors

    def _request(self, texts: list[str]) -> list[list[float]]:
        if self._spend.exhausted():
            assert self._spend.budget_usd is not None
            raise EmbeddingError(
                f"OpenRouter embedding budget of ${self._spend.budget_usd:.2f} is spent"
            )
        response: httpx.Response | None = None
        last_transport_error: httpx.TransportError | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                response = self._http_client.post(
                    OPENROUTER_EMBEDDINGS_URL,
                    json={
                        "model": self.model_id,
                        "input": texts,
                        "encoding_format": "float",
                    },
                    headers={"Authorization": f"Bearer {self._config.api_key}"},
                    timeout=self._config.timeout_seconds,
                )
            except httpx.TransportError as exc:
                last_transport_error = exc
                if attempt == self._config.max_retries:
                    raise EmbeddingError(
                        f"OpenRouter embedding failed: {type(exc).__name__}"
                    ) from exc
                self._sleep(0.5 * (2**attempt))
                continue
            if response.status_code != 429 and response.status_code < 500:
                break
            if attempt == self._config.max_retries:
                break
            self._sleep(self._retry_delay(response, attempt))
        if response is None:
            assert last_transport_error is not None
            raise EmbeddingError("OpenRouter embedding failed") from last_transport_error
        if response.status_code != 200:
            raise EmbeddingError(f"OpenRouter embedding HTTP {response.status_code}")
        return self._parse_response(response, len(texts))

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return 0.5 * float(2**attempt)

    def _parse_response(self, response: httpx.Response, expected: int) -> list[list[float]]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise EmbeddingError("OpenRouter returned an unusable embedding response") from exc
        self._record_usage(payload, inputs=expected)
        try:
            data = payload["data"]
            if len(data) != expected:
                raise EmbeddingError("Embedding model returned a different number of vectors")
            ordered = sorted(data, key=lambda item: item["index"])
            if [item["index"] for item in ordered] != list(range(expected)):
                raise ValueError("wrong embedding indexes")
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("OpenRouter returned an unusable embedding response") from exc
        normalized = [self._normalized(vector) for vector in vectors]

        return normalized

    def _record_usage(self, payload: Any, *, inputs: int) -> None:
        if not isinstance(payload, dict):
            raise EmbeddingError("OpenRouter returned an unusable embedding response")
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            raise EmbeddingError("OpenRouter returned unusable embedding usage")
        try:
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            reported_cost = usage.get("cost")
            if reported_cost is None:
                price = OPENROUTER_USD_PER_MILLION_TOKENS.get(self.model_id.lower(), 0.0)
                cost_usd = prompt_tokens * price / 1_000_000
            else:
                cost_usd = float(reported_cost)
        except (TypeError, ValueError) as exc:
            raise EmbeddingError("OpenRouter returned unusable embedding usage") from exc
        self._spend.add(cost_usd=cost_usd, prompt_tokens=prompt_tokens)
        logger.info(
            "embedding_api_call provider=openrouter model=%s inputs=%d prompt_tokens=%d "
            "cost_usd=%.6f",
            self.model_id,
            inputs,
            prompt_tokens,
            cost_usd,
        )

    def _normalized(self, vector: list[float]) -> list[float]:
        dimension = len(vector)
        with self._dimension_lock:
            if self._dimension is None:
                self._dimension = dimension
            expected_dimension = self._dimension
        if dimension != expected_dimension:
            raise VectorSizeMismatchError(
                f"Embedding size {dimension} differs from model dimension {expected_dimension}"
            )
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingError("Embedding contains non-finite values")
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise EmbeddingError("Embedding has zero norm")
        return [value / norm for value in vector]


def create_text_embedder(
    env: Mapping[str, str] | None = None,
    *,
    model_id: str | None = None,
    http_client: httpx.Client | None = None,
    spend: EmbeddingSpend | None = None,
) -> TextEmbedder:
    """Build the configured local or OpenRouter embedding adapter."""
    env = os.environ if env is None else env
    provider = (env.get("EMBEDDING_PROVIDER") or "local").strip().lower()
    if provider == "local":
        config = EmbeddingConfig.from_env(env)
        if model_id is not None:
            config = EmbeddingConfig(model_id, config.device, config.batch_size)
        return SentenceTransformerEmbedder(config)
    if provider == "openrouter":
        return OpenRouterEmbedder(
            OpenRouterEmbeddingConfig.from_env(env, model_id=model_id),
            http_client=http_client,
            spend=spend,
        )
    raise SemanticConfigurationError(
        f"Unsupported EMBEDDING_PROVIDER {provider!r}: use local or openrouter"
    )


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
        max_seq_length = embedding_profile(self._config.model_id).max_seq_length
        if max_seq_length is not None:
            model.max_seq_length = max_seq_length
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
        prefix = embedding_profile(self.model_id).query_prefix
        return self._encode([f"{prefix}{text}"])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        prefix = embedding_profile(self.model_id).document_prefix
        return self._encode([f"{prefix}{text}" for text in texts])
