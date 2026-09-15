"""Namesake benchmark: the existing ER v2 evaluation on name pairs from the real corpus.

Cases use the `evaluate-er` corpus format (`persons.resolution.evaluation.ErCorpus`),
so metrics have one definition. The hard gate counts every AUTO_LINK of an
incoming name to a person who is not the same real person, including the
"indistinguishable" namesakes the existing report lists apart.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from sqlalchemy import Engine

from db.database import create_session_factory
from db.maintenance import truncate_disposable_tables
from evaluation.real_world.models import DEFAULT_DATA_DIR
from evaluation.real_world.results import NamesakeSection, SectionStatus
from persons.resolution.evaluation import ErCorpus, load_er_corpus, run_er_evaluation
from persons.resolution.models import PersonResolutionAction

NamesakeCorpus = ErCorpus
DEFAULT_NAMESAKE_PATH = DEFAULT_DATA_DIR / "namesake_cases.json"
MIN_NAMESAKE_CASES = 30


def load_namesakes(path: Path = DEFAULT_NAMESAKE_PATH) -> NamesakeCorpus | None:
    return load_er_corpus(path) if path.exists() else None


def evaluate_namesakes(engine: Engine, corpus: NamesakeCorpus | None) -> NamesakeSection:
    if corpus is None or not corpus.cases:
        return NamesakeSection(status=SectionStatus.NOT_RUN, not_run_reason="no namesake cases")
    truncate_disposable_tables(engine)
    try:
        run = run_er_evaluation(create_session_factory(engine), corpus)
    finally:
        truncate_disposable_tables(engine)
    different_person_links = sum(
        1
        for outcome in run.outcomes
        if outcome.action is PersonResolutionAction.AUTO_LINK
        and (outcome.true_person is None or outcome.selected_person != outcome.true_person)
    )
    decision = run.decision.model_dump(exclude={"actions"})
    combined = next((g for g in run.generators if g.generator == "combined"), None)
    return NamesakeSection(
        status=SectionStatus.RUN,
        not_run_reason=None
        if len(corpus.cases) >= MIN_NAMESAKE_CASES
        else f"only {len(corpus.cases)} cases (< {MIN_NAMESAKE_CASES})",
        cases=len(corpus.cases),
        by_category=dict(sorted(Counter(case.category for case in corpus.cases).items())),
        different_person_auto_links=different_person_links,
        decision={k: v for k, v in decision.items() if isinstance(v, int | float) or v is None},
        candidate_recall_at={str(k): v for k, v in combined.recall_at.items()} if combined else {},
    )
