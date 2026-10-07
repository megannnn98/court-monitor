"""A question answered from the counts, on PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import ArticleSentenceRecord, ChatQuestionRecord
from entities.ask import (
    ANSWERED,
    FAILED,
    REFUSED,
    AskerError,
    Call,
    Plan,
    ask,
    spent_today,
    unverified_numbers,
)
from entities.llm import Spend


class FakeAsker:
    model = "fake-model"

    def __init__(self, plan: Plan, answer: str = "Ответ.", cost: float = 0.01) -> None:
        self._plan = plan
        self._answer = answer
        self._cost = cost
        self.spend = Spend()
        self.results: Sequence[Mapping[str, object]] = []
        self.fail_answer = False

    def plan(self, question: str) -> Plan:
        self.spend.add(self._cost)
        return self._plan

    def answer(self, question: str, results: Sequence[Mapping[str, object]]) -> str:
        if self.fail_answer:
            raise AskerError("HTTP 500")
        self.results = results
        return self._answer


def _sentence(
    article_id: int, person: str, region: str, months: int, **fields: Any
) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "person": person,
        "person_key": person.lower(),
        "region": region,
        "kind": "colony",
        "months": months,
        "fine_rub": 0,
        "in_absentia": False,
        "sentenced_on": "2026-09-24",
        "articles": ["207.3"],
        "reason": "antiwar_speech",
        "reason_text": "посты о войне",
        "quote": "приговорил",
    } | fields


def _seed(session_factory: sessionmaker[Session]) -> list[int]:
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        first, _ = seed.article(
            source,
            external_id="one",
            title="Приговор за пикет",
            text="Активистку задержали на одиночном пикете у здания суда.",
        )
        second, _ = seed.article(
            source, external_id="two", title="Погода", text="В городе выпал первый снег."
        )
        session.flush()
        session.add_all(
            [
                ArticleSentenceRecord(**_sentence(first, "Петров Иван", "Москва", 84)),
                ArticleSentenceRecord(**_sentence(first, "Сидоров Олег", "Москва", 60)),
                ArticleSentenceRecord(**_sentence(second, "Иванова Анна", "Тульская область", 24)),
                ArticleSentenceRecord(
                    **_sentence(second, "Скрытый Человек", "Тульская область", 240, hidden=True)
                ),
            ]
        )
    return [first, second]


def _items(value: object) -> list[dict[str, object]]:
    """A list of rows from a tool's result, checked to be one."""
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return value


def _stats_plan(**fields: Any) -> Plan:
    return Plan(calls=[Call(tool="stats", group_by="region", sort="mean_years", **fields)])


def test_the_model_reads_the_counts_and_the_journal_keeps_them(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    asker = FakeAsker(_stats_plan(reasons=["antiwar_speech"]), "В Москве 6 лет.")

    asked = ask(session_factory, asker, "  Где   суровее? ")

    assert (asked.outcome, asked.answer) == (ANSWERED, "В Москве 6 лет.")
    (result,) = asker.results
    # What a person hid is in no count: Тула's mean is its one visible sentence.
    assert [(g["name"], g["cases"], g["mean_years"]) for g in _items(result["groups"])] == [
        ("Москва", 2, 6.0),
        ("Тульская область", 1, 2.0),
    ]
    with session_factory() as session:
        record = session.scalars(select(ChatQuestionRecord)).one()
        assert record.id == asked.id
        assert (record.question, record.outcome, record.model) == (
            "Где суровее?",
            ANSWERED,
            "fake-model",
        )
        assert record.calls[0]["result"] == result
        assert record.cost_usd == 0.01
        assert spent_today(session) == 0.01


def test_a_group_too_small_to_rank_is_given_apart(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    asker = FakeAsker(_stats_plan(min_imprisoned=2))

    ask(session_factory, asker, "Где суровее?")

    (result,) = asker.results
    assert [group["name"] for group in _items(result["groups"])] == ["Москва"]
    assert [group["name"] for group in _items(result["small_groups"])] == ["Тульская область"]
    assert result["small_groups_left_out"] == 1
    assert result["notes"] == ["Групп, слишком маленьких для сравнения: 1."]


def test_a_question_the_base_does_not_answer_is_refused(
    session_factory: sessionmaker[Session],
) -> None:
    asker = FakeAsker(Plan(calls=[], refusal="Я отвечаю только о делах."))

    asked = ask(session_factory, asker, "Какая погода?")

    assert (asked.outcome, asked.answer) == (REFUSED, "Я отвечаю только о делах.")
    assert asker.results == []


def test_a_models_failure_is_said_and_kept(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    asker = FakeAsker(_stats_plan())
    asker.fail_answer = True

    asked = ask(session_factory, asker, "Где суровее?")

    assert asked.outcome == FAILED
    assert "HTTP 500" in asked.answer
    with session_factory() as session:
        record = session.scalars(select(ChatQuestionRecord)).one()
    # What the counts gave is kept: the page shows it without the model's words.
    assert record.outcome == FAILED and len(record.calls) == 1


def test_past_the_days_budget_nothing_is_asked(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    first = ask(session_factory, FakeAsker(_stats_plan(), cost=0.6), "Первый?", budget_usd=1.0)
    # The choice of counts takes what is left of the day: the answer is not paid for.
    over = FakeAsker(_stats_plan(), cost=0.6)
    second = ask(session_factory, over, "Второй?", budget_usd=1.0)
    third = FakeAsker(_stats_plan())

    asked = ask(session_factory, third, "Третий?", budget_usd=1.0)

    assert first.outcome == ANSWERED
    assert second.outcome == REFUSED and "предел" in second.answer
    assert over.results == [] and second.id is not None
    assert asked.outcome == REFUSED and "предел" in asked.answer
    assert (third.spend.calls, asked.id) == (0, None)
    with session_factory() as session:
        assert len(session.scalars(select(ChatQuestionRecord)).all()) == 2


def test_without_a_model_or_a_question_nothing_is_written(
    session_factory: sessionmaker[Session],
) -> None:
    assert ask(session_factory, None, "Где суровее?").outcome == FAILED
    assert ask(session_factory, FakeAsker(_stats_plan()), "   ").outcome == REFUSED
    with session_factory() as session:
        assert session.scalars(select(ChatQuestionRecord)).all() == []


def test_no_more_than_three_counts_are_run(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    asker = FakeAsker(Plan(calls=[Call(tool="stats", group_by="region")] * 5))

    ask(session_factory, asker, "Где суровее?")

    assert len(asker.results) == 3


def test_a_list_gives_the_cases_with_their_publication(
    session_factory: sessionmaker[Session],
) -> None:
    first, _ = _seed(session_factory)
    asker = FakeAsker(Plan(calls=[Call(tool="list", sort="months", limit=1, region="Москва")]))

    ask(session_factory, asker, "Самый большой срок в Москве?")

    (result,) = asker.results
    assert result["total"] == 2
    (case,) = _items(result["cases"])
    assert (case["person"], case["years"], case["article_id"]) == ("Петров Иван", 7.0, first)
    assert case["punishment"] == "лишение свободы"


def test_a_region_the_model_wrote_loosely_is_still_the_region(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    asker = FakeAsker(Plan(calls=[Call(tool="list", region="г. Москва")]))

    ask(session_factory, asker, "Приговоры в Москве?")

    assert asker.results[0]["total"] == 2
    assert "регион: Москва" in str(asker.results[0]["what"])


def test_a_search_finds_the_articles_by_any_form_of_a_word(
    session_factory: sessionmaker[Session],
) -> None:
    first, _ = _seed(session_factory)
    asker = FakeAsker(Plan(calls=[Call(tool="search", words=["пикет", "митинг"])]))

    ask(session_factory, asker, "Кого задерживали на пикетах?")

    (result,) = asker.results
    assert result["total"] == 1
    (found,) = _items(result["publications"])
    assert found["article_id"] == first
    snippet = found["snippet"]
    assert isinstance(snippet, str)
    assert "[[пикете]]" in snippet


def test_a_search_without_words_finds_nothing(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    asker = FakeAsker(Plan(calls=[Call(tool="search", words=[" "])]))

    ask(session_factory, asker, "Что?")

    assert asker.results[0]["total"] == 0


def test_a_number_the_counts_do_not_hold_is_named() -> None:
    results = [{"groups": [{"name": "Москва", "cases": 12, "mean_years": 6.5, "max_years": 10.0}]}]

    assert unverified_numbers("В Москве 12 дел, в среднем 6,5 года, до 10 лет.", "", results) == []
    assert unverified_numbers("В Москве 13 дел и 78 месяцев.", "", results) == ["13", "78"]
    # A number of the question itself is the operator's, not the model's.
    assert unverified_numbers("За 2026 год — 12 дел.", "Что было в 2026 году?", results) == []
