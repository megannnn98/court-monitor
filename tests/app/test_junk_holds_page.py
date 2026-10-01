"""«Отсев: на проверке»: the articles the junk screen held back, and a person's word."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import JunkScreenHoldRecord
from monitoring.junk_screen import reason
from web.app import app
from web.dependencies import get_db
from web.ui.junk_holds import PAGE_SIZE, _pages, same_news


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
        return session.scalar(
            text("SELECT status FROM junk_screen_holds WHERE article_id = :id"), {"id": article}
        )


def test_the_held_articles_are_listed_with_why_and_the_queue_counts_them(
    session_factory: sessionmaker[Session],
) -> None:
    article = _held(session_factory, "Сегодня в городе прошла выставка.")

    with _client(session_factory) as client:
        page = client.get("/ui/junk-holds").text
        cycle = client.get("/ui/cycle").text

    assert "<title>Отсев: на проверке</title>" in page
    assert f'href="/ui/articles/{article}">Главные новости &lt;b&gt;дня&lt;/b&gt;</a>' in page
    assert "оценка 0.81 (порог 0.52)" in page and "не доказательство уголовного дела" in page
    assert "На проверке (1)" in page and "Извлечь заново" in page
    assert 'data-primary-task="junk_holds"' in cycle
    assert "Публикации на проверке: 1" in cycle


def test_junk_and_back_again(session_factory: sessionmaker[Session]) -> None:
    article = _held(session_factory, "Сегодня в городе прошла выставка.")

    with _client(session_factory) as client:
        junk = client.post(
            "/ui/junk-holds/junk",
            data={"article": article, "back": "status=held&page=1"},
            follow_redirects=False,
        )
        marked = _status(session_factory, article)
        listed = client.get("/ui/junk-holds", params={"status": "junk"}).text
        back = client.post("/ui/junk-holds/hold", data={"article": article}, follow_redirects=False)
        twice = client.post("/ui/junk-holds/hold", data={"article": article})
        nothing = client.post("/ui/junk-holds/junk", data={"article": "999999"})

    assert junk.status_code == 303
    assert junk.headers["location"] == f"/ui/junk-holds?status=held&page=1#a-{article}"
    assert marked == "junk" and "удалится при следующей очистке" in listed
    assert back.status_code == 303 and _status(session_factory, article) == "held"
    assert twice.status_code == 404 and nothing.status_code == 404


def test_extracting_again_releases_an_article_whose_event_is_found(
    session_factory: sessionmaker[Session],
) -> None:
    article = _held(session_factory, "Суд арестовал Олега Орлова по делу о фейках.")

    with _client(session_factory) as client:
        released = client.post(
            "/ui/junk-holds/reextract", data={"article": article}, follow_redirects=False
        )
        page = client.get(released.headers["location"]).text
        gone = client.post("/ui/junk-holds/reextract", data={"article": article})

    assert released.status_code == 303 and f"released={article}" in released.headers["location"]
    assert _status(session_factory, article) is None
    assert "она возвращена в работу" in page
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
        page = client.get("/ui/junk-holds").text
        assert page.count('<section class="same-news">') == 1
        assert "Похоже на одну новость: 2 публикации." in page
        # The copy stands right after the first, ahead of the unrelated article.
        assert page.index(f'id="a-{first}"') < page.index(f'id="a-{second}"')
        assert page.index(f'id="a-{second}"') < page.index(f'id="a-{other}"')
        assert f'name="articles" value="{first},{second}"' in page

        response = client.post(
            "/ui/junk-holds/junk-all",
            content=f"articles={first},{second}&back=status%3Dheld%26page%3D1",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert _status(session_factory, first) == _status(session_factory, second) == "junk"
        assert _status(session_factory, other) == "held"
        assert '<section class="same-news">' not in client.get("/ui/junk-holds").text


def test_junk_for_all_is_all_or_nothing(session_factory: sessionmaker[Session]) -> None:
    """One article of the story no longer held: the page is stale, nothing is changed."""
    first = _held_as(session_factory, "a", "Первая новость о задержании активиста", 0.9)
    with _client(session_factory) as client:
        response = client.post(
            "/ui/junk-holds/junk-all",
            content=f"articles={first},999999",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert response.status_code == 404
        assert _status(session_factory, first) == "held"
        bad = client.post(
            "/ui/junk-holds/junk-all",
            content="articles=1,x",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert bad.status_code == 400


def test_unrelated_articles_are_not_grouped(session_factory: sessionmaker[Session]) -> None:
    _held_as(session_factory, "a", "Суд в Москве арестовал блогера за пост о войне", 0.9)
    _held_as(session_factory, "b", "В Казани задержали организатора одиночного пикета", 0.8)
    with _client(session_factory) as client:
        assert '<section class="same-news">' not in client.get("/ui/junk-holds").text


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
        page = client.get("/ui/junk-holds").text
    assert page.count('<section class="same-news">') == 1
    assert f'name="articles" value="{first},{second}"' in page
