"""«Следователь»: the overview, the dossier of a person, the people, the publications and the
operator's queue, on PostgreSQL with real rows."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import (
    EntityGroupPoliticsRecord,
    EntityGroupRfMatchRecord,
    EntityGroupRoleRecord,
    EntityMentionRecord,
    EntityPairDecisionRecord,
)
from entities.collector import EntityCollector
from operator_console import OperationRegistry
from web.app import app
from web.dependencies import get_db, get_operation_registry

MOOR = "александр моор"
ARREST = "Суд арестовал Александра Моора по ч. 2 ст. 205.2 УК РФ."
QUOTE = "Александра Моора арестовали за антивоенные посты"


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_operation_registry] = lambda: registry
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_operation_registry, None)


def _person(
    session: Session, seed: DatabaseSeeder, run: int, surface: str, first: str, last: str
) -> int:
    mention_id = seed.mention(run, surface, person_id=None)
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": None,
    }
    return mention_id


def _law(session: Session, seed: DatabaseSeeder, run: int, surface: str, article: str) -> int:
    mention_id = seed.mention(run, surface, person_id=None, entity_type="legal_reference")
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "code": "УК РФ",
        "article": article,
        "part": "2",
        "clause": None,
    }
    return mention_id


def _case(session_factory: sessionmaker[Session], *, extra: int = 0) -> None:
    """Моор's case: a sentence on 10.09 (seeded first), an arrest on 01.09 told by two
    publications, a court, an article of the Code, a witness; `extra` more publications,
    each with a person of its own and an event on a day of its own."""
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("ОВД-Инфо", "https://ovd.info")
        _, run = seed.article(
            source,
            external_id="sentence",
            title="Приговор Моору",
            text="Александру Моору вынесли приговор. Иван Иванов дал показания.",
            published_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
        )
        moor = _person(session, seed, run, "Александру Моору", "Александр", "Моору")
        _person(session, seed, run, "Иван Иванов", "Иван", "Иванов")
        seed.event(
            run,
            "Александру Моору вынесли приговор.",
            event_type="sentence",
            event_date=datetime(2026, 9, 10, 12, tzinfo=UTC),
            links=[],
            entity_links=[(moor, "target")],
        )
        text_ = f"{ARREST} Ленинский суд. {QUOTE}."
        _, run = seed.article(
            source,
            external_id="arrest",
            title="Арест Моора",
            text=text_,
            published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        )
        moor = _person(session, seed, run, "Александра Моора", "Александр", "Моора")
        court = seed.mention(run, "Ленинский суд", person_id=None, entity_type="court")
        seed.event(
            run,
            ARREST,
            event_type="arrest",
            event_date=datetime(2026, 9, 1, 9, tzinfo=UTC),
            links=[],
            entity_links=[
                (moor, "target"),
                (_law(session, seed, run, "ч. 2 ст. 205.2 УК РФ", "205.2"), "legal_basis"),
                (court, "court"),
            ],
        )
        _, run = seed.article(
            source,
            external_id="arrest-again",
            title="Моора арестовали",
            text="Суд отправил Александра Моора под арест.",
            published_at=datetime(2026, 9, 1, 18, tzinfo=UTC),
        )
        moor = _person(session, seed, run, "Александра Моора", "Александр", "Моора")
        seed.event(
            run,
            "Суд отправил Александра Моора под арест.",
            event_type="arrest",
            event_date=datetime(2026, 9, 1, 18, tzinfo=UTC),
            links=[],
            entity_links=[(moor, "target")],
        )
        session.commit()
    _more(session_factory, extra)


def _more(
    session_factory: sessionmaker[Session], extra: int, source_name: str = "Медиазона"
) -> None:
    """`extra` more publications of Моор's, each with a person of its own and a search on a
    day of its own; then the entities rebuilt and judged again (a rebuild drops the role
    and the verdict, as step 3 does)."""
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = (
            seed.source(source_name, f"https://{quote(source_name)}.example.test") if extra else 0
        )
        for number in range(extra):
            other = f"Петр Сидоров{number}"
            _, run = seed.article(
                source,
                external_id=f"{source_name}-{number}",
                title=f"Обыск {number}",
                text=f"У Александра Моора и {other} прошёл обыск по ст. {300 + number} УК РФ.",
                published_at=datetime(2026, 8, 1 + number % 28, tzinfo=UTC),
            )
            moor = _person(session, seed, run, "Александра Моора", "Александр", "Моора")
            _person(session, seed, run, other, "Петр", f"Сидоров{number}")
            seed.event(
                run,
                "прошёл обыск",
                event_type="search",
                event_date=datetime(2026, 8, 1 + number % 28, tzinfo=UTC),
                links=[],
                entity_links=[
                    (moor, "target"),
                    (
                        _law(session, seed, run, f"ст. {300 + number} УК РФ", str(300 + number)),
                        "legal_basis",
                    ),
                ],
            )
        session.commit()
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        group = session.scalar(text("SELECT id FROM entity_groups WHERE key = :key"), {"key": MOOR})
        session.add(
            EntityGroupRoleRecord(
                group_id=group,
                role="figurant",
                kind="accused",
                method="model",
                reason="Арестован по уголовному делу.",
                quote=ARREST,
            )
        )
        session.add(
            EntityGroupPoliticsRecord(
                group_id=group,
                verdict="political",
                method="model",
                reason="Преследование за антивоенные посты.",
                quote=QUOTE,
            )
        )


def _listed(session_factory: sessionmaker[Session], level: str) -> None:
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        entry = seed.entry(seed.snapshot(), "МООР АЛЕКСАНДР ПЕТРОВИЧ")
        group = session.scalar(text("SELECT id FROM entity_groups WHERE key = :key"), {"key": MOOR})
        session.add(EntityGroupRfMatchRecord(group_id=group, entry_id=entry, level=level))


def _news(session_factory: sessionmaker[Session], key: str, kind: str, reason: str) -> None:
    with session_factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO entity_group_news (group_id, kind, method, reason, quote, "
                "published_at) SELECT id, :kind, 'model', :reason, '', last_published_at "
                "FROM entity_groups WHERE key = :key"
            ),
            {"key": key, "kind": kind, "reason": reason},
        )


def test_the_overview_is_what_is_new(session_factory: sessionmaker[Session]) -> None:
    _case(session_factory)
    _news(session_factory, MOOR, "new_case", "арестован по новому делу")
    # Иванов's news is no political case's: not on the overview.
    _news(session_factory, "иван иванов", "sentence", "приговор свидетелю")

    with _client(session_factory) as client:
        page = client.get("/ui/overview")

    assert page.status_code == 200
    assert "<title>Обзор</title>" in page.text
    new_cases = page.text[
        page.text.index('id="new_case-title"') : page.text.index('id="sentence-title"')
    ]
    assert 'Новые дела <span class="count">1</span>' in new_cases
    assert f'href="/ui/investigations/{quote(MOOR)}">Моор Александр</a>' in new_cases
    assert "арестован по новому делу" in new_cases
    assert 'href="/ui/political?months=0&news=new_case">Все: 1 →</a>' in new_cases
    assert 'Приговоры <span class="count">0</span>' in page.text
    assert "Приговоров нет." in page.text and "приговор свидетелю" not in page.text
    assert "Неопознанные фигуранты" in page.text
    # The pipeline, its runs and the figures are elsewhere; the status line keeps the figures.
    for gone in ("Цикл обработки", "kpi", "Последние запуски", "Ошибки источников"):
        assert gone not in page.text, gone


def test_a_dossier_opens_and_an_unknown_person_is_not_found(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{quote(MOOR)}")
        missing = client.get("/ui/investigations/" + quote("никто никакой"))
        moved = client.get(f"/ui/entities/{quote(MOOR)}", follow_redirects=False)

    assert page.status_code == 200
    assert "<title>Моор Александр</title>" in page.text
    for section in ("Решение системы", "Статьи УК", "Хронология", "Граф событий", "Доказательства"):
        assert section in page.text
    assert '<section class="band" id="graph" aria-labelledby="graph-title">' in page.text
    assert "<dt>Публикаций</dt><dd>3 · упоминаний: 3</dd>" in page.text
    assert "<dt>Первая публикация</dt><dd>01.09.2026</dd>" in page.text
    assert missing.status_code == 404
    assert (moved.status_code, moved.headers["location"]) == (
        307,
        f"/ui/investigations/{quote(MOOR)}",
    )


def test_the_timeline_is_in_order_and_one_event_told_twice_is_one_item(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{quote(MOOR)}").text

    timeline = page[page.index('<ol class="timeline">') : page.index("</ol>")]
    items = re.findall(r"<time>([^<]+)</time>.*?<p><b>([^<]+)</b>", timeline, re.DOTALL)
    # The arrest of 01.09 was seeded after the sentence of 10.09: the order is the dates'.
    assert items == [("01.09.2026", "Арест"), ("10.09.2026", "Приговор")]
    # Two publications of the arrest, one item with two sources.
    assert "источников: 2" in timeline and timeline.count('class="timeline-item"') == 2
    # No event date is invented: the page says whose date it is.
    assert "по дате публикации" in timeline
    assert "Ленинский суд" in timeline and "(суд)" in timeline
    assert "ст. 205.2" in timeline


def test_the_evidence_quotes_mark_the_mention_and_link_the_text(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    with session_factory() as session:
        article, start, end = session.execute(
            text(
                "SELECT a.id, m.start_offset, m.end_offset FROM parsed_articles a "
                "JOIN article_extraction_runs r ON r.article_id = a.id "
                "JOIN entity_mentions m ON m.extraction_run_id = r.id "
                "WHERE a.title = 'Арест Моора' AND m.surface_text = 'Александра Моора'"
            )
        ).one()

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{quote(MOOR)}").text
        source = client.get(f"/ui/articles/{article}?start={start}&end={end}").text

    evidence = page[page.index('id="evidence"') :]
    assert (
        f'<a href="/ui/articles/{article}?start={start}&amp;end={end}">Арест Моора</a>' in evidence
    )
    assert "<mark>Александра Моора</mark>" in evidence
    assert "ОВД-Инфо · 01.09.2026" in evidence
    assert 'href="https://example.test/arrest" rel="noopener noreferrer"' in evidence
    # The other person of a publication, and the text the link opens, marked.
    assert "Иванов Иван</a>" in evidence
    assert "<mark>Александра Моора</mark>" in source


def test_the_decision_panel_shows_the_verdict_its_reason_and_its_source(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    with session_factory() as session:
        source = session.scalar(text("SELECT id FROM parsed_articles WHERE title = 'Арест Моора'"))

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{quote(MOOR)}").text

    decision = page[page.index('id="decision-title"') : page.index('id="charges"')]
    assert '<span class="badge succeeded">политическое</span>' in decision
    assert "модель по цитатам из публикаций" in decision
    assert "Преследование за антивоенные посты." in decision
    assert f"<blockquote>{QUOTE}</blockquote>" in decision
    # The quote leads to its publication in one step.
    assert f'<a href="/ui/articles/{source}">исходная публикация</a>' in decision
    assert "Уверенность не хранится" in decision


def test_the_rosfinmonitoring_status_is_shown_as_it_is(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    with _client(session_factory) as client:
        clear = client.get(f"/ui/investigations/{quote(MOOR)}").text
    _listed(session_factory, "name")
    with _client(session_factory) as client:
        maybe = client.get(f"/ui/investigations/{quote(MOOR)}").text
    with session_factory.begin() as session:
        session.execute(text("UPDATE entity_group_rf_matches SET level = 'full'"))
    with _client(session_factory) as client:
        listed = client.get(f"/ui/investigations/{quote(MOOR)}").text

    assert "не найден в перечне" in clear
    assert "возможно в перечне (тёзка без отчества)" in maybe and "МООР АЛЕКСАНДР ПЕТРОВИЧ" in maybe
    assert "может быть тёзка" in maybe
    # On the list: who the person is, not a mark against them.
    assert '<span class="badge ">в перечне Росфинмониторинга</span>' in listed


def test_the_latest_news_is_named_in_the_dossier_and_the_overview(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    with _client(session_factory) as client:
        unread = client.get(f"/ui/investigations/{quote(MOOR)}").text
    with session_factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO entity_group_news (group_id, kind, method, reason, quote) "
                "SELECT id, 'sentence', 'model', 'суд вынес приговор', '' FROM entity_groups "
                "WHERE key = :key"
            ),
            {"key": MOOR},
        )

    with _client(session_factory) as client:
        dossier = client.get(f"/ui/investigations/{quote(MOOR)}").text
        overview = client.get("/ui/overview").text

    assert "Свежая новость" not in unread
    role = dossier[dossier.index("<h3>Роль в деле</h3>") :]
    assert '<h3>Свежая новость</h3>\n    <p><span class="badge succeeded">приговор</span>' in role
    assert "суд вынес приговор" in role
    assert "суд вынес приговор" in overview


def _known(session_factory: sessionmaker[Session], *names: str) -> None:
    from db.orm_models import AirtableKnownPersonRecord

    with session_factory.begin() as session:
        for number, name in enumerate(names):
            session.add(
                AirtableKnownPersonRecord(
                    external_id=f"{name}-{number}",
                    full_name=name,
                    normalized_name=name.lower(),
                    matching_key=name.lower().replace(" ", ""),
                    active=True,
                )
            )


def test_the_dossier_says_whether_the_operator_s_base_holds_the_person(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    with _client(session_factory) as client:
        no_base = client.get(f"/ui/investigations/{quote(MOOR)}").text
    _known(session_factory, "Кто-то Другой")
    with _client(session_factory) as client:
        absent = client.get(f"/ui/investigations/{quote(MOOR)}").text
    _known(session_factory, "Моор Александр Петрович")
    with _client(session_factory) as client:
        present = client.get(f"/ui/investigations/{quote(MOOR)}").text

    # No base synced: the dossier says nothing, rather than «not in the base».
    assert "База Airtable" not in no_base
    assert "<h3>База Airtable</h3>" in absent and "нет в базе" in absent
    assert "вероятно, новый человек" in absent
    assert "вероятно, есть в базе" in present and "<li>Моор Александр Петрович</li>" in present
    # What a name proves is said where the answer is.
    assert "Сверка только по имени" in present and "нет в базе" not in present


def test_the_dossier_holds_the_place_of_the_event_graph(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{MOOR}").text
        graph = client.get(f"/api/investigations/{MOOR}/graph")

    section = page[page.index('<section class="band" id="graph"') :]
    section = section[: section.index("</section>")]
    address = "/api/investigations/%D0%B0%D0%BB%D0%B5%D0%BA%D1%81%D0%B0%D0%BD%D0%B4%D1%80%20%D0%BC%D0%BE%D0%BE%D1%80/graph"
    assert f'data-graph-url="{address}"' in section
    assert f'data-expand-url="{address}/expand"' in section
    # The address the page gives is the one the API serves.
    assert graph.status_code == 200 and graph.json()["center"].startswith("person:")
    assert '<a href="#graph">Граф событий</a>' in page
    # The library, the logic and the page script, from this site, in that order.
    assert re.findall(
        r'<script defer src="(/static/[^"?]+)\?v=[0-9a-f]{12}"></script>', section
    ) == [
        "/static/vendor/vis-network/vis-network.min.js",
        "/static/investigation-graph-core.js",
        "/static/investigation-graph.js",
    ]
    assert "http://" not in section and "https://" not in section
    # Co-occurrence is off until asked for; the rest is on.
    assert re.findall(r'data-filter="(\w+)"( checked)?', section) == [
        ("events", " checked"),
        ("people", " checked"),
        ("publications", " checked"),
        ("orgs", " checked"),
        ("articles", " checked"),
        ("cooccurrence", ""),
    ]
    assert 'class="secondary ig-reset"' in section and '<div class="ig-panel"' in section
    # Without the script the section says so, and no node of the graph is written here.
    assert "требует JavaScript" in section and "Остальное досье от него не зависит" in section
    assert "Ленинский суд" not in section and "Моор" not in section.split("aria-label")[0]


def test_the_people_of_the_same_publications_are_a_count_not_a_connection(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory, extra=25)

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{quote(MOOR)}").text

    table = page[page.index('<details id="links">') :]
    table = table[: table.index("</details>")]
    # Everyone of a shared publication, readable without the script — and named for what
    # it is.
    assert "<summary>Люди из тех же публикаций (26)</summary>" in table
    assert "Совместные упоминания: счёт общих публикаций, не установленная связь" in table
    assert table.count("<tr><td><a href=") == 26 and "Иванов Иван" in table
    # The old drawing is gone: no person is joined to a person, a court or an article here.
    assert "<svg" not in page[page.index('id="graph"') :]
    assert "Ленинский суд" not in table and "ст. 205.2" not in table


def test_a_cut_list_of_people_says_it_is_cut(session_factory: sessionmaker[Session]) -> None:
    _case(session_factory, extra=33)

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{quote(MOOR)}").text

    # 34 people share a publication; the table holds the first 30 and does not call it all.
    assert "<summary>Люди из тех же публикаций (первые 30)</summary>" in page
    assert page[page.index('<details id="links">') :].count("<tr><td><a href=") == 30


def _statements(session_factory: sessionmaker[Session], path: str) -> int:
    engine = session_factory.kw["bind"]
    count = 0

    def counted(*_args: object) -> None:
        nonlocal count
        count += 1

    event.listen(engine, "before_cursor_execute", counted)
    try:
        with _client(session_factory) as client:
            assert client.get(path).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", counted)
    return count


def test_the_dossier_s_queries_do_not_grow_with_the_case(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    small = _statements(session_factory, f"/ui/investigations/{quote(MOOR)}")
    _more(session_factory, 12, "Коммерсантъ")

    large = _statements(session_factory, f"/ui/investigations/{quote(MOOR)}")

    assert large == small


def test_the_people_pager_is_short_and_keeps_the_filters(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO entity_groups (key, name, variants, mention_count, article_count, "
                "event_types) SELECT 'человек ' || n, 'Иван Человеков' || n, '[]', 1, 1, '{}' "
                "FROM generate_series(1, 1200) AS n"
            )
        )

    with _client(session_factory) as client:
        page = client.get("/ui/entities", params={"rf": "all", "sort": "name", "page": 6}).text

    pager = page[page.index('class="pager"') :]
    shown = re.findall(r">(\d+|…|Назад|Вперёд)<", pager)
    assert shown == ["Назад", "1", "…", "4", "5", "6", "7", "8", "…", "12", "Вперёд"]
    for link in re.findall(r'href="(/ui/entities\?[^"]+)"', pager):
        assert "rf=all" in link and "sort=name" in link
    assert "Найдено: 1200." in page


def _pairs(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        for key, name, mentions in (
            ("олег орлов", "Олег Орлов", 9),
            ("олег петрович орлов", "Олег Петрович Орлов", 8),
            ("анна смирнова", "Анна Смирнова", 5),
            ("анна ивановна смирнова", "Анна Ивановна Смирнова", 4),
        ):
            session.execute(
                text(
                    "INSERT INTO entity_groups (key, name, variants, mention_count, "
                    "article_count, event_types) VALUES (:key, :name, '[]', :mentions, 1, '{}')"
                ),
                {"key": key, "name": name, "mentions": mentions},
            )


def test_a_decided_pair_is_kept_and_the_next_one_is_shown(
    session_factory: sessionmaker[Session],
) -> None:
    _pairs(session_factory)

    with _client(session_factory) as client:
        first = client.get("/ui/pairs").text
        decided = client.post(
            "/ui/disputes/decide",
            data={
                "key_a": "олег орлов",
                "key_b": "олег петрович орлов",
                "decision": "different",
                "back": "pairs",
            },
            follow_redirects=False,
        )
        after = client.get(decided.headers["location"]).text

    assert "Нерешённых пар: 2" in first and "Орлов Олег Петрович" in first
    assert decided.status_code == 303 and decided.headers["location"] == "/ui/pairs"
    assert "Нерешённых пар: 1" in after and "Смирнова Анна Ивановна" in after
    assert "Орлов" not in after
    with session_factory() as session:
        record = session.get(EntityPairDecisionRecord, ("олег орлов", "олег петрович орлов"))
        assert record is not None and (record.decision, record.source) == ("different", "manual")


def test_the_old_queue_keeps_its_filter_when_redirecting(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        legacy = client.get("/ui/queue?kind=similar", follow_redirects=False)

    assert (legacy.status_code, legacy.headers["location"]) == (
        303,
        "/ui/pairs?kind=similar",
    )


def test_empty_states_say_so(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        overview = client.get("/ui/overview").text
        pairs = client.get("/ui/pairs").text
        roles = client.get("/ui/roles").text
        politics = client.get("/ui/politics-review").text
        publications = client.get("/ui/publications").text
        investigations = client.get("/ui/investigations").text
    _case(session_factory)
    with _client(session_factory) as client:
        witness = client.get("/ui/investigations/" + quote("иван иванов")).text

    assert "Новых дел нет." in overview and "Приговоров нет." in overview
    assert "Неопознанных нет." in overview and "Решений оператора не ждёт ничего." in overview
    assert "ошибками загрузки" not in overview
    assert "Спорных пар нет." in pairs
    assert "Неясная роль в деле: открытых случаев нет." in roles
    assert "Неясная политичность: открытых случаев нет." in politics
    assert "Ничего не найдено." in publications
    assert "Никого не найдено." in investigations
    assert "Нет событий, где этот человек назван участником" in witness
    assert "Шаг 5 не оценивал это дело" in witness


def test_loaded_and_typed_text_is_escaped(session_factory: sessionmaker[Session]) -> None:
    _case(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE entity_groups SET name = 'Александр <b>Моор</b>' WHERE key = :key"),
            {"key": MOOR},
        )
        session.execute(
            text(
                "UPDATE parsed_articles SET title = '<img src=x onerror=alert(1)>' "
                "WHERE title = 'Арест Моора'"
            )
        )

        # A scraped address is no script to run.
        session.execute(
            text(
                "UPDATE source_documents SET canonical_url = 'javascript:alert(1)' "
                "WHERE external_id = 'arrest'"
            )
        )
        article = session.scalar(text("SELECT id FROM parsed_articles WHERE title LIKE '<img%'"))

    with _client(session_factory) as client:
        pages = [
            client.get(f"/ui/investigations/{quote(MOOR)}").text,
            client.get("/ui/publications").text,
            client.get("/ui/entities", params={"figurants": "all", "q": "<script>"}).text,
            client.get("/ui/investigations", params={"q": "<script>x</script>"}).text,
            client.get(f"/ui/articles/{article}").text,
        ]

    for page in pages:
        assert "<b>Моор</b>" not in page
        assert "<img src=x" not in page
        assert "<script>x</script>" not in page and '"<script>"' not in page
        assert 'href="javascript:' not in page
    assert "&lt;b&gt;Моор&lt;/b&gt;" in pages[0]
    assert "&lt;img src=x onerror=alert(1)&gt;" in pages[1]


def test_the_old_addresses_still_answer(session_factory: sessionmaker[Session]) -> None:
    _case(session_factory)

    with _client(session_factory) as client:
        answers = {
            path: client.get(path).status_code
            for path in (
                "/",
                "/ui",
                "/ui/management",
                "/ui/entities",
                f"/ui/entities/{quote(MOOR)}",
                "/ui/disputes",
                "/ui/political",
                "/ui/airtable",
                "/ui/logs",
                "/ui/wiki",
                "/ui/overview",
                "/ui/investigations",
                "/ui/publications",
                "/ui/queue",
                "/ui/pairs",
            )
        }

    assert set(answers.values()) == {200}, answers


def test_the_interface_speaks_russian(session_factory: sessionmaker[Session]) -> None:
    _case(session_factory)

    with _client(session_factory) as client:
        pages = {
            path: client.get(path).text
            for path in (
                "/ui/overview",
                f"/ui/investigations/{quote(MOOR)}",
                "/ui/entities",
                "/ui/publications",
                "/ui/pairs",
            )
        }

    for path, page in pages.items():
        visible = re.sub(r"<script.*?</script>|<[^>]+>", " ", page, flags=re.DOTALL)
        for english in ("Persons", "ER pending", "run:", "Dashboard", "Search", "Timeline"):
            assert english not in visible, (path, english)
        assert '<html lang="ru">' in page
    assert 'href="/ui/cycle">К циклу</a>' not in pages["/ui/overview"]  # no work is open
    assert "Спорные совпадения людей" in pages["/ui/pairs"]


def test_every_pair_decision_can_be_reset(session_factory: sessionmaker[Session]) -> None:
    _pairs(session_factory)
    with session_factory.begin() as session:
        for key_a, key_b, source in (
            ("олег орлов", "олег петрович орлов", "manual"),
            ("анна ивановна смирнова", "анна смирнова", "region"),
        ):
            session.add(
                EntityPairDecisionRecord(
                    key_a=key_a, key_b=key_b, decision="different", source=source
                )
            )

    with _client(session_factory) as client:
        before = client.get("/ui/pairs").text
        reset = client.post("/ui/pairs/reset-decisions", follow_redirects=False)
        after = client.get(reset.headers["location"]).text

    # Asked first, with what goes; the button only when there is something to forget.
    assert "Сбросить все решения по парам" in before
    assert "Сохранено решений: 2 (вручную: 1," in before and "Спорных пар нет." in before
    assert "return confirm(" in before and "Это необратимо." in before
    assert reset.status_code == 303 and reset.headers["location"] == "/ui/pairs?reset=2"
    assert "Решения по парам сброшены: 2." in after
    # Both pairs are disputed again; nothing is left to reset.
    assert "Нерешённых пар: 2" in after and "Сохранённых решений по парам нет." in after
    with session_factory() as session:
        assert session.scalar(text("SELECT count(*) FROM entity_pair_decisions")) == 0
