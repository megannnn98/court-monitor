"""ER v2 evaluation corpus, evaluation run, CLI and a real-Qdrant semantic generator."""

from __future__ import annotations

import argparse
import os
import uuid
from collections import Counter
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from support.person_resolution_fixtures import seed_mentions
from support.research_db_fixtures import ResearchSeeder
from support.semantic_fakes import HashingEmbedder

from db.database import create_database_engine
from db.maintenance import NotDisposableDatabaseError
from db.orm_models import EntityMentionRecord, PersonRecord, PersonResolutionDecisionRecord
from persons.resolution.cli import (
    DEFAULT_ER_CORPUS_PATH,
    run_evaluate_er,
    run_person_resolution_command,
)
from persons.resolution.evaluation import load_er_corpus, run_er_evaluation
from persons.resolution.factory import build_person_resolution_service
from persons.resolution.models import PersonResolutionAction
from semantic_retrieval.factory import SemanticComponents
from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType
from semantic_retrieval.vector_store import QdrantVectorStore


def test_corpus_covers_positive_hard_negative_and_ambiguous_cases() -> None:
    corpus = load_er_corpus(DEFAULT_ER_CORPUS_PATH)
    categories = Counter(case.category for case in corpus.cases)
    tags = {tag for case in corpus.cases for tag in case.tags}

    assert len(corpus.cases) >= 50
    assert categories["positive"] >= 30 and categories["negative"] >= 10
    assert categories["ambiguous"] >= 5
    assert {
        "reordered",
        "yo",
        "case",
        "whitespace",
        "initials",
        "alias",
        "typo",
        "same-surname",
        "same-initial",
        "same-first-last-different-patronymic",
        "similar-spelling",
        "semantic-trap",
        "duplicate-persons",
    } <= tags


def test_er_evaluation_has_no_false_links_on_the_corpus(
    session_factory: sessionmaker[Session],
) -> None:
    run = run_er_evaluation(session_factory, load_er_corpus(DEFAULT_ER_CORPUS_PATH), sweep=True)

    decision = run.decision
    assert decision.false_links == 0
    assert decision.auto_link_precision == 1.0
    assert decision.false_create_new == 0
    assert decision.auto_link_recall >= 0.5
    recall = {row.generator: row.recall_at for row in run.generators}
    assert recall["exact"][10] < recall["trigram"][10] == 1.0
    assert recall["combined"][5] == 1.0
    # The calibrated defaults are the lowest auto-link minimum without false links.
    by_auto = {
        row.auto_link_min_score: row.metrics.false_links
        for row in run.sweep
        if row.review_min_score == 0.4 and row.min_margin == 0.1
    }
    assert by_auto[0.85] == 0 and by_auto[0.8] > 0


def test_resolve_person_cli_is_a_dry_run(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    from support.person_resolution_fixtures import seed_person

    seed_person(session_factory, "Иван Иванов")
    seed_person(session_factory, "Илья Иванов")

    handled = run_person_resolution_command(
        argparse.Namespace(command="resolve-person", name="И. Иванов", json=False),
        session_factory,
    )

    out = capsys.readouterr().out
    assert handled
    assert "Decision: review" in out and "initials_only" in out
    assert "given name: initial_compatible" in out
    assert "Dry run: nothing was written." in out
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PersonRecord)) == 2
        assert session.scalar(select(func.count()).select_from(PersonResolutionDecisionRecord)) == 0


def test_evaluate_er_refuses_a_non_disposable_database() -> None:
    args = argparse.Namespace(
        database_url="postgresql+psycopg://nobody:nothing@127.0.0.1:1/court_monitor",
        corpus_path=Path(DEFAULT_ER_CORPUS_PATH),
        sweep=False,
        semantic=False,
        qdrant_url=":memory:",
        output_path=None,
    )

    with pytest.raises(NotDisposableDatabaseError):
        run_evaluate_er(args)
    assert create_database_engine(args.database_url).url.database == "court_monitor"


@pytest.mark.qdrant
def test_semantic_candidate_from_real_qdrant_joins_the_pool_but_never_links(
    session_factory: sessionmaker[Session],
) -> None:
    url = os.environ.get("QDRANT_TEST_URL")
    if not url:
        pytest.skip("QDRANT_TEST_URL is not set")
    client = QdrantClient(url=url, timeout=10, check_compatibility=False)
    collections = {
        RetrievalEntityType.PERSON: f"test_er_persons_{uuid.uuid4().hex[:8]}",
        RetrievalEntityType.EVENT: f"test_er_events_{uuid.uuid4().hex[:8]}",
    }
    try:
        with session_factory() as session:
            seed = ResearchSeeder(session)
            source_id = seed.source("ОВД-Инфо", "https://ovd.info")
            text = "Ивана Иванова задержали на пикете против войны"
            _, run_id = seed.article(source_id, external_id="er-q", title="Хроника", text=text)
            ivan = seed.person("Иван Иванов")
            seed.event(
                run_id, text, event_type="detention", event_date=None, links=[(ivan, "subject")]
            )
            session.commit()
        components = SemanticComponents(
            session_factory=session_factory,
            store=QdrantVectorStore(client),
            embedder=HashingEmbedder(),
            collections=collections,
        )
        components.indexer().rebuild(RetrievalEntityType.PERSON)
        service = build_person_resolution_service(
            session_factory,
            {"ER_SEMANTIC_CANDIDATES": "1"},
            semantic_retriever=components.retriever(RetrievalBackend.DENSE),
        )
        # Shares words with the indexed biography, not with the person's name.
        _, (mention_id,) = seed_mentions(session_factory, "Пикет Войны")
        with session_factory.begin() as session:
            outcome = service.resolve_mention(
                session, session.get_one(EntityMentionRecord, mention_id)
            )
            decision = session.scalars(select(PersonResolutionDecisionRecord)).one()

        assert outcome is not None
        assert outcome.action is not PersonResolutionAction.AUTO_LINK
        assert outcome.person_id != ivan
        (candidate,) = [c for c in decision.candidates if c["candidate"]["person_id"] == ivan]
        assert candidate["candidate"]["sources"] == ["semantic"]
        assert candidate["candidate"]["semantic_similarity"] > 0
        assert decision.semantic_source == "ok"
    finally:
        for name in collections.values():
            if client.collection_exists(name):
                client.delete_collection(name)
