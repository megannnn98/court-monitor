"""«Отсев: на проверке»: the articles the junk screen held back, and a person's word.

Read and taken through `/api/v1/junk-holds`, as the React page does."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from processing.junk.hold_reader import add_event
from processing.junk.purge import JunkPurge
from processing.junk.screen import reason
from processing.storage.models import JunkScreenHoldRecord
from web.app import app
from web.dependencies import get_db
from web.ui.run_cards import _holds_read
from web.views.junk_holds import PAGE_SIZE, _pages, same_news
from web.views.workload import workload

API = "/api/v1/junk-holds"


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


def _held(session_factory: sessionmaker[Session], body: str) -> int:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        article, _run = seed.article(
            source,
            external_id="digest",
            title="Главные новости <b>дня</b>",
            text=body,
            published_at=datetime(2026, 9, 25, tzinfo=UTC),
        )
        session.add(
            JunkScreenHoldRecord(
                article_id=article,
                status="held",
                score=0.81,
                cutoff=0.52,
                screen="junk-screen-v1:e5",
                reason=reason(0.81, 0.52),
            )
        )
        session.commit()
        return article


def _status(session_factory: sessionmaker[Session], article: int) -> str | None:
    with session_factory() as session:
        status: str | None = session.scalar(
            text("SELECT status FROM junk_screen_holds WHERE article_id = :id"), {"id": article}
        )
        return status


def _list(client: TestClient, status: str | None = None) -> dict[str, Any]:
    params = {} if status is None else {"status": status}
    listed: dict[str, Any] = client.get(API, params=params).json()
    return listed


def _stories(listed: dict[str, Any]) -> list[list[int]]:
    return [[article["article_id"] for article in story["articles"]] for story in listed["stories"]]


def _cards(listed: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        article["article_id"]: article
        for story in listed["stories"]
        for article in story["articles"]
    }


def _counts(listed: dict[str, Any]) -> dict[str, int]:
    return {option["value"]: option["count"] for option in listed["statuses"]}


def test_the_held_articles_are_listed_with_why_and_the_queue_counts_them(
    session_factory: sessionmaker[Session],
) -> None:
    article = _held(session_factory, "Сегодня в городе прошла выставка.")

    with _client(session_factory) as client:
        listed = _list(client)
    with session_factory() as session:
        queue = workload(session).junk_holds

    card = _cards(listed)[article]
    assert card["title"] == "Главные новости <b>дня</b>"
    assert (card["score"], card["cutoff"]) == (0.81, 0.52)
    assert "не доказательство уголовного дела" in card["reason"]
    assert listed["status"] == "held" and _counts(listed)["held"] == 1
    assert queue == 1


def test_junk_and_back_again(session_factory: sessionmaker[Session]) -> None:
    article = _held(session_factory, "Сегодня в городе прошла выставка.")

    with _client(session_factory) as client:
        junk = client.post(f"{API}/junk", json={"article": article})
        marked = _status(session_factory, article)
        listed = _list(client, "junk")
        back = client.post(f"{API}/hold", json={"article": article})
        twice = client.post(f"{API}/hold", json={"article": article})
        nothing = client.post(f"{API}/junk", json={"article": 999999})

    assert junk.status_code == 200 and marked == "junk"
    assert _stories(listed) == [[article]]
    assert back.status_code == 200 and _status(session_factory, article) == "held"
    assert twice.status_code == 404 and nothing.status_code == 404


def test_extracting_again_releases_an_article_whose_event_is_found(
    session_factory: sessionmaker[Session],
) -> None:
    article = _held(session_factory, "Суд арестовал Олега Орлова по делу о фейках.")

    with _client(session_factory) as client:
        released = client.post(f"{API}/reextract", json={"article": article})
        gone = client.post(f"{API}/reextract", json={"article": article})

    assert released.status_code == 200 and released.json()["released"] is True
    assert _status(session_factory, article) is None
    assert gone.status_code == 404


def _held_as(
    session_factory: sessionmaker[Session], external_id: str, title: str, score: float
) -> int:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source(f"news-{external_id}", f"https://{external_id}.example.test")
        article, _run = seed.article(
            source,
            external_id=external_id,
            title=title,
            text=f"{title}. Подробности.",
            published_at=datetime(2026, 9, 25, tzinfo=UTC),
        )
        session.add(
            JunkScreenHoldRecord(
                article_id=article,
                status="held",
                score=score,
                cutoff=0.52,
                screen="junk-screen-v1:e5",
                reason=reason(score, 0.52),
            )
        )
        session.commit()
        return article


def test_the_grouping_chains_pairs_and_keeps_the_order() -> None:
    """A with B and B with C is one story of three; it stands where its first stood."""
    assert same_news([5, 1, 9, 3, 7], [(1, 3), (3, 7), (8, 9)]) == [[5], [1, 3, 7], [9]]
    # Two stories found apart and then joined through their last members.
    assert same_news([1, 3, 7], [(1, 3), (7, 3)]) == [[1, 3, 7]]
    assert same_news([], [(1, 2)]) == []


def test_a_story_is_never_cut_by_a_page_break() -> None:
    stories = [[n] for n in range(PAGE_SIZE - 1)] + [[100, 101, 102], [200]]
    pages = _pages(stories)
    assert [100, 101, 102] in pages[0] and pages[1] == [[200]]
    assert _pages([]) == []


def test_the_same_news_from_several_sources_stands_together_and_goes_together(
    session_factory: sessionmaker[Session],
) -> None:
    """Sources copy one another. Ирина reads the story once and dismisses every copy."""
    title = "Следователи СК России будут расследовать очередные преступления против жителей"
    first = _held_as(session_factory, "a", title + " Брянской области", 0.9)
    other = _held_as(session_factory, "b", "Суд оштрафовал жителя Анапы за репост", 0.8)
    second = _held_as(session_factory, "c", title + " Белгородской области", 0.7)
    with _client(session_factory) as client:
        # The copy stands right after the first, ahead of the unrelated article.
        assert _stories(_list(client)) == [[first, second], [other]]

        response = client.post(f"{API}/junk-all", json={"articles": [first, second]})
        assert response.status_code == 200
        assert _status(session_factory, first) == _status(session_factory, second) == "junk"
        assert _status(session_factory, other) == "held"
        assert _stories(_list(client)) == [[other]]


def test_junk_for_all_is_all_or_nothing(session_factory: sessionmaker[Session]) -> None:
    """One article of the story no longer held: the page is stale, nothing is changed."""
    first = _held_as(session_factory, "a", "Первая новость о задержании активиста", 0.9)
    with _client(session_factory) as client:
        response = client.post(f"{API}/junk-all", json={"articles": [first, 999999]})
    assert response.status_code == 404
    assert _status(session_factory, first) == "held"


def test_unrelated_articles_are_not_grouped(session_factory: sessionmaker[Session]) -> None:
    first = _held_as(session_factory, "a", "Суд в Москве арестовал блогера за пост о войне", 0.9)
    second = _held_as(
        session_factory, "b", "В Казани задержали организатора одиночного пикета", 0.8
    )
    with _client(session_factory) as client:
        assert _stories(_list(client)) == [[first], [second]]


def _held_naming(
    session_factory: sessionmaker[Session], external_id: str, title: str, *people: str
) -> int:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source(f"news-{external_id}", f"https://{external_id}.example.test")
        article, run = seed.article(
            source,
            external_id=external_id,
            title=title,
            text=f"{title}. {'. '.join(people)}.",
            published_at=datetime(2026, 9, 25, tzinfo=UTC),
        )
        for person in people:
            seed.mention(run, person, person_id=None)
        session.add(
            JunkScreenHoldRecord(
                article_id=article,
                status="held",
                score=0.8,
                cutoff=0.52,
                screen="junk-screen-v1:e5",
                reason=reason(0.8, 0.52),
            )
        )
        session.commit()
        return article


def test_two_articles_naming_one_person_are_one_story_and_a_surname_alone_is_not(
    session_factory: sessionmaker[Session],
) -> None:
    """Different words about one person («перевели в больницу», «экстренно госпитализирован»)
    are the same news. A bare surname names half the country and joins nobody."""
    first = _held_naming(
        session_factory, "a", "Историка перевели из колонии в больницу", "Юрий Дмитриев"
    )
    second = _held_naming(
        session_factory, "b", "Экстренная госпитализация главы «Мемориала»", "Юрий Дмитриев"
    )
    _held_naming(session_factory, "c", "Суд вынес решение по иску к мэрии", "Дмитриев")
    _held_naming(session_factory, "d", "Прокурор запросил срок по делу о репосте", "Дмитриев")
    with _client(session_factory) as client:
        stories = _stories(_list(client))
    assert [story for story in stories if len(story) > 1] == [[first, second]]


def _read(session_factory: sessionmaker[Session]) -> dict[str, int]:
    """Four articles, as a model's reading left them: unread, its junk twice, a release."""
    ids = {}
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for name, status, verdict, note in (
            ("unread", "held", None, ""),
            ("bribe", "held", "junk", "взятки, обычное уголовное дело"),
            ("fine", "held", "junk", "штраф по КоАП"),
            ("donation", "released", "case", "приговор за донаты"),
        ):
            ids[name], _run = seed.article(
                source, external_id=name, title=f"Заголовок {name}", text=f"Текст {name}."
            )
            session.add(
                JunkScreenHoldRecord(
                    article_id=ids[name],
                    status=status,
                    score=0.8,
                    cutoff=0.5,
                    screen="s",
                    reason="r",
                    note=note,
                    reader="hold-reader-v1:m" if verdict else None,
                    reader_verdict=verdict,
                    reader_event="sentence" if verdict == "case" else None,
                )
            )
        session.commit()
    with session_factory.begin() as session:
        add_event(session, ids["donation"], "sentence", "приговор за донаты")
    return ids


def _model_events(session_factory: sessionmaker[Session], article: int) -> list[str]:
    with session_factory() as session:
        return list(
            session.scalars(
                text(
                    "SELECT e.event_type FROM extracted_events e JOIN article_extraction_runs r "
                    "ON r.id = e.extraction_run_id WHERE r.article_id = :id "
                    "AND e.extractor_name = 'hold-reader'"
                ),
                {"id": article},
            )
        )


def _titles(listed: dict[str, Any]) -> list[str]:
    return [
        article["title"].removeprefix("Заголовок ")
        for story in listed["stories"]
        for article in story["articles"]
    ]


def test_the_held_are_four_lists_and_the_model_s_junk_goes_by_one_press(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _read(session_factory)

    with _client(session_factory) as client:
        lists = {view: _list(client, view) for view in ("held", "model_junk", "released", "junk")}
        gone = client.post(f"{API}/junk-all", json={"articles": lists["model_junk"]["article_ids"]})
        after = _list(client, "model_junk")

    assert {view: _titles(listed) for view, listed in lists.items()} == {
        "held": ["unread"],
        "model_junk": ["bribe", "fine"],
        "released": ["donation"],
        "junk": [],
    }
    assert _counts(lists["held"]) == {"held": 1, "model_junk": 2, "released": 1, "junk": 0}
    # The model's reason, as the model's.
    bribe = _cards(lists["model_junk"])[ids["bribe"]]
    assert (bribe["note_label"], bribe["note"]) == (
        "Модель: в работу не брать — ",
        "взятки, обычное уголовное дело",
    )
    donation = _cards(lists["released"])[ids["donation"]]
    assert (donation["note_label"], donation["note"]) == ("Модель: дело — ", "приговор за донаты")
    # «Мусор — все» on the model's list takes every article of it.
    assert lists["model_junk"]["article_ids"] == [ids["bribe"], ids["fine"]]
    assert gone.status_code == 200
    assert _counts(after)["model_junk"] == 0 and _counts(after)["junk"] == 2


def test_a_person_takes_a_case_on_against_the_model_and_undoes_a_release(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _read(session_factory)

    with _client(session_factory) as client:
        taken = client.post(f"{API}/release", json={"article": ids["fine"]})
        twice = client.post(f"{API}/release", json={"article": ids["fine"]})
        fine = _cards(_list(client, "released"))[ids["fine"]]
        undone = client.post(f"{API}/unrelease", json={"article": ids["donation"]})
        not_released = client.post(f"{API}/unrelease", json={"article": ids["bribe"]})

    assert (taken.status_code, twice.status_code, undone.status_code) == (200, 404, 200)
    assert not_released.status_code == 404 and _status(session_factory, ids["bribe"]) == "held"
    # A person's release is an event too: the later steps take an article by its event.
    assert _status(session_factory, ids["fine"]) == "released"
    assert _model_events(session_factory, ids["fine"]) == ["case_opened"]
    # The word is the person's: the card no longer says what the model thought of it.
    assert (fine["note_label"], fine["note"]) == ("", "Выпущено в работу оператором.")
    # A release undone takes its event back, or the purge would never see the article.
    assert _status(session_factory, ids["donation"]) == "junk"
    assert _model_events(session_factory, ids["donation"]) == []
    JunkPurge(session_factory).run()
    with session_factory() as session:
        left = set(session.scalars(text("SELECT id FROM parsed_articles")))
    assert left == {ids["unread"], ids["bribe"], ids["fine"]}


def test_the_purge_s_card_says_what_the_model_read() -> None:
    log = "event=holds_read HoldsRead(released=24, model_junk=43, failures=0, restored=0)"

    marks = "".join(_holds_read(log))

    assert 'href="/ui/junk-holds?status=released"' in marks
    assert "Модель выпустила в работу: 24" in marks and "Модель считает мусором: 43" in marks
    assert _holds_read("event=junk_purge_started total=0") == []


def test_every_held_article_is_counted_and_the_unread_list_opens_first(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _read(session_factory)

    with session_factory() as session:
        with_unread = workload(session).junk_holds
    with _client(session_factory) as client:
        opened_with_unread = _list(client)
        client.post(f"{API}/junk", json={"article": ids["unread"]})
        opened = _list(client)
        client.post(f"{API}/junk-all", json={"articles": [ids["bribe"], ids["fine"]]})
    with session_factory() as session:
        nothing_left = workload(session).junk_holds

    # One the model did not answer about and the two it calls junk: three publications.
    assert with_unread == 3
    assert _titles(opened_with_unread) == ["unread"]
    # Nothing unread: the queue's link opens the list there is work in.
    assert opened["status"] == "model_junk" and _titles(opened) == ["bribe", "fine"]
    assert nothing_left == 0


def test_two_articles_a_model_calls_junk_count_as_two(
    session_factory: sessionmaker[Session],
) -> None:
    """The queue says how many publications wait, not «one piece of work» for all the
    model's: eighteen of them showed as «1»."""
    ids = _read(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("DELETE FROM junk_screen_holds WHERE article_id = :id"), {"id": ids["unread"]}
        )

    with session_factory() as session:
        assert workload(session).junk_holds == 2


def test_a_page_opened_before_a_release_cannot_undo_it(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _read(session_factory)

    with _client(session_factory) as client:
        # The page of the model's junk is open; meanwhile another person takes one on.
        client.post(f"{API}/release", json={"article": ids["fine"]})
        stale_all = client.post(f"{API}/junk-all", json={"articles": [ids["bribe"], ids["fine"]]})
        stale_one = client.post(f"{API}/junk", json={"article": ids["fine"]})

    # All or nothing, and nothing: the released keeps its event, the other stays held.
    assert (stale_all.status_code, stale_one.status_code) == (404, 404)
    assert _status(session_factory, ids["fine"]) == "released"
    assert _model_events(session_factory, ids["fine"]) == ["case_opened"]
    assert _status(session_factory, ids["bribe"]) == "held"


def test_back_to_the_check_is_read_anew(session_factory: sessionmaker[Session]) -> None:
    ids = _read(session_factory)

    with _client(session_factory) as client:
        client.post(f"{API}/unrelease", json={"article": ids["donation"]})
        client.post(f"{API}/junk", json={"article": ids["bribe"]})
        for name in ("donation", "bribe"):
            client.post(f"{API}/hold", json={"article": ids[name]})
        held = _list(client, "held")
    with session_factory() as session:
        unread = set(
            session.scalars(
                text(
                    "SELECT article_id FROM junk_screen_holds WHERE status = 'held' AND reader IS NULL"
                )
            )
        )

    # What a model said before goes with the person's «Мусор»: both wait to be read, in
    # the list of the unread — neither stuck with an old verdict nor back among its junk.
    assert unread == {ids["unread"], ids["donation"], ids["bribe"]}
    assert sorted(_titles(held)) == ["bribe", "donation", "unread"]


def test_an_article_with_no_extraction_to_write_to_is_not_released(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _read(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE article_extraction_runs SET status = 'failed' WHERE article_id = :id"),
            {"id": ids["fine"]},
        )

    with _client(session_factory) as client:
        refused = client.post(f"{API}/release", json={"article": ids["fine"]})

    # Released without its event it would be in no list and no step, and never purged.
    assert refused.status_code == 404
    assert _status(session_factory, ids["fine"]) == "held"
    assert _model_events(session_factory, ids["fine"]) == []


def test_the_api_reads_the_stories_and_refuses_what_it_should(
    session_factory: sessionmaker[Session],
) -> None:
    first = _held_as(session_factory, "a", "Суд арестовал активиста Ивана Петрова за пост", 0.9)
    second = _held_as(session_factory, "b", "Суд арестовал активиста Ивана Петрова за посты", 0.8)
    alone = _held(session_factory, "Сегодня в городе прошла выставка.")

    with _client(session_factory) as client:
        held = _list(client)
        junk = client.post(f"{API}/junk", json={"article": alone})
        as_junk = _list(client, "junk")
        twice = client.post(f"{API}/junk", json={"article": alone})
        stale = client.post(f"{API}/junk-all", json={"articles": [first, 999999]})
        after_stale = _status(session_factory, first)
        # A list longer than any page: it reaches the action, which takes all or none.
        many = [first, second, *range(1_000_000, 1_005_000)]
        long_stale = client.post(f"{API}/junk-all", json={"articles": many})
        after_long = _status(session_factory, second)
        foreign = client.post(
            f"{API}/hold", json={"article": alone}, headers={"Origin": "https://evil.example"}
        )
        back = client.post(f"{API}/hold", json={"article": alone})
        both = client.post(f"{API}/junk-all", json={"articles": [first, second]})

    assert held["status"] == "held" and _counts(held)["held"] == 3
    assert _stories(held) == [[first, second], [alone]]
    card = _cards(held)[alone]
    assert card["title"] == "Главные новости <b>дня</b>" and card["score"] == 0.81
    assert card["url"].startswith("https://") and card["status"] == "held"
    assert junk.json() == {"articles": [alone], "released": False}
    assert _stories(as_junk) == [[alone]]
    assert twice.status_code == 404 and twice.json()["detail"] == "Статья не на проверке"
    assert stale.status_code == 404 and after_stale == "held"
    assert (long_stale.status_code, after_long) == (404, "held")
    assert foreign.status_code == 403
    assert back.status_code == 200 and _status(session_factory, alone) == "held"
    assert both.status_code == 200 and _status(session_factory, second) == "junk"
