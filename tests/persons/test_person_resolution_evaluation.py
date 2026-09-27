"""ER v2 evaluation corpus, evaluation run and CLI."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine
from db.maintenance import NotDisposableDatabaseError
from db.orm_models import PersonRecord, PersonResolutionDecisionRecord
from persons.resolution.cli import (
    DEFAULT_ER_CORPUS_PATH,
    run_evaluate_er,
    run_person_resolution_command,
)
from persons.resolution.evaluation import load_er_corpus, run_er_evaluation
from persons.resolution.models import PersonResolutionAction


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
        "same-full-name-same-person",
        "same-full-name-different-person",
        "two-active-namesakes",
        "reviewer-created-namesake",
        "exact-fuzzy-competitor",
        "reordered-same-person",
        "reordered-namesake-ambiguity",
    } <= tags
    assert categories["indistinguishable"] >= 1


def test_er_evaluation_has_no_false_links_on_the_corpus(
    session_factory: sessionmaker[Session],
) -> None:
    run = run_er_evaluation(session_factory, load_er_corpus(DEFAULT_ER_CORPUS_PATH), sweep=True)

    decision = run.decision
    assert decision.false_links == 0
    namesakes = [o for o in run.outcomes if "namesake" in " ".join(o.tags)]
    assert namesakes and all(
        o.action is not PersonResolutionAction.AUTO_LINK
        for o in namesakes
        if o.category == "ambiguous"
    )
    # A single existing person with the same full name *and patronymic* is still linked
    # without context (documented limitation); a name without patronymic is reviewed.
    assert decision.indistinguishable_namesake_links == 1
    assert decision.auto_link_precision == 1.0
    assert decision.false_create_new == 0
    # Name-only cross-article links are reviewed since real-world validation v1 (0.37 measured).
    assert decision.auto_link_recall >= 0.35
    recall = {row.generator: row.recall_at for row in run.generators}
    assert recall["exact"][10] < recall["trigram"][10] == 1.0
    assert recall["combined"][5] == 1.0
    # Before the name-only evidence rule 0.80 produced false links on this corpus; the
    # rule, not the threshold, now keeps name matches without patronymic out of AUTO_LINK.
    by_auto = {
        row.auto_link_min_score: row.metrics.false_links
        for row in run.sweep
        if row.review_min_score == 0.4 and row.min_margin == 0.1
    }
    assert by_auto[0.85] == 0 and by_auto[0.8] == 0


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
        output_path=None,
    )

    with pytest.raises(NotDisposableDatabaseError):
        run_evaluate_er(args)
    assert create_database_engine(args.database_url).url.database == "court_monitor"
