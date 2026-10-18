"""A cached answer is the model's own (`processing.answers`): another model asks again.

16.10.2026: a free model answered «accused» about a man named beside another's case; the
operator chose another model, and the cache — found by the question alone, positive
answers sticking to the person — kept answering «accused» for it, while the new model's
answer to the same question was dropped on the key's conflict."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from processing.answers import ask_missing, input_hash
from processing.roles.roles import ROLE_CACHE, RoleAnswer, RoleClassifierError, RoleItem
from processing.storage.models import EntityRoleAnswerRecord

FREE = "nvidia/nemotron-3.5-super-vl:free"
DEEPSEEK = "deepseek/deepseek-v4.1-flash"
ITEM = RoleItem(1, "Михаил Звинчук", ("…задержание в Черногории основателя канала…",))
KEY = "михаил звинчук"


class _Classifier:
    def __init__(self, model: str, kind: str) -> None:
        self.model = model
        self._kind = kind
        self.asked: list[int] = []

    def classify(self, items: Sequence[RoleItem]) -> dict[int, RoleAnswer]:
        self.asked += [item.id for item in items]
        return {
            item.id: RoleAnswer(id=item.id, source=item.name, kind=self._kind, explanation="—")  # type: ignore[arg-type]
            for item in items
        }


def _ask(
    session_factory: sessionmaker[Session], classifier: _Classifier, quotes: tuple[str, ...]
) -> str:
    item = RoleItem(ITEM.id, ITEM.name, quotes)
    found = ask_missing(
        session_factory,
        ROLE_CACHE,
        classifier,
        {item.id: item},
        {item.id: KEY},
        {item.id: input_hash(item.name, *item.quotes)},
        batch_size=10,
        concurrency=1,
        value_of=lambda answer: answer.kind,
        explanation_of=lambda answer: answer.explanation,
        on_stage=lambda _stage: None,
        event="test_roles",
        errors=(RoleClassifierError,),
    )
    return found.answers[item.id].kind


def test_another_model_asks_again_and_its_answer_is_kept(
    session_factory: sessionmaker[Session],
) -> None:
    free = _Classifier(FREE, "accused")
    deepseek = _Classifier(DEEPSEEK, "foreign")

    assert _ask(session_factory, free, ITEM.quotes) == "accused"
    assert _ask(session_factory, deepseek, ITEM.quotes) == "foreign"
    # Asked again by its own cache: no call.
    assert _ask(session_factory, deepseek, ITEM.quotes) == "foreign"

    assert free.asked == [1] and deepseek.asked == [1]
    with session_factory() as session:
        rows = session.execute(
            select(EntityRoleAnswerRecord.model, EntityRoleAnswerRecord.kind)
        ).all()
    # Both kept: the second model's answer is not dropped on the question's key.
    assert sorted((model, kind) for model, kind in rows) == [
        (DEEPSEEK, "foreign"),
        (FREE, "accused"),
    ]


def test_a_positive_answer_sticks_only_within_its_model(
    session_factory: sessionmaker[Session],
) -> None:
    """New quotes: the model that said «accused» keeps it; another model is asked."""
    free = _Classifier(FREE, "accused")
    deepseek = _Classifier(DEEPSEEK, "foreign")
    _ask(session_factory, free, ITEM.quotes)

    newer = (*ITEM.quotes, "новая публикация")
    assert _ask(session_factory, _Classifier(FREE, "other"), newer) == "accused"
    assert _ask(session_factory, deepseek, newer) == "foreign"
    assert deepseek.asked == [1]


def test_without_a_model_the_newest_answer_wins_every_time(
    session_factory: sessionmaker[Session],
) -> None:
    """No OpenRouter key: any model's answer will do, but the same one each run — the
    newest — not whichever row the database returns last."""
    # Written DeepSeek first, the free model second, but the free one dated older: the
    # table's order and the answers' age disagree, and the age must decide.
    _ask(session_factory, _Classifier(DEEPSEEK, "foreign"), ITEM.quotes)
    _ask(session_factory, _Classifier(FREE, "accused"), ITEM.quotes)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE entity_role_answers SET created_at = created_at - interval '1 day' "
                "WHERE model = :model"
            ),
            {"model": FREE},
        )

    found = [
        ask_missing(
            session_factory,
            ROLE_CACHE,
            None,
            {ITEM.id: ITEM},
            {ITEM.id: KEY},
            {ITEM.id: input_hash(ITEM.name, *ITEM.quotes)},
            batch_size=10,
            concurrency=1,
            value_of=lambda answer: answer.kind,
            explanation_of=lambda answer: answer.explanation,
            on_stage=lambda _stage: None,
            event="test_roles",
            errors=(RoleClassifierError,),
        )
        .answers[ITEM.id]
        .kind
        for _ in range(3)
    ]

    assert found == ["foreign", "foreign", "foreign"]
