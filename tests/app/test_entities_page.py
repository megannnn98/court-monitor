"""«Люди»: the list, the old card address (now the dossier), and the rebuild button."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.pipeline_runs import finish_steps
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import EntityGroupPoliticsRecord, EntityMentionRecord
from entities.collector import EntityCollector
from entities.rf_check import EntityRfCheck
from entities.roles import FigurantFinder, RoleAnswer, RoleItem
from operator_console import OperationRegistry, OperationRunStatus
from web.app import app
from web.dependencies import get_db, get_operation_registry

MOOR = quote("александр моор")


@contextmanager
def _client(
    session_factory: sessionmaker[Session], registry: OperationRegistry
) -> Iterator[TestClient]:
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


def _collected(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        _, run = seed.article(
            source,
            external_id="arrest",
            title="Арест Моора",
            text="Суд арестовал Александра Моора и Ивана Иванова.",
            published_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        arrested = _person(session, seed, run, "Александра Моора", "Александр", "Моора")
        _person(session, seed, run, "Ивана Иванова", "Иван", "Иванов")
        seed.event(
            run,
            "Суд арестовал",
            event_type="arrest",
            event_date=None,
            links=[],
            entity_links=[(arrested, "subject")],
        )
        _, run = seed.article(
            source,
            external_id="sentence",
            title="Приговор Моору",
            text="Александру Моору вынесли приговор.",
            published_at=datetime(2026, 9, 10, tzinfo=UTC),
        )
        _person(session, seed, run, "Александру Моору", "Александр", "Моору")
        seed.event(run, "вынесли приговор", event_type="sentence", event_date=None, links=[])
        session.commit()
    EntityCollector(session_factory).run()


def test_the_list_shows_entities_with_their_forms_and_finds_by_any_form(
    session_factory: sessionmaker[Session],
) -> None:
    _collected(session_factory)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/entities").text
        by_declined_form = client.get("/ui/entities", params={"q": "Моору"}).text
        nobody = client.get("/ui/entities", params={"q": "Петров"}).text

    assert "<span>Люди</span></a>" in page
    # Surname first, as the candidates are; the name opens the dossier.
    assert f'<a href="/ui/investigations/{MOOR}">Моор Александр</a>' in page
    assert "Найдено: 2." in page
    assert "Найдено: 1." in by_declined_form and "Моор Александр" in by_declined_form
    assert "Найдено: 0." in nobody


def test_a_card_marks_each_mention_and_lists_the_people_named_beside(
    session_factory: sessionmaker[Session],
) -> None:
    _collected(session_factory)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        card = client.get(f"/ui/entities/{MOOR}")
        missing = client.get("/ui/entities/" + quote("никто никакой"))

    assert card.status_code == 200
    assert "<mark>Александра Моора</mark>" in card.text
    assert "<mark>Александру Моору</mark>" in card.text
    assert re.search(
        r'href="/ui/investigations/[^"]+">Иванов Иван</a></td><td class="num">1</td>',
        card.text,
    )
    assert "Арест: 1" in card.text
    assert missing.status_code == 404


def test_the_button_rebuilds_in_the_background_and_can_be_stopped(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        early = client.get("/ui/entities").text
        refused = client.post("/ui/entities/collect", follow_redirects=False)
        finish_steps(session_factory, registry, "load", "purge")
        idle = client.get("/ui/entities").text
        started = client.post("/ui/entities/collect", follow_redirects=False)
        busy = client.get("/ui/entities").text
        run = registry.runs_of("monitor")[0]
        stopped = client.post(
            f"/ui/management/runs/{run.id}/stop", data={"back": "entities"}, follow_redirects=False
        )

    # Out of turn the button is grey and says which step is due; the server agrees.
    assert 'id="collect-button"' not in early and "Сейчас шаг 1" in early
    assert refused.status_code == 303
    assert 'id="collect-button"' in idle
    assert started.status_code == 303
    assert (run.parameters.mode, run.command[2:]) == ("entities", ["collect-entities"])
    assert f"Остановить сборку #{run.id}" in busy
    assert stopped.headers["location"] == f"/ui/entities?run_id={run.id}"
    assert registry.get(run.id).status is OperationRunStatus.INTERRUPTED


def test_a_name_a_model_gave_is_marked(session_factory: sessionmaker[Session]) -> None:
    _collected(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE entity_groups SET name_source = 'model', gender = 'male' "
                "WHERE key = 'александр моор'"
            )
        )
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/entities").text
        card = client.get(f"/ui/entities/{MOOR}").text
        other = client.get("/ui/entities/" + quote("иван иванов")).text

    assert re.search(r">Моор Александр</a> <span class=\"badge\"[^>]*>ИИ</span>", page)
    assert "<dt>Имя</dt><dd>дала модель · мужчина</dd>" in card
    assert "<dt>Имя</dt><dd>по правилам склейки</dd>" in other


def test_names_read_surname_first_and_sort_by_surname(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        for key, name in (
            ("анна яковлева", "Анна Яковлева"),
            ("борис абрамов", "Борис Петрович Абрамов"),
            ("вера моор", "Вера Моор"),
        ):
            session.execute(
                text(
                    "INSERT INTO entity_groups (key, name, variants, mention_count, "
                    "article_count, event_types) VALUES (:key, :name, '[]', 1, 1, '{}')"
                ),
                {"key": key, "name": name},
            )
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/entities", params={"sort": "name"}).text
        card = client.get("/ui/entities/" + quote("борис абрамов")).text

    assert re.findall(r'<a href="/ui/investigations/[^"]+">([^<]+)</a>', page) == [
        "Абрамов Борис Петрович",
        "Моор Вера",
        "Яковлева Анна",
    ]
    assert "<title>Абрамов Борис Петрович</title>" in card


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ("Иван Иванов", "Иванов Иван"),
        ("Иван Петрович Иванов", "Иванов Иван Петрович"),
        ("Соломатин П.", "Соломатин П."),  # a surname and a given name's initial
        ("Дмитрий С.", "С. Дмитрий"),  # a given name and a surname's initial
        ("Бонцлер", "Бонцлер"),
    ],
)
def test_a_name_is_shown_surname_first_initials_kept(stored: str, shown: str) -> None:
    from web.ui.entities import display_name

    assert display_name(stored) == shown


def test_initials_sort_by_the_name_as_shown(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        for key, name in (
            ("дмитрий с", "Дмитрий С."),
            ("соломатин п", "Соломатин П."),
            ("анна яковлева", "Анна Яковлева"),
        ):
            session.execute(
                text(
                    "INSERT INTO entity_groups (key, name, variants, mention_count, "
                    "article_count, event_types) VALUES (:key, :name, '[]', 1, 1, '{}')"
                ),
                {"key": key, "name": name},
            )
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/entities", params={"sort": "name"}).text

    assert re.findall(r'<a href="/ui/investigations/[^"]+">([^<]+)</a>', page) == [
        "С. Дмитрий",
        "Соломатин П.",
        "Яковлева Анна",
    ]


def _law(session: Session, seed: ResearchSeeder, run: int, surface: str, article: str) -> int:
    mention_id = seed.mention(run, surface, person_id=None, entity_type="legal_reference")
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "code": "УК РФ",
        "article": article,
        "part": "2" if "ч. 2" in surface else None,
        "clause": None,
    }
    return mention_id


def _charged(session_factory: sessionmaker[Session]) -> None:
    """Моор is charged alone under 205.2; Иванов and Петров share 207.3 and 20.3.1."""
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        text_ = "Суд арестовал Александра Моора по ч. 2 ст. 205.2 УК РФ."
        _, run = seed.article(source, external_id="moor", title="Арест Моора", text=text_)
        moor = _person(session, seed, run, "Александра Моора", "Александр", "Моор")
        law = _law(session, seed, run, "ч. 2 ст. 205.2 УК РФ", "205.2")
        seed.event(
            run,
            text_,
            event_type="arrest",
            event_date=None,
            links=[],
            entity_links=[(moor, "target"), (law, "legal_basis")],
        )
        text_ = "Ивана Иванова и Петра Петрова обвинили по ст. 207.3 УК РФ."
        _, run = seed.article(source, external_id="pair", title="Обвинение", text=text_)
        seed.event(
            run,
            text_,
            event_type="charge",
            event_date=None,
            links=[],
            entity_links=[
                (_person(session, seed, run, "Ивана Иванова", "Иван", "Иванов"), "target"),
                (_person(session, seed, run, "Петра Петрова", "Петр", "Петров"), "target"),
                (_law(session, seed, run, "ст. 207.3 УК РФ", "207.3"), "legal_basis"),
            ],
        )
        session.commit()
    EntityCollector(session_factory).run()


def test_a_card_lists_the_criminal_code_articles_with_their_publications(
    session_factory: sessionmaker[Session],
) -> None:
    _charged(session_factory)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        moor = client.get(f"/ui/entities/{MOOR}").text
        ivanov = client.get("/ui/entities/" + quote("иван иванов")).text

    assert '<h2 id="charges-title">Статьи УК</h2>' in moor
    assert re.search(
        r">ст\. 205\.2</a> <span class=\"badge\">политическая</span> \(ч\. 2\) "
        r'<span class="muted">публикаций: 1</span>',
        moor,
    )
    assert "Суд арестовал Александра Моора по ч. 2 ст. 205.2 УК РФ." in moor
    # Named beside another target: shown, but marked shared; the only target is not.
    shared = 'title="Во всех событиях обвиняемыми названы и другие люди">общая</span>'
    assert re.search(r">ст\. 207\.3</a>.*?" + re.escape(shared), ivanov)
    assert shared not in moor


def test_the_list_shows_the_articles_and_filters_by_one(
    session_factory: sessionmaker[Session],
) -> None:
    _charged(session_factory)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/entities").text
        by_article = client.get("/ui/entities", params={"article": "207.3"}).text

    assert (
        '<a href="/ui/entities?article=205.2&rf=all&rf_possible=all&role=figurant&verdict=all&region=&sort=mentions">205.2</a>'
        in page
    )
    assert (
        '<a href="/ui/entities?article=207.3&rf=all&rf_possible=all&role=figurant&verdict=all&region=&sort=mentions" class="muted" title="общая">'
        "207.3</a>" in page
    )
    assert "Найдено: 3" in page
    assert "Найдено: 2 по статье УК 207.3" in by_article
    assert "Иванов Иван" in by_article and "Петров Петр" in by_article
    assert "Моор Александр" not in by_article


RF_PAGE = """<!doctype html><html>
<div class="panel-heading"><h4>Физические лица</h4></div>
<div class="panel-body"><ol>
  <li>1. МООР АЛЕКСАНДР ПЕТРОВИЧ*, 01.02.1980 г.р. , Г. МОСКВА;</li>
  <li>2. ИВАНОВ ИВАН СЕРГЕЕВИЧ*, , ;</li>
  <li>3. МООР АЛЕКСАНДР*, , ;</li>
</ol></div></html>""".encode()


def test_entities_on_the_rosfinmonitoring_list_are_shown_marked_and_can_be_hidden(
    session_factory: sessionmaker[Session],
) -> None:
    _collected(session_factory)
    with session_factory.begin() as session:
        # The model's full name: the list's patronymic, so the entity is on the list.
        session.execute(
            text("UPDATE entity_groups SET name = 'Александр Петрович Моор' WHERE key = :key"),
            {"key": "александр моор"},
        )
    EntityRfCheck(session_factory, download=lambda: RF_PAGE).run()
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/entities").text
        everyone = client.get("/ui/entities", params={"rf": "all"}).text
        # The form sends the hidden «all» first, then the ticked box's «hide».
        ticked = client.get("/ui/entities?rf=all&rf=hide").text
        both = client.get("/ui/entities?rf=all&rf=hide&rf_possible=all&rf_possible=hide").text
        card = client.get(f"/ui/entities/{MOOR}").text

    # The list confirms who a person is: shown by default, hidden only on request.
    assert re.search(r"Моор Александр Петрович</a>.*?>в перечне</span>", page)
    assert "Найдено: 2." in page
    assert '<input id="box-rf" type="checkbox" name="rf" value="hide" onchange' in page
    assert "Скрыть тех, кто в перечне РФМ (1)" in page
    # A name without a patronymic may be a namesake: shown, marked.
    assert re.search(r"Иванов Иван</a>.*?возможно в перечне", page)
    assert "Найдено: 2." in everyone
    assert re.search(r"Моор Александр Петрович</a>.*?>в перечне</span>", everyone)
    assert '<input id="box-rf" type="checkbox" name="rf" value="hide" onchange' in everyone
    # The second box, off by default, hides the maybe-namesakes too; Моор is on the list
    # for certain, so his patronymic-less namesake entry does not count him again.
    assert "Скрыть возможных — тёзки без отчества (1)" in page
    assert 'name="rf_possible" value="hide" onchange' in page
    assert 'name="rf_possible" value="hide" checked' in both
    assert "Найдено: 0." in both
    assert "Моор Александр" not in ticked and "Найдено: 1." in ticked
    assert '<input id="box-rf" type="checkbox" name="rf" value="hide" checked' in ticked
    assert "<h3>Росфинмониторинг</h3>" in card and "в перечне Росфинмониторинга" in card
    assert "МООР АЛЕКСАНДР ПЕТРОВИЧ, 01.02.1980 г.р., Г. МОСКВА" in card


class _Roles:
    model = "fake-model"

    def classify(self, items: Sequence[RoleItem]) -> dict[int, RoleAnswer]:
        kinds = {"Александр Моор": "accused", "Иван Иванов": "administrative"}
        return {
            item.id: RoleAnswer(
                id=item.id, source=item.name, kind=kinds[item.name], explanation="по цитате"
            )  # type: ignore[arg-type]
            for item in items
        }


def test_only_the_figurants_are_listed_by_default(
    session_factory: sessionmaker[Session],
) -> None:
    _collected(session_factory)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    with _client(session_factory, registry) as client:
        before = client.get("/ui/entities").text
    FigurantFinder(session_factory, classifier=_Roles()).run()

    with _client(session_factory, registry) as client:
        page = client.get("/ui/entities").text
        everyone = client.get("/ui/entities?figurants=all").text
        card = client.get(f"/ui/entities/{MOOR}").text

    # Before step 5 nobody has a role: all are shown, and the page says why.
    assert "Фигуранты ещё не определены" in before and "Найдено: 2." in before
    assert "Найдено: 1." in page
    assert re.search(r"Моор Александр</a>.*?фигурант дела</span>", page)
    assert "Иванов Иван" not in page
    # The role is a choice now; «figurants=all» of the old links still means every role.
    assert '<option value="figurant" selected>Фигуранты дел</option>' in page
    # An administrative case is no criminal one: not a figurant, and says so.
    assert "Найдено: 2." in everyone and "административное дело</span>" in everyone
    assert "<h3>Роль в деле</h3>" in card and "ответ модели по цитатам" in card


def test_the_region_of_a_registry_card_is_shown(session_factory: sessionmaker[Session]) -> None:
    _collected(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE entity_groups SET regions = '[[\"Луганская область\", 1]]' WHERE key = :key"
            ),
            {"key": "александр моор"},
        )
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/entities").text
        card = client.get(f"/ui/entities/{MOOR}").text

    assert re.search(r"Моор Александр</a>.*?<span class=\"muted\">Луганская область</span>", page)
    assert "<dt>Регион</dt><dd>Луганская область</dd>" in card


def test_an_official_is_marked_on_the_card_and_unmarked_on_the_officials_page(
    session_factory: sessionmaker[Session],
) -> None:
    _collected(session_factory)
    FigurantFinder(session_factory, classifier=_Roles()).run()
    with session_factory.begin() as session:
        moor = session.scalar(text("SELECT id FROM entity_groups WHERE key = 'александр моор'"))
        session.add(
            EntityGroupPoliticsRecord(
                group_id=moor, verdict="political", method="model", reason="—", quote=""
            )
        )
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        card = client.get(f"/ui/entities/{MOOR}").text
        listed = client.get("/ui/political").text
        marked = client.post(
            f"/ui/entities/{MOOR}/official", data={"official": "yes"}, follow_redirects=False
        )
        officials = client.get("/ui/officials").text
        unmarked = client.post(
            f"/ui/entities/{MOOR}/official",
            data={"official": "no", "back": "officials"},
            follow_redirects=False,
        )
        after = client.get("/ui/officials").text
        missing = client.post("/ui/entities/никто/official", data={"official": "yes"})

    assert '<button type="submit" class="secondary">Это должностное лицо</button>' in card
    assert marked.status_code == 303
    assert marked.headers["location"] == f"/ui/investigations/{MOOR}"
    assert "<span>Должностные лица</span></a>" in officials
    assert "Найдено: 1." in officials and "Моор Александр" in officials
    # An official leaves «Список» at once.
    assert "Моор Александр" in listed
    with session_factory() as session:
        assert session.scalar(text("SELECT count(*) FROM entity_group_politics")) == 0
    assert "должностное лицо — отмечен вручную" in officials
    assert unmarked.headers["location"] == "/ui/officials"
    assert "Найдено: 0." in after
    assert missing.status_code == 404


def test_a_name_is_corrected_on_the_card(session_factory: sessionmaker[Session]) -> None:
    _collected(session_factory)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        card = client.get(f"/ui/entities/{MOOR}").text
        corrected = client.post(
            f"/ui/entities/{MOOR}/name",
            data={"name": "Моор Александр Викторович"},
            follow_redirects=False,
        )
        after = client.get(f"/ui/entities/{MOOR}").text
        listed = client.get("/ui/entities", params={"figurants": "all"}).text
        empty = client.post(f"/ui/entities/{MOOR}/name", data={"name": "   "})
        missing = client.post("/ui/entities/никто/name", data={"name": "Кто-то"})

    assert '<button type="submit" class="secondary">Исправить имя</button>' in card
    assert corrected.status_code == 303
    assert corrected.headers["location"] == f"/ui/investigations/{MOOR}"
    # Written surname first, kept given name first, shown surname first.
    assert "<title>Моор Александр Викторович</title>" in after
    assert 'value="Александр Викторович Моор"' in after
    assert "<dt>Имя</dt><dd>исправлено вручную</dd>" in after
    assert "Моор Александр Викторович</a>" in listed and "исправлено</span>" in listed
    assert empty.status_code == 400 and missing.status_code == 404


def test_a_corrected_name_that_is_another_entity_s_merges_them(
    session_factory: sessionmaker[Session],
) -> None:
    _collected(session_factory)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        client.post(f"/ui/entities/{MOOR}/name", data={"name": "Иван Иванов"})
        page = client.get("/ui/entities", params={"figurants": "all"}).text

    assert "Найдено: 1." in page and "Иванов Иван" in page
    with session_factory() as session:
        assert session.scalar(text("SELECT mention_count FROM entity_groups")) == 3
