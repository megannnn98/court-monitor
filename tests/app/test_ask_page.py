"""«Спросить» and «Приговоры»: the question, its answer with the counts, a person's word."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import ArticleSentenceRecord, ChatQuestionRecord
from entities.ask import Call, Plan
from entities.llm import Spend
from web.app import app
from web.dependencies import get_db
from web.ui import ask as ask_page


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


class FakeAsker:
    model = "fake-model"

    def __init__(self, plan: Plan, answer: str) -> None:
        self._plan = plan
        self._answer = answer
        self.spend = Spend()

    def plan(self, question: str) -> Plan:
        self.spend.add(0.002)
        return self._plan

    def answer(self, question: str, results: Sequence[Mapping[str, object]]) -> str:
        return self._answer


def _sentence(article_id: int, person: str, region: str, months: int) -> ArticleSentenceRecord:
    return ArticleSentenceRecord(
        article_id=article_id,
        person=person,
        person_key=person.lower(),
        region=region,
        kind="colony",
        months=months,
        fine_rub=0,
        in_absentia=False,
        sentenced_on="2026-09-24",
        articles=["207.3"],
        reason="antiwar_speech",
        reason_text="посты о войне",
        quote=f"приговорил {person}",
    )


def _seed(session_factory: sessionmaker[Session]) -> int:
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        article, _ = seed.article(
            source, external_id="one", title="Два приговора", text="Суд вынес два приговора."
        )
        session.flush()
        session.add_all(
            [
                _sentence(article, "Петров Иван", "Москва", 84),
                _sentence(article, "Сидоров Олег", "Тульская область", 24),
            ]
        )
    return article


def _ask(client: TestClient, monkeypatch: pytest.MonkeyPatch, asker: Any, question: str) -> Any:
    monkeypatch.setattr(ask_page, "asker_from_env", lambda: asker)
    return client.post("/ui/ask", data={"question": question})


def test_the_answer_is_shown_with_the_counts_it_stands_on(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    article = _seed(session_factory)
    plan = Plan(
        calls=[
            Call(tool="stats", group_by="region", sort="mean_years"),
            Call(tool="list", sort="months", limit=5),
        ]
    )
    asker = FakeAsker(plan, "Суровее всего в Москве: 7 лет.\nВсего дел: 2. См. [№ 5] и [12345].")

    with _client(session_factory) as client:
        page = _ask(client, monkeypatch, asker, "Где суровее?").text
        empty_form = client.get("/ui/ask").text

    assert "<h2>Где суровее?</h2>" in page
    assert "<p>Суровее всего в Москве: 7 лет.</p>" in page
    assert '<a href="/ui/articles/5">[№ 5]</a>' in page
    assert '<a href="/ui/articles/12345">[№ 12345]</a>' in page
    # The counts themselves: the table by region, and the list with its publication.
    assert "приговоры по регионам (все приговоры), по среднему сроку" in page
    assert "<td>Москва</td><td>1</td><td>1</td><td>7</td>" in page
    assert f'<a href="/ui/articles/{article}">Два приговора</a>' in page
    # «5» of the reference is in no count; the rest of the answer's numbers are.
    assert "проверьте по таблицам: 5, 12345." in page
    assert "Сегодня потрачено $0.00 из $1.00" in empty_form
    assert "Где суровее?" in empty_form


def test_groups_too_small_to_rank_stand_apart_in_the_table(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(session_factory)
    plan = Plan(calls=[Call(tool="stats", group_by="region", sort="mean_years", min_imprisoned=2)])

    with _client(session_factory) as client:
        page = _ask(client, monkeypatch, FakeAsker(plan, "Данных мало."), "Где суровее?").text

    apart = page.index("Слишком мало сроков для сравнения")
    assert apart < page.index("<td>Москва</td>") < page.index("<tfoot>")
    assert "групп отброшено как слишком маленькие: 2" in page


def test_a_refusal_and_an_empty_question_are_said(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    refusing = FakeAsker(Plan(calls=[], refusal="Спросите о делах."), "")

    with _client(session_factory) as client:
        refused = _ask(client, monkeypatch, refusing, "Какая погода?").text
        nothing = _ask(client, monkeypatch, refusing, "   ").text
        no_model = _ask(client, monkeypatch, None, "Где суровее?").text

    assert "<p>Спросите о делах.</p>" in refused
    assert "На чём основан ответ" not in refused
    assert '<p class="warning">Вопрос пуст.</p>' in nothing
    assert "не задан ключ OpenRouter" in no_model
    with session_factory() as session:
        assert len(session.scalars(select(ChatQuestionRecord)).all()) == 1


def test_an_earlier_question_is_opened_from_the_list(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(session_factory)
    plan = Plan(calls=[Call(tool="stats", group_by="reason")])

    with _client(session_factory) as client:
        _ask(client, monkeypatch, FakeAsker(plan, "Первый ответ."), "Первый вопрос?")
        _ask(client, monkeypatch, FakeAsker(plan, "Второй ответ."), "Второй вопрос?")
        with session_factory() as session:
            first = min(session.scalars(select(ChatQuestionRecord.id)))
        latest = client.get("/ui/ask").text
        earlier = client.get("/ui/ask", params={"q": first}).text

    assert "<p>Второй ответ.</p>" in latest and "<p>Первый ответ.</p>" not in latest
    assert "<p>Первый ответ.</p>" in earlier
    assert f'href="/ui/ask?q={first}#answer">Первый вопрос?</a>' in latest


def test_found_words_are_marked_and_the_text_is_escaped(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        seed.article(
            source,
            external_id="x",
            title="Пикет <b>у суда</b>",
            text="Активистку задержали на пикете, где 1<2 & 3>2, <script>alert(1)</script> у суда.",
        )
    asker = FakeAsker(Plan(calls=[Call(tool="search", words=["пикет"])]), "<i>Нашлось</i>.")

    with _client(session_factory) as client:
        page = _ask(client, monkeypatch, asker, "Что с пикетами?").text

    assert "<mark>пикете</mark>" in page
    assert "<script>alert(1)</script>" not in page
    assert "1&lt;2 &amp; 3&gt;2" in page
    assert "Пикет &lt;b&gt;у суда&lt;/b&gt;" in page
    assert "<p>&lt;i&gt;Нашлось&lt;/i&gt;.</p>" in page


def test_a_wrong_sentence_is_taken_out_of_the_counts_and_put_back(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(session_factory)
    with session_factory() as session:
        petrov = session.scalars(
            select(ArticleSentenceRecord.id).where(ArticleSentenceRecord.person == "Петров Иван")
        ).one()
    plan = Plan(calls=[Call(tool="stats", group_by="region")])

    with _client(session_factory) as client:
        listed = client.get("/ui/sentences").text
        hidden = client.post(
            "/ui/sentences/hide", data={"rows": str(petrov), "back": "page=1"}
        ).text
        counted = _ask(client, monkeypatch, FakeAsker(plan, "Ответ."), "Где?").text
        apart = client.get("/ui/sentences", params={"view": "hidden"}).text
        shown = client.post("/ui/sentences/show", data={"rows": str(petrov)}).text
        nobody = client.post("/ui/sentences/hide", data={"rows": "999999"})
        partly = client.post("/ui/sentences/hide", data={"rows": f"{petrov},999999"})
        after = client.get("/ui/sentences").text
        nothing = client.post("/ui/sentences/hide", data={"rows": ""})

    assert "Петров Иван" in listed and "Сидоров Олег" in listed
    assert "Найдено: 2." in listed and "Убранные: 0" in listed
    assert "Петров Иван" not in hidden and "Убранные: 1" in hidden
    assert "<td>Москва</td>" not in counted and "<td>Тульская область</td>" in counted
    assert "Петров Иван" in apart and "Вернуть" in apart
    assert "Петров Иван" in shown and "Убранные: 0" in shown
    assert (nobody.status_code, nothing.status_code) == (404, 400)
    # A page older than the rows hides nothing: a part of a case does not go alone.
    assert partly.status_code == 409
    assert "Петров Иван" in after and "Убранные: 0" in after


def test_the_sentences_are_narrowed_by_reason_and_region(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        moscow = client.get("/ui/sentences", params={"region": "Москва"}).text
        religion = client.get("/ui/sentences", params={"reason": "religion"}).text
        menu = client.get("/ui/cycle").text

    assert "Петров Иван" in moscow and "Сидоров Олег" not in moscow
    assert "Таких приговоров нет." in religion
    assert 'href="/ui/ask"' in menu and 'href="/ui/sentences"' in menu
