"""Embedder/reranker adapters and configuration without real models."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import types
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy.orm import sessionmaker
from support.semantic_fakes import StaticRetriever

from semantic_retrieval.cli import (
    SEMANTIC_EXIT_UNAVAILABLE,
    format_search_result,
    run_semantic_command,
)
from semantic_retrieval.embeddings import (
    EmbeddingConfig,
    SentenceTransformerEmbedder,
    parse_device,
    uses_e5_prefixes,
)
from semantic_retrieval.factory import (
    SemanticRetrievalConfig,
    create_configured_vector_store,
    create_semantic_components,
)
from semantic_retrieval.models import (
    EmbeddingError,
    RerankerError,
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalQuery,
    SemanticConfigurationError,
    VectorSizeMismatchError,
)
from semantic_retrieval.pgvector_store import PgVectorStore
from semantic_retrieval.reranking import CrossEncoderReranker, RerankerConfig, RetrievalCandidate
from semantic_retrieval.vector_store import QdrantVectorStore


class _FakeSentenceTransformer:
    instances: ClassVar[list[_FakeSentenceTransformer]] = []

    def __init__(self, model_id: str, device: str) -> None:
        self.model_id = model_id
        self.device = device
        self.encoded: list[list[str]] = []
        self.output: list[list[float]] | None = None
        self.error: Exception | None = None
        _FakeSentenceTransformer.instances.append(self)

    def get_embedding_dimension(self) -> int:
        return 3

    def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.encoded.append(texts)
        if self.error is not None:
            raise self.error
        return self.output if self.output is not None else [[1.0, 0.0, 0.0] for _ in texts]


class _FakeCrossEncoder:
    def __init__(self, model_id: str, device: str) -> None:
        self.pairs: list[tuple[str, str]] = []

    def predict(self, pairs: Sequence[tuple[str, str]], **kwargs: Any) -> list[float]:
        self.pairs = list(pairs)
        return [float(len(text)) for _, text in pairs]


@pytest.fixture
def fake_models(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = _FakeSentenceTransformer  # type: ignore[attr-defined]
    module.CrossEncoder = _FakeCrossEncoder  # type: ignore[attr-defined]
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    monkeypatch.setitem(sys.modules, "torch", torch)
    _FakeSentenceTransformer.instances.clear()
    yield


def test_device_setting_is_validated() -> None:
    assert [parse_device(v) for v in (None, "AUTO", "cpu", "cuda")] == [
        "auto",
        "auto",
        "cpu",
        "cuda",
    ]
    with pytest.raises(SemanticConfigurationError):
        parse_device("gpu")


def test_e5_models_get_query_and_passage_prefixes() -> None:
    assert uses_e5_prefixes("intfloat/multilingual-e5-base")
    assert not uses_e5_prefixes("sentence-transformers/LaBSE")


def test_embedding_config_from_env_defaults() -> None:
    assert EmbeddingConfig.from_env({}) == EmbeddingConfig(
        model_id="intfloat/multilingual-e5-base", device="auto", batch_size=32
    )
    assert EmbeddingConfig.from_env({"EMBEDDING_DEVICE": "cpu"}).device == "cpu"


def test_embedder_is_lazy_and_never_mixes_query_and_passage_encoding(fake_models: None) -> None:
    embedder = SentenceTransformerEmbedder(EmbeddingConfig(device="auto"))
    assert _FakeSentenceTransformer.instances == []  # constructing loads nothing

    embedder.embed_query("антивоенная позиция")
    embedder.embed_documents(["Персона: Иван.", "Персона: Анна."])

    (model,) = _FakeSentenceTransformer.instances
    assert model.device == "cpu"  # auto without CUDA
    assert model.encoded == [
        ["query: антивоенная позиция"],
        ["passage: Персона: Иван.", "passage: Персона: Анна."],
    ]
    assert embedder.dimension == 3


def test_cuda_requested_without_cuda_is_an_embedding_error(fake_models: None) -> None:
    with pytest.raises(EmbeddingError, match="CUDA is not available"):
        SentenceTransformerEmbedder(EmbeddingConfig(device="cuda")).embed_query("x")


def test_out_of_memory_and_bad_vectors_are_embedding_errors(fake_models: None) -> None:
    embedder = SentenceTransformerEmbedder(EmbeddingConfig(device="cpu"))
    embedder.embed_query("warm-up")
    (model,) = _FakeSentenceTransformer.instances

    model.error = RuntimeError("CUDA out of memory")
    with pytest.raises(EmbeddingError):
        embedder.embed_query("x")

    model.error = None
    model.output = [[1.0, 0.0]]
    with pytest.raises(VectorSizeMismatchError):
        embedder.embed_query("x")

    model.output = [[float("nan"), 0.0, 0.0]]
    with pytest.raises(EmbeddingError, match="non-finite"):
        embedder.embed_query("x")


def test_missing_semantic_dependencies_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(EmbeddingError, match="uv sync --group semantic"):
        SentenceTransformerEmbedder(EmbeddingConfig()).embed_query("x")
    with pytest.raises(RerankerError, match="uv sync --group semantic"):
        CrossEncoderReranker(RerankerConfig()).rerank(
            "x",
            [
                RetrievalCandidate(
                    hit=StaticRetriever(RetrievalBackend.HYBRID, [1])
                    .retrieve(RetrievalQuery(text="x", entity_type=RetrievalEntityType.PERSON))
                    .hits[0],
                    text="t",
                )
            ],
            limit=1,
        )


def test_cross_encoder_scores_query_and_semantic_document_pairs(fake_models: None) -> None:
    hits = (
        StaticRetriever(RetrievalBackend.HYBRID, [1, 2])
        .retrieve(RetrievalQuery(text="q", entity_type=RetrievalEntityType.PERSON))
        .hits
    )
    reranker = CrossEncoderReranker(RerankerConfig(device="cpu"))

    reranked = reranker.rerank(
        "пикеты",
        [RetrievalCandidate(hits[0], "short"), RetrievalCandidate(hits[1], "a longer text")],
        limit=2,
    )

    assert [hit.entity_id for hit in reranked] == [2, 1]
    assert reranker._model.pairs == [("пикеты", "short"), ("пикеты", "a longer text")]


def test_semantic_config_from_env_and_pool_bounds() -> None:
    config = SemanticRetrievalConfig.from_env(
        {
            "QDRANT_URL": "http://127.0.0.1:6333",
            "SEMANTIC_RERANK": "1",
            "SEMANTIC_CANDIDATE_POOL_SIZE": "50",
        }
    )

    assert (config.qdrant_url, config.rerank, config.candidate_pool_size) == (
        "http://127.0.0.1:6333",
        True,
        50,
    )
    assert config.collections[RetrievalEntityType.PERSON] == "persons_semantic"
    assert SemanticRetrievalConfig.from_env({}).qdrant_url is None
    with pytest.raises(SemanticConfigurationError, match="SEMANTIC_CANDIDATE_POOL_SIZE"):
        SemanticRetrievalConfig(candidate_pool_size=5000)
    with pytest.raises(SemanticConfigurationError, match="must be an integer"):
        SemanticRetrievalConfig.from_env({"SEMANTIC_CANDIDATE_POOL_SIZE": "abc"})
    with pytest.raises(SemanticConfigurationError, match="EMBEDDING_BATCH_SIZE"):
        EmbeddingConfig.from_env({"EMBEDDING_BATCH_SIZE": "0"})


def test_the_vector_backend_is_qdrant_unless_pgvector_is_chosen() -> None:
    default = SemanticRetrievalConfig.from_env({"QDRANT_URL": "http://127.0.0.1:6333"})
    pgvector = SemanticRetrievalConfig.from_env({"SEMANTIC_VECTOR_BACKEND": " PGVector "})

    assert (default.vector_backend, default.enabled) == ("qdrant", True)
    assert default.qdrant_service_url == "http://127.0.0.1:6333"
    assert SemanticRetrievalConfig.from_env({}).enabled is False
    # pgvector lives in the application's PostgreSQL: no QDRANT_URL, no Qdrant probe.
    assert (pgvector.vector_backend, pgvector.enabled, pgvector.qdrant_service_url) == (
        "pgvector",
        True,
        None,
    )
    with pytest.raises(SemanticConfigurationError, match="SEMANTIC_VECTOR_BACKEND"):
        SemanticRetrievalConfig.from_env({"SEMANTIC_VECTOR_BACKEND": "faiss"})


def test_the_factory_builds_the_configured_vector_store() -> None:
    session_factory = sessionmaker()

    assert isinstance(
        create_configured_vector_store(
            SemanticRetrievalConfig(qdrant_url=":memory:"), session_factory
        ),
        QdrantVectorStore,
    )
    assert isinstance(
        create_configured_vector_store(
            SemanticRetrievalConfig(vector_backend="pgvector"), session_factory
        ),
        PgVectorStore,
    )


def test_search_output_says_scores_are_not_confidences() -> None:
    result = StaticRetriever(RetrievalBackend.HYBRID, [4]).retrieve(
        RetrievalQuery(text="x", entity_type=RetrievalEntityType.PERSON)
    )

    text = format_search_result(result, {4: "Персона: Иван.\nСобытия: ..."})

    assert "not confidences of any fact" in text
    assert "person #4" in text and "Персона: Иван." in text
    assert "События" not in text


def test_evaluate_retrieval_refuses_a_non_disposable_database() -> None:
    env = {key: value for key, value in os.environ.items() if key != "EVALUATION_DATABASE_URL"}
    completed = subprocess.run(
        [
            sys.executable,
            "src/main.py",
            "evaluate-retrieval",
            "--database-url",
            "postgresql+psycopg://nobody:nothing@127.0.0.1:1/court_monitor",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode != 0
    assert "Refusing to wipe database 'court_monitor'" in completed.stderr


def test_model_load_runtime_failure_is_an_embedding_error(
    fake_models: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_init(self: _FakeSentenceTransformer, model_id: str, device: str) -> None:
        raise RuntimeError("CUDA error: no CUDA-capable device is detected")

    monkeypatch.setattr(_FakeSentenceTransformer, "__init__", failing_init)

    with pytest.raises(EmbeddingError, match="Cannot load embedding model"):
        SentenceTransformerEmbedder(EmbeddingConfig(device="cpu")).embed_query("x")


def test_creating_semantic_components_touches_neither_network_nor_models(
    fake_models: None,
) -> None:
    components = create_semantic_components(
        None,  # type: ignore[arg-type]  # not used until a retrieval runs
        SemanticRetrievalConfig(qdrant_url="http://127.0.0.1:1"),
        {},
    )

    assert _FakeSentenceTransformer.instances == []
    assert components.reranker is None


def test_semantic_cli_reports_unavailable_qdrant_without_traceback(
    fake_models: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:1")
    args = argparse.Namespace(
        command="semantic-search", text="x", entity="person", backend="dense", limit=3
    )

    with pytest.raises(SystemExit) as exit_info:
        run_semantic_command(args, None)  # type: ignore[arg-type]

    assert exit_info.value.code == SEMANTIC_EXIT_UNAVAILABLE
    assert "Semantic retrieval unavailable" in capsys.readouterr().err


def test_concurrent_first_calls_load_the_embedding_model_once(
    fake_models: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_init = _FakeSentenceTransformer.__init__

    def slow_init(self: _FakeSentenceTransformer, model_id: str, device: str) -> None:
        time.sleep(0.2)  # a real model load takes seconds; widen the race window
        original_init(self, model_id, device)

    monkeypatch.setattr(_FakeSentenceTransformer, "__init__", slow_init)
    embedder = SentenceTransformerEmbedder(EmbeddingConfig(device="cpu"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        vectors = list(pool.map(embedder.embed_query, ["a", "b", "c", "d"]))

    assert len(_FakeSentenceTransformer.instances) == 1
    assert len(vectors) == 4


def test_concurrent_first_calls_load_the_reranker_model_once(
    fake_models: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[object] = []
    original_init = _FakeCrossEncoder.__init__

    def slow_init(self: _FakeCrossEncoder, model_id: str, device: str) -> None:
        time.sleep(0.2)
        original_init(self, model_id, device)
        created.append(self)

    monkeypatch.setattr(_FakeCrossEncoder, "__init__", slow_init)
    reranker = CrossEncoderReranker(RerankerConfig(device="cpu"))
    hit = (
        StaticRetriever(RetrievalBackend.HYBRID, [1])
        .retrieve(RetrievalQuery(text="q", entity_type=RetrievalEntityType.PERSON))
        .hits[0]
    )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda _: reranker.rerank("q", [RetrievalCandidate(hit, "t")], limit=1), range(4)
            )
        )

    assert len(created) == 1


def test_failed_dimension_check_does_not_leave_a_half_loaded_model(
    fake_models: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_FakeSentenceTransformer, "get_embedding_dimension", lambda self: None)
    embedder = SentenceTransformerEmbedder(EmbeddingConfig(device="cpu"))

    for _ in range(2):
        with pytest.raises(EmbeddingError, match="does not report a dimension"):
            embedder.embed_query("x")


def test_components_tie_the_dense_threshold_to_the_embedding_model(fake_models: None) -> None:
    config = SemanticRetrievalConfig(qdrant_url="http://127.0.0.1:1")

    default = create_semantic_components(None, config, {})  # type: ignore[arg-type]
    explicit = create_semantic_components(
        None,  # type: ignore[arg-type]
        config,
        {"EMBEDDING_MODEL_ID": "sentence-transformers/LaBSE", "SEMANTIC_DENSE_MIN_SCORE": "0.5"},
    )

    assert default.dense_min_score == 0.80
    assert explicit.dense_min_score == 0.5
    assert (
        explicit.relevance_policy()
        .accept(
            RetrievalQuery(text="x", entity_type=RetrievalEntityType.PERSON),
            StaticRetriever(RetrievalBackend.DENSE, []).retrieve(
                RetrievalQuery(text="x", entity_type=RetrievalEntityType.PERSON)
            ),
        )
        .embedding_model_id
        == "sentence-transformers/LaBSE"
    )
    with pytest.raises(SemanticConfigurationError, match="SEMANTIC_DENSE_MIN_SCORE"):
        create_semantic_components(
            None,  # type: ignore[arg-type]
            config,
            {"EMBEDDING_MODEL_ID": "sentence-transformers/LaBSE"},
        )
