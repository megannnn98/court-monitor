"""Junk-screen measurement uses the configured embedding provider."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from evaluation.junk_screen import measure
from semantic_retrieval.embeddings import EmbeddingSpend


class _FakeEmbedder:
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class _FakeEmbedded:
    def __init__(self, embed: Any, articles: Sequence[measure.Article]) -> None:
        embed([article.title for article in articles])


def _scores(
    _embedded: _FakeEmbedded,
    articles: Sequence[measure.Article],
    _examples: Sequence[int],
    screened: Sequence[int],
    **_kwargs: object,
) -> list[float]:
    return [0.9 if articles[index].positive else 0.1 for index in screened]


def test_measurement_builds_each_model_with_the_selected_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    articles = [
        measure.Article(1, "purged_legal", "2026-08-01", "cal case", "", True, "case", True, True),
        measure.Article(
            2, "purged_legal", "2026-08-02", "cal junk", "", False, "other", False, True
        ),
        measure.Article(3, "purged_legal", "2026-09-01", "val case", "", True, "case", True, True),
        measure.Article(
            4, "purged_legal", "2026-09-02", "val junk", "", False, "other", False, True
        ),
    ]
    made: list[tuple[Mapping[str, str], str, EmbeddingSpend]] = []

    def create(env: Mapping[str, str], *, model_id: str, spend: EmbeddingSpend) -> _FakeEmbedder:
        made.append((env, model_id, spend))
        return _FakeEmbedder()

    monkeypatch.setattr(measure, "load", lambda: articles)
    monkeypatch.setattr(measure, "create_text_embedder", create)
    monkeypatch.setattr(measure, "Embedded", _FakeEmbedded)
    monkeypatch.setattr(
        measure,
        "_fragment_scores",
        lambda embedded, rows, examples, screened: (
            _scores(embedded, rows, examples, screened),
            _scores(embedded, rows, examples, screened),
        ),
    )
    monkeypatch.setattr(measure, "_knn_scores", _scores)
    monkeypatch.setattr(measure, "_logistic_scores", _scores)
    env = {"EMBEDDING_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "key"}
    spend = EmbeddingSpend()

    summary = measure.run(["model/one", "model/two"], env=env, spend=spend)

    assert [
        (model_id, selected_env["EMBEDDING_PROVIDER"]) for selected_env, model_id, _ in made
    ] == [
        ("model/one", "openrouter"),
        ("model/two", "openrouter"),
    ]
    assert all(selected_spend is spend for _, _, selected_spend in made)
    assert summary["embedding_usage"] == {"calls": 0, "prompt_tokens": 0, "cost_usd": 0.0}
