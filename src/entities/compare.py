"""Does another model answer as the one the steps trust? On a sample, before switching.

A cheaper model may answer worse. This asks it (`ENTITY_NORMALIZE_MODEL`) the questions
steps 5 and 6 already have answers to and the names step 3 has, and counts how often
the two agree — nothing is written. Switch only where the agreement is good enough.
"""

from __future__ import annotations

import logging
import random
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import EntityGroupPoliticsRecord, EntityGroupRecord, EntityGroupRoleRecord
from entities.grouping import name_key
from entities.normalizer import NameItem, NameNormalizer
from entities.politics import MEMORIAL_CATEGORIES, PoliticsClassifier, PoliticsItem, verdict_of
from entities.roles import _QUOTES, QUOTE_CONTEXT, QUOTES, RoleClassifier, RoleItem, role_of

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
    for group_id, quote in session.execute(
        _QUOTES, {"groups": list(ids), "context": QUOTE_CONTEXT, "quotes": QUOTES}
    ).all():
        quotes.setdefault(group_id, []).append(" ".join((quote or "").split()))
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
    """The roles the model gave (step 5) against the other model's."""
    with session_factory() as session:
        rows = session.execute(
            select(EntityGroupRecord.id, EntityGroupRecord.name, EntityGroupRoleRecord.role)
            .join(EntityGroupRoleRecord, EntityGroupRoleRecord.group_id == EntityGroupRecord.id)
            .where(EntityGroupRoleRecord.method == "model")
        ).all()
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


def compare_politics(
    session_factory: sessionmaker[Session], other: PoliticsClassifier, *, size: int, seed: int
) -> Comparison:
    """The verdicts the model gave (step 6) against the other model's."""
    with session_factory() as session:
        rows = session.execute(
            select(EntityGroupRecord.id, EntityGroupRecord.name, EntityGroupPoliticsRecord.verdict)
            .join(
                EntityGroupPoliticsRecord,
                EntityGroupPoliticsRecord.group_id == EntityGroupRecord.id,
            )
            .where(EntityGroupPoliticsRecord.method == "model")
        ).all()
        picked = _sample(rows, size, seed)
        ids = [row.id for row in picked]
        quotes = _quotes(session, ids)
        memorial = {
            group_id: category
            for group_id, category in session.execute(MEMORIAL_CATEGORIES, {"groups": ids}).all()
        }
        articles: dict[int, list[str]] = {}
        for group_id, article in session.execute(
            text(
                "SELECT DISTINCT group_id, article FROM entity_group_charges "
                "WHERE group_id = ANY(:groups) ORDER BY group_id, article"
            ),
            {"groups": ids},
        ).all():
            articles.setdefault(group_id, []).append(article)
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
        rows = session.execute(
            select(EntityGroupRecord.name, EntityGroupRecord.variants).where(
                EntityGroupRecord.name_source == "model"
            )
        ).all()
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
