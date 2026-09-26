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
from support.research_db_fixtures import ResearchSeeder

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
from web.ui.dossier import GRAPH_LIMIT, graph_nodes, load

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
    session: Session, seed: ResearchSeeder, run: int, surface: str, first: str, last: str
) -> int:
    mention_id = seed.mention(run, surface, person_id=None)
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": None,
    }
    return mention_id


def _law(session: Session, seed: ResearchSeeder, run: int, surface: str, article: str) -> int:
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
        seed = ResearchSeeder(session)
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
        seed = ResearchSeeder(session)
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
        seed = ResearchSeeder(session)
        entry = seed.entry(seed.snapshot(), "МООР АЛЕКСАНДР ПЕТРОВИЧ")
        group = session.scalar(text("SELECT id FROM entity_groups WHERE key = :key"), {"key": MOOR})
        session.add(EntityGroupRfMatchRecord(group_id=group, entry_id=entry, level=level))


def test_the_overview_shows_the_figures_the_new_figurants_and_the_pipeline(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/overview")

    assert page.status_code == 200
    assert "<title>Обзор</title>" in page.text
    for label in ("Публикации", "Люди", "Фигуранты", "Результат", "Очередь"):
        assert f'<span class="kpi-label">{label}</span>' in page.text
    assert re.search(
        r'<span class="kpi-label">Результат</span><strong class="kpi-value">1<', page.text
    )
    assert f'href="/ui/investigations/{quote(MOOR)}">Моор Александр</a>' in page.text
    assert "Цикл обработки" in page.text and "1. Подгрузить статьи" in page.text
    # The runs' tables are folded.
    assert "<details><summary>Последние запуски подробно</summary>" in page.text


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
    for section in ("Решение системы", "Статьи УК", "Хронология", "Связи", "Доказательства"):
        assert section in page.text
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


def test_the_links_are_the_data_s_and_the_graph_keeps_to_its_limit(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory, extra=25)

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{quote(MOOR)}").text
    with session_factory() as session:
        dossier = load(session, MOOR)
    assert dossier is not None
    nodes, total = graph_nodes(dossier)

    links = page[page.index('id="links"') : page.index('id="evidence"')]
    # A court of an event, an article of a charge, a person of a shared publication.
    assert "Ленинский суд" in links and "ст. 205.2" in links and "Иванов Иван" in links
    # Every kind has its share; together no more than the limit.
    assert len(nodes) <= GRAPH_LIMIT < total
    assert {node.kind for node in nodes} == {"article", "person", "org", "publication", "event"}
    assert links.count('class="node node-') == len(nodes) + 1  # and the person
    assert f"Показано {len(nodes)} из {total}." in links
    # The rest is in the table, all of it.
    assert "Показать все связанные люди (26)" in links


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
        first = client.get("/ui/queue").text
        decided = client.post(
            "/ui/disputes/decide",
            data={
                "key_a": "олег орлов",
                "key_b": "олег петрович орлов",
                "decision": "different",
                "back": "queue",
            },
            follow_redirects=False,
        )
        after = client.get(decided.headers["location"]).text

    assert "Пара 1 из 2" in first and "Орлов Олег Петрович" in first
    assert "Почему предложена:" in first and "Почему не слита автоматически:" in first
    assert decided.status_code == 303 and decided.headers["location"].startswith("/ui/queue")
    assert "Пара 1 из 1" in after and "Смирнова Анна Ивановна" in after
    assert "Орлов" not in after.split('id="pairs"')[1].split("</section>")[0]
    with session_factory() as session:
        record = session.get(EntityPairDecisionRecord, ("олег орлов", "олег петрович орлов"))
        assert record is not None and (record.decision, record.source) == ("different", "manual")


def test_a_postponed_pair_steps_aside_for_this_visit(
    session_factory: sessionmaker[Session],
) -> None:
    _pairs(session_factory)
    orlov = "олег орлов|олег петрович орлов"
    smirnova = "анна ивановна смирнова|анна смирнова"

    with _client(session_factory) as client:
        first = client.get("/ui/queue").text
        next_one = client.get("/ui/queue", params={"skip": orlov}).text
        none_left = client.get("/ui/queue", params=[("skip", orlov), ("skip", smirnova)]).text

    postpone = re.search(r'href="(/ui/queue\?skip=[^"]+)#pairs">Отложить', first)
    assert postpone is not None and "skip=" in postpone.group(1)
    assert "Смирнова Анна Ивановна" in next_one and "Пара 2 из 2" in next_one
    assert "Все 2 пар отложены" in none_left
    with session_factory() as session:
        assert session.scalar(text("SELECT count(*) FROM entity_pair_decisions")) == 0


def test_empty_states_say_so(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        overview = client.get("/ui/overview").text
        queue = client.get("/ui/queue").text
        publications = client.get("/ui/publications").text
        investigations = client.get("/ui/investigations").text
    _case(session_factory)
    with _client(session_factory) as client:
        witness = client.get("/ui/investigations/" + quote("иван иванов")).text

    assert "Фигурантов пока нет" in overview and "Ошибок нет." in overview
    assert "Очередь пуста." in overview
    assert "Спорных пар нет." in queue and "Неясных ролей нет." in queue
    assert "Неясных дел нет." in queue and "Сбоев нет." in queue
    assert "Ничего не найдено." in publications
    assert "Никого не найдено." in investigations
    assert "Нет событий, где этот человек назван участником" in witness
    assert "Шаг 6 не оценивал это дело" in witness


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
                "/ui/officials",
                "/ui/logs",
                "/ui/wiki",
                "/ui/overview",
                "/ui/investigations",
                "/ui/publications",
                "/ui/queue",
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
                "/ui/queue",
            )
        }

    for path, page in pages.items():
        visible = re.sub(r"<script.*?</script>|<[^>]+>", " ", page, flags=re.DOTALL)
        for english in ("Persons", "ER pending", "run:", "Dashboard", "Search", "Timeline"):
            assert english not in visible, (path, english)
        assert '<html lang="ru">' in page
    assert "Разобрать очередь" not in pages["/ui/overview"]  # the queue is empty
    assert "Спорные совпадения людей" in pages["/ui/queue"]


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
        before = client.get("/ui/queue").text
        reset = client.post("/ui/queue/reset-decisions", follow_redirects=False)
        after = client.get(reset.headers["location"]).text

    # Asked first, with what goes; the button only when there is something to forget.
    assert "Сбросить все решения по парам" in before
    assert "Сохранено решений: 2 (вручную: 1," in before and "Спорных пар нет." in before
    assert "return confirm(" in before and "Это необратимо." in before
    assert reset.status_code == 303 and reset.headers["location"] == "/ui/queue?reset=2#pairs"
    assert "Решения по парам сброшены: 2." in after
    # Both pairs are disputed again; nothing is left to reset.
    assert "Пара 1 из 2" in after and "Сохранённых решений по парам нет." in after
    with session_factory() as session:
        assert session.scalar(text("SELECT count(*) FROM entity_pair_decisions")) == 0
