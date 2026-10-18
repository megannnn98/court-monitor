"""Does another model answer as the one the steps trust? On a sample, before switching.

A cheaper model may answer worse. This asks it (`ENTITY_NORMALIZE_MODEL`) the questions
steps 4 and 5 already have answers to and the names step 3 has, and counts how often
the two agree — nothing is written. Switch only where the agreement is good enough.
"""

from __future__ import annotations

import logging
import random
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from sqlalchemy.orm import Session, sessionmaker

from analysis import testing as analysis_testing
from analysis.politics.classification import (
    PoliticsClassifier,
    PoliticsItem,
    verdict_of,
)
from analysis.service import GroupFollowers
from processing import read as identity
from processing import testing as processing_testing
from processing.identity.grouping import name_key
from processing.identity.normalizer import NameItem, NameNormalizer
from processing.roles.roles import (
    QUOTE_CONTEXT,
    QUOTES,
    RoleClassifier,
    RoleItem,
    latest_quotes,
    role_of,
)

logger = logging.getLogger("entities")

STEPS = ("names", "roles", "politics")
BATCH_SIZE = 25
SHOWN_DISAGREEMENTS = 20


@dataclass
class Comparison:
    step: str
    model: str
    sample: int = 0
    answered: int = 0
    agree: int = 0
    # (the trusted answer, the other model's) → how often.
    confusion: Counter[tuple[str, str]] = field(default_factory=Counter)
    disagreements: list[dict[str, str]] = field(default_factory=list)

    @property
    def agreement(self) -> float:
        return round(self.agree / self.answered, 3) if self.answered else 0.0

    def add(self, name: str, trusted: str, other: str, why: str = "") -> None:
        self.answered += 1
        self.confusion[(trusted, other)] += 1
        if trusted == other:
            self.agree += 1
        elif len(self.disagreements) < SHOWN_DISAGREEMENTS:
            self.disagreements.append(
                {"name": name, "trusted": trusted, "other": other, "why": why}
            )

    def report(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "model": self.model,
            "sample": self.sample,
            "answered": self.answered,
            "agree": self.agree,
            "agreement": self.agreement,
            "confusion": {f"{a} → {b}": count for (a, b), count in self.confusion.most_common()},
            "disagreements": self.disagreements,
        }


def _sample[T](rows: Sequence[T], size: int, seed: int) -> list[T]:
    rows = list(rows)
    random.Random(seed).shuffle(rows)
    return rows[:size]


def _quotes(session: Session, ids: Sequence[int]) -> dict[int, list[str]]:
    quotes: dict[int, list[str]] = {}
    for (group_id, _publication), quote in latest_quotes(
        session, list(ids), GroupFollowers(), context=QUOTE_CONTEXT, per_group=QUOTES
    ).items():
        quotes.setdefault(group_id, []).append(quote)
    return quotes


def _batches[T](items: Sequence[T]) -> list[Sequence[T]]:
    return [items[start : start + BATCH_SIZE] for start in range(0, len(items), BATCH_SIZE)]


def _ask[Item, Answer](
    ask: Callable[[Sequence[Item]], dict[int, Answer]], items: Sequence[Item]
) -> dict[int, Answer]:
    answers: dict[int, Answer] = {}
    for batch in _batches(items):
        try:
            answers.update(ask(batch))
        except Exception as exc:  # noqa: BLE001 - a failed batch is unanswered, not a crash
            logger.warning("event=entity_compare_batch_failed size=%d error=%s", len(batch), exc)
    return answers


def compare_roles(
    session_factory: sessionmaker[Session], other: RoleClassifier, *, size: int, seed: int
) -> Comparison:
    """The roles the model gave (step 4) against the other model's."""
    with session_factory() as session:
        rows = processing_testing.model_roles(session)
        picked = _sample(rows, size, seed)
        quotes = _quotes(session, [row.id for row in picked])
    items = [RoleItem(row.id, row.name, tuple(quotes.get(row.id, []))) for row in picked]
    answers = _ask(other.classify, items)
    result = Comparison("roles", other.model, sample=len(items))
    for row in picked:
        answer = answers.get(row.id)
        if answer is not None:
            result.add(row.name, row.role, role_of(answer.kind), answer.explanation)
    return result


class _Verdict(NamedTuple):
    id: int
    name: str
    verdict: str


def compare_politics(
    session_factory: sessionmaker[Session], other: PoliticsClassifier, *, size: int, seed: int
) -> Comparison:
    """The verdicts the model gave (step 5) against the other model's."""
    with session_factory() as session:
        verdicts = analysis_testing.model_verdicts(session)
        names = identity.groups(session, [group_id for group_id, _ in verdicts])
        rows = [
            _Verdict(group_id, names[group_id].name, verdict)
            for group_id, verdict in verdicts
            if group_id in names
        ]
        picked = _sample(rows, size, seed)
        ids = [row.id for row in picked]
        quotes = _quotes(session, ids)
        memorial = identity.memorial_categories(session, ids)
        articles = processing_testing.sorted_charge_articles(session, ids)
    items = [
        PoliticsItem(
            row.id,
            row.name,
            tuple(quotes.get(row.id, [])),
            tuple(articles.get(row.id, [])),
            memorial.get(row.id),
        )
        for row in picked
    ]
    answers = _ask(other.classify, items)
    result = Comparison("politics", other.model, sample=len(items))
    for row in picked:
        answer = answers.get(row.id)
        if answer is not None:
            result.add(row.name, row.verdict, verdict_of(answer.verdict), answer.explanation)
    return result


def compare_names(
    session_factory: sessionmaker[Session], other: NameNormalizer, *, size: int, seed: int
) -> Comparison:
    """The names the model gave (step 3) against the other model's, by their key: the
    patronymic and the order kept, the case and «ё» folded."""
    with session_factory() as session:
        rows = processing_testing.model_names(session)
    picked = _sample(rows, size, seed)
    items = [
        NameItem(position, tuple(str(form) for form, _ in row.variants[:8]), "")
        for position, row in enumerate(picked)
    ]
    answers = _ask(other.normalize, items)
    result = Comparison("names", other.model, sample=len(items))
    for position, row in enumerate(picked):
        answer = answers.get(position)
        if answer is not None:
            result.add(row.name, name_key(row.name), name_key(answer.nominative))
    return result


def run_comparison(
    session_factory: sessionmaker[Session], step: str, *, size: int, seed: int
) -> dict[str, Any]:
    """Compare the configured model with the trusted one on one step; the report."""
    from analysis.politics.classification import politics_classifier_from_env
    from processing.identity.normalizer import name_normalizer_from_env
    from processing.roles.roles import role_classifier_from_env

    if step == "names":
        normalizer = name_normalizer_from_env()
        if normalizer is None:
            raise SystemExit("No model configured: OPENROUTER_API_KEY is not set")
        result = compare_names(session_factory, normalizer, size=size, seed=seed)
    elif step == "roles":
        roles = role_classifier_from_env()
        if roles is None:
            raise SystemExit("No model configured: OPENROUTER_API_KEY is not set")
        result = compare_roles(session_factory, roles, size=size, seed=seed)
    else:
        politics = politics_classifier_from_env()
        if politics is None:
            raise SystemExit("No model configured: OPENROUTER_API_KEY is not set")
        result = compare_politics(session_factory, politics, size=size, seed=seed)
    return result.report()
