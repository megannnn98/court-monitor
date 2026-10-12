"""Dense retrieval on a disposable PostgreSQL with pgvector, over a fake embedder: the
windows are indexed and searched, a changed text is indexed again, and a publication
found by both the ranked text search and the dense one ranks first (RRF)."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Sequence

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from application import build_processing_service, build_results_service
from investigation.models import InvestigationQuery
from investigation.service import InvestigationService
from investigation.services.retrieval import (
    DensePublicationsRetriever,
    LexicalPublicationsRetriever,
)
from processing.evidence.publication_vectors import windows_of

WORDS = ("бонцлер", "адвокат", "суд", "тула", "выставка", "кража", "петров")


class WordEmbedder:
    """A vector per word of `WORDS` it holds: texts about the same people are near."""

    model_id = "fake/words-v1"

    def _vector(self, value: str) -> list[float]:
        folded = value.lower()
        counts = [float(len(re.findall(word, folded))) for word in WORDS]
        norm = math.sqrt(sum(count * count for count in counts)) or 1.0
        return [count / norm for count in counts]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(item) for item in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture(autouse=True)
def _no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def _seed(session_factory: sessionmaker[Session]) -> dict[str, int]:
    texts = {
        "lawyer": "Адвокат Бонцлер выступила в суде Тулы. Бонцлер защищает подзащитного.",
        "court": "Суд в Туле продлил арест. Адвокат говорит о давлении.",
        "theft": "В Москве задержан Петров по делу о краже.",
        "art": "В Туле открылась выставка.",
    }
    ids = {}
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for key, body in texts.items():
            ids[key], _ = seed.article(source, external_id=key, title=key, text=body)
        session.commit()
    return ids


def test_long_texts_are_cut_into_overlapping_windows_that_cover_them() -> None:
    windows = windows_of("я" * 3_500)

    assert windows[0] == (0, 1_200) and windows[-1][1] == 3_500
    assert all(start < end for start, end in windows)
    assert windows_of("") == []


def test_publications_are_indexed_once_and_again_when_their_text_changes(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    processing = build_processing_service(session_factory)

    first = processing.index_publication_vectors(WordEmbedder())
    again = processing.index_publication_vectors(WordEmbedder())
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE parsed_articles SET text = text || ' Бонцлер.' WHERE id = :id"),
            {"id": ids["art"]},
        )
    changed = processing.index_publication_vectors(WordEmbedder())

    assert (first.indexed, first.windows) == (4, 4)
    assert (again.indexed, again.unchanged) == (0, 4)
    assert (changed.indexed, changed.unchanged) == (1, 3)


def test_the_dense_retriever_ranks_by_meaning_and_quotes_the_window(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    build_processing_service(session_factory).index_publication_vectors(WordEmbedder())
    retriever = DensePublicationsRetriever(build_results_service(session_factory), WordEmbedder())

    found = asyncio.run(
        retriever.search(InvestigationQuery(kind="keywords", name="адвокат Бонцлер"))
    )

    assert found[0].article_id == ids["lawyer"]
    assert found[0].score_type == "cosine" and found[0].score is not None
    assert all(found[i].score >= found[i + 1].score for i in range(len(found) - 1))  # type: ignore[operator]
    assert "Бонцлер" in found[0].text and found[0].span_start == 0


def test_a_publication_both_searches_find_ranks_above_one_only_one_finds(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    build_processing_service(session_factory).index_publication_vectors(WordEmbedder())
    results = build_results_service(session_factory)
    service = InvestigationService(
        retrievers=[
            LexicalPublicationsRetriever(results),
            DensePublicationsRetriever(results, WordEmbedder()),
        ]
    )

    result = asyncio.run(service.investigate("Что известно про адвоката Бонцлер?"))

    quoted = [item.article_id for item in result.evidence]
    # «lawyer» holds the name (text search) and is nearest in meaning (dense): first.
    assert quoted[0] == ids["lawyer"]
    assert {item.source_id for item in result.evidence} >= {"publications_dense"}


def test_a_long_article_does_not_crowd_the_others_out_of_the_best_documents(
    session_factory: sessionmaker[Session],
) -> None:
    """A long text is many windows, all near the query. Taking the nearest windows first
    and folding them into articles afterwards returned that one article alone."""
    ids = _seed(session_factory)
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("long", "https://long.example.test")
        long_id, _ = seed.article(
            source,
            external_id="long",
            title="long",
            text="Адвокат Бонцлер выступила в суде. " * 900,
        )
        session.commit()
    indexed = build_processing_service(session_factory).index_publication_vectors(WordEmbedder())
    embedder = WordEmbedder()

    found = build_results_service(session_factory).dense_publications(
        embedder.embed_query("адвокат Бонцлер"), model_id=embedder.model_id, limit=2
    )

    # Far more windows of the long text than eight times the limit.
    assert indexed.windows > 20
    # Two documents were asked for and two came: the long one and the next nearest.
    assert {publication.article_id for publication in found} == {long_id, ids["lawyer"]}
