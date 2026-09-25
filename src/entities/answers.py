"""The cache of a model's answers about entities, and the asking of what it lacks.

What steps 5 and 6 share, so that a rebuild costs nothing it need not:

- An answer is found by the hash of the question (`input_hash`), not by the entity
  key: a rebuild, a merge, a region in the key, a new rule of grouping — none of them
  asks again what was asked.
- A positive answer sticks: an entity once «accused» (step 5) or «political» (step 6)
  is not asked again when its quotes change (a new publication); only a person undoes
  it. The others are asked again then: a new publication may bring a case.
- A new prompt version need not ask everything again: `accepted` takes the answers of
  earlier versions the change does not touch.
- The rest is asked in batches within the run's budget (`entities.llm`); what the
  budget leaves unasked is asked by the next run.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from entities.disputes import KeyIndex
from entities.llm import BudgetExceededError, ModelError, Spend, ask_in_batches

logger = logging.getLogger("entities")


def input_hash(*parts: object) -> str:
    """The question's hash; the prompt version is kept beside it, not in it."""
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class AnswerRecord(Protocol):
    key: str
    input_hash: str
    prompt_version: str
    explanation: str


class Classifier[Item, Answer](Protocol):
    @property
    def model(self) -> str: ...

    def classify(self, items: Sequence[Item]) -> dict[int, Answer]: ...


@dataclass(frozen=True)
class AskResult[Answer]:
    answers: dict[int, Answer]
    asked: int
    cached: int
    failures: int
    # Left unasked: the run's budget was spent.
    unasked: int
    cost_usd: float


@dataclass(frozen=True)
class AnswerCache[Answer]:
    """How one step keeps its answers: which table, which field is the answer."""

    record: Any
    field: str
    prompt_version: str
    # An earlier version's answer the prompt's change does not touch.
    accepted: Callable[[str, str, str], bool]
    # A positive answer: kept for the entity whatever its quotes become.
    sticky: Callable[[str], bool]
    # (entity id, the cached value, its explanation) → the answer.
    make: Callable[[int, str, str], Answer]


def cached[Answer](
    session: Session,
    cache: AnswerCache[Answer],
    keys: Mapping[int, str],
    hashes: Mapping[int, str],
) -> dict[int, Answer]:
    """The answers the cache holds for these entities: by the question, else a positive
    answer the entity (by key, resolved to today's) was once given."""
    by_hash: dict[str, Any] = {}
    positive: dict[str, Any] = {}
    records = session.scalars(
        select(cache.record).where(
            cache.record.input_hash.in_(set(hashes.values()))
            | cache.record.key.in_(set(keys.values()))
        )
    )
    for record in records:
        value = getattr(record, cache.field)
        current = record.prompt_version == cache.prompt_version
        if not current and not cache.accepted(record.prompt_version, value, record.explanation):
            continue
        if record.input_hash not in by_hash or current:
            by_hash[record.input_hash] = record
        if cache.sticky(value):
            positive[record.key] = record
    index = KeyIndex(keys.values())
    positive_today: dict[str, Any] = {}
    for key, record in positive.items():
        today = index.today(key)
        if today is not None:
            positive_today[today] = record
    found: dict[int, Answer] = {}
    for group_id, digest in hashes.items():
        record = by_hash.get(digest) or positive_today.get(keys[group_id])
        if record is not None:
            found[group_id] = cache.make(group_id, getattr(record, cache.field), record.explanation)
    return found


def ask_missing[Item, Answer](
    session_factory: sessionmaker[Session],
    cache: AnswerCache[Answer],
    classifier: Classifier[Item, Answer] | None,
    items: Mapping[int, Item],
    keys: Mapping[int, str],
    hashes: Mapping[int, str],
    *,
    batch_size: int,
    concurrency: int,
    value_of: Callable[[Answer], str],
    explanation_of: Callable[[Answer], str],
    on_stage: Callable[[str], None],
    event: str,
    errors: tuple[type[Exception], ...],
) -> AskResult[Answer]:
    """Cached answers, then the rest asked in batches, each batch cached as it comes. A
    failed batch stays unanswered: the next run asks it again."""
    with session_factory() as session:
        answers = cached(session, cache, keys, hashes)
    hits = len(answers)
    missing = [group_id for group_id in items if group_id not in answers]
    spend: Spend | None = getattr(classifier, "spend", None)
    if classifier is None or not missing:
        if missing:
            logger.warning("event=%s_not_asked entities=%d", event, len(missing))
        return AskResult(answers, 0, hits, 0, 0, 0.0)
    batches = [missing[start : start + batch_size] for start in range(0, len(missing), batch_size)]
    asked = failures = unasked = done = 0
    for batch, result in ask_in_batches(
        batches,
        lambda ids: classifier.classify([items[group_id] for group_id in ids]),
        concurrency=concurrency,
        spend=spend,
    ):
        done += len(batch)
        on_stage(f"asking {done}/{len(missing)}")
        if isinstance(result, BudgetExceededError):
            unasked += len(batch)
            continue
        if isinstance(result, Exception):
            if not isinstance(result, (ModelError, *errors)):
                raise result
            failures += len(batch)
            logger.warning("event=%s_batch_failed entities=%d error=%s", event, len(batch), result)
            continue
        failures += len(batch) - len(result)
        if result:
            _store(
                session_factory,
                cache,
                classifier.model,
                result,
                keys,
                hashes,
                value_of,
                explanation_of,
            )
        answers.update(result)
        asked += len(result)
    if unasked:
        logger.warning(
            "event=%s_budget_spent unasked=%d cost_usd=%.4f",
            event,
            unasked,
            spend.cost_usd if spend else 0.0,
        )
    return AskResult(answers, asked, hits, failures, unasked, spend.cost_usd if spend else 0.0)


def _store[Answer](
    session_factory: sessionmaker[Session],
    cache: AnswerCache[Answer],
    model: str,
    answers: Mapping[int, Answer],
    keys: Mapping[int, str],
    hashes: Mapping[int, str],
    value_of: Callable[[Answer], str],
    explanation_of: Callable[[Answer], str],
) -> None:
    rows = [
        {
            "key": keys[group_id],
            "input_hash": hashes[group_id],
            "prompt_version": cache.prompt_version,
            "model": model,
            cache.field: value_of(answer),
            "explanation": explanation_of(answer),
        }
        for group_id, answer in answers.items()
    ]
    with session_factory.begin() as session:
        session.execute(pg_insert(cache.record).values(rows).on_conflict_do_nothing())
