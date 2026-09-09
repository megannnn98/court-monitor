import pytest

from dense_config import DenseSearchConfig


def test_dense_search_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "article_chunks_dense")
    monkeypatch.setenv("EMBEDDING_MODEL_ID", "intfloat/multilingual-e5-base")

    config = DenseSearchConfig.from_env()

    assert config.qdrant_url == "http://127.0.0.1:6333"
    assert config.collection_name == "article_chunks_dense"
    assert config.embedding_model_id == "intfloat/multilingual-e5-base"
