"""OpenRouter embedding adapter: batching, retries, validation, and accounting."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from semantic_retrieval.embeddings import (
    EmbeddingSpend,
    OpenRouterEmbedder,
    OpenRouterEmbeddingConfig,
    SentenceTransformerEmbedder,
    create_text_embedder,
    embedding_profile,
)
from semantic_retrieval.models import EmbeddingError, SemanticConfigurationError


def _response(vectors: list[list[float]], *, cost: float | None = 0.0) -> dict[str, Any]:
    usage: dict[str, int | float] = {"prompt_tokens": 12, "total_tokens": 12}
    if cost is not None:
        usage["cost"] = cost
    return {
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ],
        "model": "intfloat/multilingual-e5-large",
        "object": "list",
        "usage": usage,
    }


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openrouter_batches_prefixed_inputs_normalizes_vectors_and_counts_cost() -> None:
    sent: list[dict[str, Any]] = []
    request_metadata: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        request_metadata.append(
            {
                "auth": request.headers.get("authorization"),
                "timeout": request.extensions["timeout"],
            }
        )
        count = len(sent[-1]["input"])
        vectors = [[3.0, 4.0] for _ in range(count)]
        return httpx.Response(200, json=_response(vectors, cost=0.001))

    spend = EmbeddingSpend(budget_usd=1.0)
    embedder = OpenRouterEmbedder(
        OpenRouterEmbeddingConfig(
            model_id="intfloat/multilingual-e5-large",
            api_key="secret",
            batch_size=2,
            dimension=2,
        ),
        http_client=_client(handle),
        spend=spend,
    )

    vectors = embedder.embed_documents(["один", "два", "три"])

    assert sent == [
        {
            "model": "intfloat/multilingual-e5-large",
            "input": ["passage: один", "passage: два"],
            "encoding_format": "float",
        },
        {
            "model": "intfloat/multilingual-e5-large",
            "input": ["passage: три"],
            "encoding_format": "float",
        },
    ]
    assert all(vector == pytest.approx([0.6, 0.8]) for vector in vectors)
    assert request_metadata[0] == {
        "auth": "Bearer secret",
        "timeout": {"connect": 90.0, "read": 90.0, "write": 90.0, "pool": 90.0},
    }
    assert (spend.calls, spend.prompt_tokens, spend.cost_usd) == (2, 24, pytest.approx(0.002))
    assert embedder.dimension == 2


def test_openrouter_query_prefix_is_only_used_by_e5() -> None:
    sent: list[list[str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body["input"])
        return httpx.Response(200, json=_response([[1.0, 0.0]]))

    e5 = OpenRouterEmbedder(
        OpenRouterEmbeddingConfig("intfloat/multilingual-e5-large", "key", dimension=2),
        http_client=_client(handle),
    )
    bge = OpenRouterEmbedder(
        OpenRouterEmbeddingConfig("baai/bge-m3", "key", dimension=2),
        http_client=_client(handle),
    )

    e5.embed_query("дело")
    bge.embed_query("дело")

    assert sent == [["query: дело"], ["дело"]]


def test_openrouter_retries_only_temporary_failures() -> None:
    attempts = 0
    delays: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"}, text="slow down")
        if attempts == 2:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json=_response([[1.0, 0.0]]))

    embedder = OpenRouterEmbedder(
        OpenRouterEmbeddingConfig("baai/bge-m3", "key", dimension=2, max_retries=2),
        http_client=_client(handle),
        sleep=delays.append,
    )

    assert embedder.embed_documents(["x"]) == [[1.0, 0.0]]
    assert attempts == 3
    assert delays == [0.25, 1.0]

    permanent_attempts = 0

    def reject(_request: httpx.Request) -> httpx.Response:
        nonlocal permanent_attempts
        permanent_attempts += 1
        return httpx.Response(401, text="bad key")

    rejected = OpenRouterEmbedder(
        OpenRouterEmbeddingConfig("baai/bge-m3", "key", dimension=2, max_retries=2),
        http_client=_client(reject),
        sleep=delays.append,
    )
    with pytest.raises(EmbeddingError, match="HTTP 401"):
        rejected.embed_documents(["x"])
    assert permanent_attempts == 1


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"data": []}, "different number"),
        (_response([[1.0]]), "differs from model dimension"),
        (_response([[float("nan"), 0.0]]), "non-finite"),
    ],
)
def test_openrouter_refuses_unusable_vectors(payload: dict[str, Any], message: str) -> None:
    client = _client(
        lambda _request: httpx.Response(
            200,
            content=json.dumps(payload, allow_nan=True).encode(),
            headers={"content-type": "application/json"},
        )
    )
    embedder = OpenRouterEmbedder(
        OpenRouterEmbeddingConfig("baai/bge-m3", "key", dimension=2),
        http_client=client,
    )

    with pytest.raises(EmbeddingError, match=message):
        embedder.embed_documents(["x"])


def test_embedding_factory_follows_provider_and_requires_openrouter_key() -> None:
    local = create_text_embedder({"EMBEDDING_MODEL_ID": "local/model"})
    remote = create_text_embedder(
        {
            "EMBEDDING_PROVIDER": "openrouter",
            "EMBEDDING_MODEL_ID": "baai/bge-m3",
            "OPENROUTER_API_KEY": "key",
        },
        http_client=_client(lambda _request: httpx.Response(500)),
    )

    assert isinstance(local, SentenceTransformerEmbedder)
    assert isinstance(remote, OpenRouterEmbedder)
    assert remote.model_id == "baai/bge-m3"
    with pytest.raises(SemanticConfigurationError, match="OPENROUTER_API_KEY"):
        create_text_embedder(
            {"EMBEDDING_PROVIDER": "openrouter", "EMBEDDING_MODEL_ID": "baai/bge-m3"}
        )
    with pytest.raises(SemanticConfigurationError, match="EMBEDDING_MODEL_ID"):
        create_text_embedder({"EMBEDDING_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "key"})
    with pytest.raises(SemanticConfigurationError, match="EMBEDDING_PROVIDER"):
        create_text_embedder({"EMBEDDING_PROVIDER": "unknown"})


def test_openrouter_profiles_have_explicit_dimensions() -> None:
    assert embedding_profile("intfloat/multilingual-e5-large").dimension == 1024
    assert embedding_profile("baai/bge-m3").dimension == 1024
    assert embedding_profile("qwen/qwen3-embedding-8b").dimension == 4096
    assert embedding_profile("openai/text-embedding-3-small").dimension == 1536


def test_openrouter_does_not_send_after_the_reported_budget_is_spent() -> None:
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response([[1.0, 0.0]], cost=0.01))

    spend = EmbeddingSpend(budget_usd=0.01)
    embedder = OpenRouterEmbedder(
        OpenRouterEmbeddingConfig("baai/bge-m3", "key", batch_size=1, dimension=2),
        http_client=_client(handle),
        spend=spend,
    )

    with pytest.raises(EmbeddingError, match="budget"):
        embedder.embed_documents(["first", "not sent"])
    assert calls == 1


def test_openrouter_config_does_not_reveal_the_key() -> None:
    config = OpenRouterEmbeddingConfig("baai/bge-m3", "very-secret")

    assert "very-secret" not in repr(config)


def test_openrouter_calculates_known_model_cost_when_provider_omits_it() -> None:
    spend = EmbeddingSpend()
    client = _client(lambda _request: httpx.Response(200, json=_response([[1.0, 0.0]], cost=None)))
    embedder = OpenRouterEmbedder(
        OpenRouterEmbeddingConfig("openai/text-embedding-3-small", "key", dimension=2),
        http_client=client,
        spend=spend,
    )

    embedder.embed_documents(["x"])

    assert spend.cost_usd == pytest.approx(12 * 0.02 / 1_000_000)
