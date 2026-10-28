"""«Люди»: the list, the old card address (now the dossier), and the rebuild button."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from analysis.rosfinmonitoring.rf_check import EntityRfCheck
from analysis.service import GroupFollowers
from analysis.storage.models import EntityGroupPoliticsRecord
from orchestrator.operations import OperationRegistry
from processing.identity.collector import EntityCollector
from processing.roles.roles import FigurantFinder, Kind, RoleAnswer, RoleItem
from processing.storage.models import EntityMentionRecord
from web.app import app
from web.dependencies import OPERATION_COMMAND_LINE, get_db, get_operation_registry

KEY = "александр моор"
MOOR = quote(KEY)


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


def _people(client: TestClient, **params: str | int) -> dict[str, Any]:
    """«Все люди» as the React page reads it."""
    listed: dict[str, Any] = client.get("/api/v1/entities", params=params).json()
    return listed


def _dossier(client: TestClient, key: str) -> dict[str, Any]:
    """A person's card as the React dossier reads it."""
    card: dict[str, Any] = client.get(f"/api/v1/investigations/{quote(key)}").json()
    return card


def _names(listed: dict[str, Any]) -> list[str]:
    return [row["name"] for row in listed["items"]]


def _row(listed: dict[str, Any], name: str) -> dict[str, Any]:
    row: dict[str, Any] = next(item for item in listed["items"] if item["name"].startswith(name))
    return row


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


def _collected(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
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
    EntityCollector(session_factory, downstream=GroupFollowers()).run()


def test_the_list_shows_entities_with_their_forms_and_finds_by_any_form(
    session_factory: sessionmaker[Session],
) -> None:
    _collected(session_factory)
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        listed = _people(client)
        by_declined_form = _people(client, q="Моору")
        nobody = _people(client, q="Петров")

    # Surname first, as the candidates are; the name opens the dossier.
    assert _row(listed, "Моор Александр")["dossier_url"] == f"/ui/investigations/{MOOR}"
    assert listed["total"] == 2
    assert by_declined_form["total"] == 1 and _names(by_declined_form) == ["Моор Александр"]
    assert nobody["total"] == 0


def test_a_card_marks_each_mention_and_lists_the_people_named_beside(
    session_factory: sessionmaker[Session],
) -> None:
    _collected(session_factory)
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        card = _dossier(client, "александр моор")
        missing = client.get("/api/v1/investigations/" + quote("никто никакой"))

    # Each mention is marked in its publication's quote.
    marked = {item["quote"][item["start"] : item["end"]] for item in card["publications"]}
    assert marked == {"Александра Моора", "Александру Моору"}
    assert [(person["name"], person["shared"]) for person in card["related"]] == [
        ("Иванов Иван", 1)
    ]
    assert {"kind": "arrest", "label": "Арест", "count": 1} in card["events"]
    assert missing.status_code == 404


def test_a_name_a_model_gave_is_marked(session_factory: sessionmaker[Session]) -> None:
    _collected(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE entity_groups SET name_source = 'model', gender = 'male' "
                "WHERE key = 'александр моор'"
            )
        )
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        listed = _people(client)
        card = _dossier(client, "александр моор")
        other = _dossier(client, "иван иванов")

    assert _row(listed, "Моор Александр")["name_source_label"] == "ИИ"
    assert _row(listed, "Иванов Иван")["name_source_label"] is None
    assert (card["name_source_label"], card["gender_label"]) == ("дала модель", "мужчина")
    assert other["name_source_label"] == "по правилам склейки"


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
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        listed = _people(client, sort="name")
        card = _dossier(client, "борис абрамов")

    assert _names(listed) == ["Абрамов Борис Петрович", "Моор Вера", "Яковлева Анна"]
    assert card["name"] == "Абрамов Борис Петрович"


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
    from web.views.entities import display_name

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
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        listed = _people(client, sort="name")

    assert _names(listed) == ["С. Дмитрий", "Соломатин П.", "Яковлева Анна"]


def _law(session: Session, seed: DatabaseSeeder, run: int, surface: str, article: str) -> int:
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
        seed = DatabaseSeeder(session)
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
    EntityCollector(session_factory, downstream=GroupFollowers()).run()


def test_a_card_lists_the_criminal_code_articles_with_their_publications(
    session_factory: sessionmaker[Session],
) -> None:
    _charged(session_factory)
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        moor = _dossier(client, "александр моор")
        ivanov = _dossier(client, "иван иванов")

    assert moor["charges"] == [
        {"article": "205.2", "parts": ["2"], "shared": False, "political": True, "publications": 1}
    ]
    (item,) = moor["timeline"]
    assert item["sources"][0]["quote"] == "Суд арестовал Александра Моора по ч. 2 ст. 205.2 УК РФ."
    # Named beside another target: shown, but marked shared; the only target is not.
    assert [(charge["article"], charge["shared"]) for charge in ivanov["charges"]] == [
        ("207.3", True)
    ]


def test_the_list_shows_the_articles_and_filters_by_one(
    session_factory: sessionmaker[Session],
) -> None:
    _charged(session_factory)
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        listed = _people(client)
        by_article = _people(client, article="207.3")

    # Charged alone: the article is his; named beside another target: shared.
    assert _row(listed, "Моор Александр")["articles"] == [{"article": "205.2", "shared": False}]
    assert _row(listed, "Иванов Иван")["articles"] == [{"article": "207.3", "shared": True}]
    assert listed["total"] == 3
    assert by_article["total"] == 2
    assert sorted(_names(by_article)) == ["Иванов Иван", "Петров Петр"]


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
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        listed = _people(client)
        hidden = _people(client, rf="hide")
        both = _people(client, rf="hide", rf_possible="hide")
        card = _dossier(client, "александр моор")

    # The list confirms who a person is: shown by default, hidden only on request.
    assert listed["total"] == 2
    moor = _row(listed, "Моор Александр Петрович")
    assert (moor["rf_level"], moor["rf_label"]) == ("full", "в перечне")
    # A name without a patronymic may be a namesake: shown, marked.
    assert _row(listed, "Иванов Иван")["rf_label"] == "возможно в перечне"
    assert listed["hidden_in_list"] == 1
    # The second choice, off by default, hides the maybe-namesakes too; Моор is on the list
    # for certain, so his patronymic-less namesake entry does not count him again.
    assert listed["hidden_maybe_listed"] == 1
    assert hidden["total"] == 1 and _names(hidden) == ["Иванов Иван"]
    assert both["total"] == 0
    assert card["rf_label"] == "в перечне Росфинмониторинга"
    assert any(
        "МООР АЛЕКСАНДР ПЕТРОВИЧ, 01.02.1980 г.р., Г. МОСКВА" in entry["text"]
        for entry in card["rf_entries"]
    )


class _Roles:
    model = "fake-model"

    def classify(self, items: Sequence[RoleItem]) -> dict[int, RoleAnswer]:
        kinds: dict[str, Kind] = {"Александр Моор": "accused", "Иван Иванов": "administrative"}
        return {
            item.id: RoleAnswer(
                id=item.id, source=item.name, kind=kinds[item.name], explanation="по цитате"
            )
            for item in items
        }


def test_only_the_figurants_are_listed_by_default(
    session_factory: sessionmaker[Session],
) -> None:
    _collected(session_factory)
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    with _client(session_factory, registry) as client:
        before = _people(client)
    FigurantFinder(session_factory, classifier=_Roles(), downstream=GroupFollowers()).run()

    with _client(session_factory, registry) as client:
        figurants = _people(client)
        everyone = _people(client, role="all")
        card = _dossier(client, "александр моор")

    # Before step 5 nobody has a role: all are shown, and the list says why.
    assert before["roles_known"] is False and before["total"] == 2
    assert figurants["roles_known"] is True and figurants["total"] == 1
    assert _names(figurants) == ["Моор Александр"]
    assert _row(figurants, "Моор Александр")["role_label"] == "фигурант дела"
    # An administrative case is no criminal one: not a figurant, and says so.
    assert everyone["total"] == 2
    assert _row(everyone, "Иванов Иван")["role_label"] == "административное дело"
    assert card["role_label"] == "фигурант дела"
    assert "ответ модели по цитатам" in (card["role_method_label"] or "")


def test_the_region_of_a_registry_card_is_shown(session_factory: sessionmaker[Session]) -> None:
    _collected(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE entity_groups SET regions = '[[\"Луганская область\", 1]]' WHERE key = :key"
            ),
            {"key": "александр моор"},
        )
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        listed = _people(client)
        card = _dossier(client, "александр моор")

    assert _row(listed, "Моор Александр")["regions"] == ["Луганская область"]
    assert card["regions"] == ["Луганская область"]


def _mark_of(session_factory: sessionmaker[Session]) -> bool | None:
    """Whether Moor is marked an official right now, read while the client is still up."""
    with session_factory() as session:
        mark: bool | None = session.scalar(
            text("SELECT official FROM entity_official_marks WHERE key = 'александр моор'")
        )
        return mark


def test_an_official_is_marked_on_the_card_and_unmarked_there(
    session_factory: sessionmaker[Session],
) -> None:
    """Marking a person an official, and taking it back, happens on their card.

    The officials are a reference list on «Справочники», not a page of their own, so
    what this checks is the mark itself and its effect, not where it is listed."""
    _collected(session_factory)
    FigurantFinder(session_factory, classifier=_Roles(), downstream=GroupFollowers()).run()
    with session_factory.begin() as session:
        moor = session.scalar(text("SELECT id FROM entity_groups WHERE key = 'александр моор'"))
        session.add(
            EntityGroupPoliticsRecord(
                group_id=moor, verdict="political", method="model", reason="—", quote=""
            )
        )
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        card = _dossier(client, "александр моор")
        listed = [row["name"] for row in client.get("/api/v1/political").json()["items"]]
        marked = client.post("/api/v1/entities/official", json={"key": KEY, "official": True})
        card_after_mark = _dossier(client, "александр моор")
        official_after_mark = _mark_of(session_factory)
        unmarked = client.post("/api/v1/entities/official", json={"key": KEY, "official": False})
        card_after_unmark = _dossier(client, "александр моор")
        official_after_unmark = _mark_of(session_factory)
        missing = client.post("/api/v1/entities/official", json={"key": "никто", "official": True})

    assert card["official"] is False
    assert marked.json() == {"key": KEY, "official": True}
    # The card now says he is an official, and why.
    assert card_after_mark["official"] is True
    assert card_after_mark["role_label"] == "упомянут: чиновник"
    assert card_after_mark["role_method_label"] == "должностное лицо"
    assert official_after_mark is True
    # An official leaves «Список» at once.
    assert "Моор Александр" in listed
    with session_factory() as session:
        assert session.scalar(text("SELECT count(*) FROM entity_group_politics")) == 0
    # Taking it back puts the card as it was, and the mark is cleared: the row stays,
    # it no longer says he is an official.
    assert unmarked.json() == {"key": KEY, "official": False}
    assert official_after_unmark is False
    assert card_after_unmark["official"] is False
    assert missing.status_code == 404 and missing.json() == {"detail": "Человек не найден"}


def test_a_name_is_corrected_on_the_card(session_factory: sessionmaker[Session]) -> None:
    _collected(session_factory)
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        corrected = client.post(
            "/api/v1/entities/name", json={"key": KEY, "name": "Моор Александр Викторович"}
        )
        after = _dossier(client, "александр моор")
        listed = _row(_people(client, role="all"), "Моор Александр Викторович")
        empty = client.post("/api/v1/entities/name", json={"key": KEY, "name": "   "})
        missing = client.post("/api/v1/entities/name", json={"key": "никто", "name": "Кто-то"})

    # The name as kept: given name first.
    assert corrected.json() == {"key": KEY, "name": "Александр Викторович Моор"}
    # Written surname first, kept given name first, shown surname first.
    assert after["name"] == "Моор Александр Викторович"
    assert after["name_as_kept"] == "Александр Викторович Моор"
    assert after["name_source_label"] == "исправлено вручную"
    assert listed["name_source_label"] == "исправлено"
    assert empty.status_code == 400 and missing.status_code == 404


def test_a_corrected_name_that_is_another_entity_s_merges_them(
    session_factory: sessionmaker[Session],
) -> None:
    _collected(session_factory)
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        client.post("/api/v1/entities/name", json={"key": KEY, "name": "Иван Иванов"})
        listed = _people(client, role="all")

    assert listed["total"] == 1 and _names(listed) == ["Иванов Иван"]
    with session_factory() as session:
        assert session.scalar(text("SELECT mention_count FROM entity_groups")) == 3
