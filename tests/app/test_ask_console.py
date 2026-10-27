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

from llm.chat import Spend
from processing.storage.models import ArticleSentenceRecord
from results.ask import Call, Plan
from results.storage.models import ChatQuestionRecord
from web.app import app
from web.dependencies import get_db
from web.views import ask as ask_views


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


def _ask(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, asker: Any, question: str
) -> dict[str, Any]:
    """Asked as the React page asks: the question, then the answer it was given."""
    monkeypatch.setattr(ask_views, "asker_from_env", lambda: asker)
    asked: dict[str, Any] = client.post("/api/v1/ask", json={"question": question}).json()
    if asked["id"] is None:
        return asked
    answer: dict[str, Any] = client.get("/api/v1/ask", params={"q": asked["id"]}).json()["answer"]
    return answer


def _result(answer: dict[str, Any], tool: str) -> dict[str, Any]:
    found: dict[str, Any] = next(result for result in answer["results"] if result["tool"] == tool)
    return found


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
        before = client.get("/api/v1/ask").json()
        answer = _ask(client, monkeypatch, asker, "Где суровее?")
        after = client.get("/api/v1/ask").json()

    assert answer["question"] == "Где суровее?" and answer["outcome"] == "answered"
    assert answer["answer"] == "Суровее всего в Москве: 7 лет.\nВсего дел: 2. См. [№ 5] и [12345]."
    # The counts themselves: the table by region, and the list with its publication.
    stats = _result(answer, "stats")
    assert stats["what"] == "приговоры по регионам (все приговоры), по среднему сроку"
    moscow = next(group for group in stats["groups"] if group["name"] == "Москва")
    assert (moscow["cases"], moscow["imprisoned"], moscow["mean_years"]) == (1, 1, 7.0)
    listed = _result(answer, "list")
    assert {(case["article_id"], case["title"]) for case in listed["cases"]} == {
        (article, "Два приговора")
    }
    # «5» of the reference is in no count; the rest of the answer's numbers are.
    assert answer["unverified"] == ["5", "12345"]
    assert (before["spent_today"], before["budget"]) == (0.0, 1.0)
    # The question asked is in the list of those asked before.
    assert [one["question"] for one in after["history"]] == ["Где суровее?"]


def test_groups_too_small_to_rank_stand_apart_in_the_table(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(session_factory)
    plan = Plan(calls=[Call(tool="stats", group_by="region", sort="mean_years", min_imprisoned=2)])

    with _client(session_factory) as client:
        answer = _ask(client, monkeypatch, FakeAsker(plan, "Данных мало."), "Где суровее?")

    stats = _result(answer, "stats")
    # Too few terms to rank: the groups stand apart from the ranked ones, and are said.
    assert stats["groups"] == []
    assert [group["name"] for group in stats["small_groups"]] == ["Москва", "Тульская область"]
    assert stats["small_groups_left_out"] == 2
    assert stats["notes"] == ["Групп, слишком маленьких для сравнения: 2."]


def test_a_refusal_and_an_empty_question_are_said(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    refusing = FakeAsker(Plan(calls=[], refusal="Спросите о делах."), "")

    with _client(session_factory) as client:
        refused = _ask(client, monkeypatch, refusing, "Какая погода?")
        nothing = _ask(client, monkeypatch, refusing, "   ")
        no_model = _ask(client, monkeypatch, None, "Где суровее?")

    assert refused["outcome"] == "refused" and refused["answer"] == "Спросите о делах."
    assert refused["results"] == []
    assert nothing == {"id": None, "note": "Вопрос пуст."}
    assert no_model["id"] is None and "не задан ключ OpenRouter" in no_model["note"]
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
        latest = client.get("/api/v1/ask").json()
        earlier = client.get("/api/v1/ask", params={"q": first}).json()

    assert latest["answer"]["answer"] == "Второй ответ."
    assert earlier["answer"]["answer"] == "Первый ответ."
    assert [(one["id"], one["question"]) for one in latest["history"]][-1] == (
        first,
        "Первый вопрос?",
    )


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
        page = client.get("/api/v1/ask").json()
        answer = _ask(client, monkeypatch, asker, "Что с пикетами?")

    # The found word is marked with the API's own marks; the text is as written, for
    # the page to escape.
    (found,) = _result(answer, "search")["publications"]
    assert f"{page['mark_open']}пикете{page['mark_close']}" in found["snippet"]
    assert found["title"] == "Пикет <b>у суда</b>"
    assert answer["answer"] == "<i>Нашлось</i>."


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
        counted = [
            group["name"]
            for group in _result(
                _ask(client, monkeypatch, FakeAsker(plan, "Ответ."), "Где?"), "stats"
            )["groups"]
        ]
        apart = client.get("/ui/sentences", params={"view": "hidden"}).text
        shown = client.post("/ui/sentences/show", data={"rows": str(petrov)}).text
        nobody = client.post("/ui/sentences/hide", data={"rows": "999999"})
        partly = client.post("/ui/sentences/hide", data={"rows": f"{petrov},999999"})
        after = client.get("/ui/sentences").text
        nothing = client.post("/ui/sentences/hide", data={"rows": ""})

    assert "Петров Иван" in listed and "Сидоров Олег" in listed
    assert "Найдено: 2." in listed and "Убранные: 0" in listed
    assert "Петров Иван" not in hidden and "Убранные: 1" in hidden
    assert counted == ["Тульская область"]
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
        menu = client.get("/ui/logs").text

    assert "Петров Иван" in moscow and "Сидоров Олег" not in moscow
    assert "Таких приговоров нет." in religion
    assert 'href="/ui/ask"' in menu and 'href="/ui/sentences"' in menu


def test_the_api_asks_and_reads_the_answer_the_page_does(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/api/v1/ask` for the React page: the same answer, counts and history."""
    _seed(session_factory)
    plan = Plan(calls=[Call(tool="stats", group_by="region", sort="mean_years")])
    monkeypatch.setattr(
        ask_views, "asker_from_env", lambda: FakeAsker(plan, "В Москве 7 лет. См. [№ 5].")
    )

    with _client(session_factory) as client:
        before = client.get("/api/v1/ask").json()
        asked = client.post("/api/v1/ask", json={"question": "Где суровее?"}).json()
        page = client.get("/api/v1/ask", params={"q": asked["id"]}).json()
        empty = client.post("/api/v1/ask", json={"question": "  "}).json()
        foreign = client.post(
            "/api/v1/ask", json={"question": "Где?"}, headers={"Origin": "https://evil.example"}
        )

    assert before["answer"] is None and before["history"] == []
    assert before["budget"] == 1.0 and before["examples"]
    answer = page["answer"]
    assert answer["question"] == "Где суровее?" and answer["answer"] == "В Москве 7 лет. См. [№ 5]."
    assert answer["results"][0]["tool"] == "stats"
    assert answer["results"][0]["groups"][0]["name"] == "Москва"
    assert answer["unverified"] == ["5"]
    assert [one["question"] for one in page["history"]] == ["Где суровее?"]
    assert empty == {"id": None, "note": "Вопрос пуст."}
    assert foreign.status_code == 403
