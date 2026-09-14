"""Deterministic semantic representations of Person and Event (PostgreSQL)."""

from __future__ import annotations

from datetime import UTC, datetime

from research_db_fixtures import ResearchSeeder
from sqlalchemy.orm import Session, sessionmaker

from semantic_retrieval.documents import (
    EVENT_REPRESENTATION_VERSION,
    PERSON_REPRESENTATION_VERSION,
    EventSemanticDocumentBuilder,
    PersonSemanticDocumentBuilder,
    compute_content_hash,
)
from semantic_retrieval.models import RetrievalEntityType

ARTICLE_TEXT = (
    "Вводная часть статьи, не относящаяся к делу, с длинным описанием контекста. "
    "Мещанский суд арестовал Ивана Иванова за пост против вторжения в Украину по статье 207.3 УК. "
    "Анну Петрову задержали в Москве на одиночном пикете. "
    "Хвост статьи, который тоже не должен попасть в представление."
)


def _seed(session_factory: sessionmaker[Session]) -> dict[str, int]:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("ОВД-Инфо", "https://ovd.info")
        _, run_id = seed.article(source_id, external_id="a1", title="Хроника", text=ARTICLE_TEXT)
        ivan = seed.person("Иван Иванов")
        anna = seed.person("Анна Петрова")
        seed.alias(ivan, "Ивана Иванова")
        seed.alias(ivan, "И. Иванов")
        seed.mention(run_id, "Ивана Иванова", person_id=ivan)
        court = seed.mention(run_id, "Мещанский суд", person_id=None, entity_type="court")
        law = seed.mention(run_id, "статье 207.3 УК", person_id=None, entity_type="legal_reference")
        moscow = seed.mention(run_id, "Москве", person_id=None, entity_type="location")
        arrest = seed.event(
            run_id,
            "Мещанский суд арестовал Ивана Иванова за пост против вторжения в Украину по статье 207.3 УК",
            event_type="arrest",
            event_date=datetime(2024, 3, 5, tzinfo=UTC),
            links=[(ivan, "subject")],
            attributes={"trigger_text": "арестовал"},
            entity_links=[(court, "court"), (law, "legal_basis")],
        )
        detention = seed.event(
            run_id,
            "Анну Петрову задержали в Москве на одиночном пикете",
            event_type="detention",
            event_date=None,
            links=[(anna, "subject")],
            entity_links=[(moscow, "location")],
        )
        seed.classification(
            ivan,
            "political",
            0.9,
            reasons=["Антивоенная деятельность", "Политическая статья: 207.3"],
            evidence_types=["anti_war_activity", "political_charge"],
        )
        session.commit()
    return {"ivan": ivan, "anna": anna, "arrest": arrest, "detention": detention}


def test_person_document_contains_names_classification_and_linked_events(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)

    (document,) = PersonSemanticDocumentBuilder(session_factory).build([ids["ivan"]])

    assert document.entity_type is RetrievalEntityType.PERSON
    assert document.entity_id == ids["ivan"]
    assert document.representation_version == PERSON_REPRESENTATION_VERSION
    assert document.text == (
        "Персона: Иван Иванов.\n"
        "Другие написания: И. Иванов; Ивана Иванова.\n"
        "Классификация преследования: политическое. "
        "Основания: Антивоенная деятельность; Политическая статья: 207.3. "
        "Признаки: антивоенная деятельность; политическое обвинение.\n"
        "События:\n"
        "- арест, 2024-03-05: Мещанский суд арестовал Ивана Иванова за пост против вторжения "
        "в Украину по статье 207.3 УК. Суд: Мещанский суд. Правовое основание: статье 207.3 УК."
    )
    assert document.content_hash == compute_content_hash(
        RetrievalEntityType.PERSON, PERSON_REPRESENTATION_VERSION, document.text
    )


def test_person_document_uses_only_data_linked_to_that_person(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)

    (anna,) = PersonSemanticDocumentBuilder(session_factory).build([ids["anna"]])

    assert "Иван" not in anna.text
    assert "арест" not in anna.text
    assert "Классификация" not in anna.text  # never classified: nothing invented
    assert "задержание, дата неизвестна: Анну Петрову задержали в Москве" in anna.text
    assert "Место: Москве." in anna.text


def test_documents_never_contain_text_outside_event_spans(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    builder = PersonSemanticDocumentBuilder(session_factory)
    events = EventSemanticDocumentBuilder(session_factory)

    documents = [*builder.build([ids["ivan"], ids["anna"]]), *events.build([ids["arrest"]])]

    for document in documents:
        assert "Вводная часть" not in document.text
        assert "Хвост статьи" not in document.text


def test_event_document_contains_type_date_span_participants_roles_and_source(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)

    (document,) = EventSemanticDocumentBuilder(session_factory).build([ids["arrest"]])

    assert document.entity_type is RetrievalEntityType.EVENT
    assert document.representation_version == EVENT_REPRESENTATION_VERSION
    assert document.text == (
        "Событие: арест, 2024-03-05.\n"
        "Фрагмент: Мещанский суд арестовал Ивана Иванова за пост против вторжения в Украину "
        "по статье 207.3 УК.\n"
        "Участники: Иван Иванов (subject).\n"
        "Суд: Мещанский суд. Правовое основание: статье 207.3 УК.\n"
        "Источник: ОВД-Инфо."
    )


def test_build_is_deterministic_regardless_of_id_order(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    builder = PersonSemanticDocumentBuilder(session_factory)

    first = builder.build([ids["anna"], ids["ivan"]])
    second = builder.build([ids["ivan"], ids["anna"]])

    assert [d.entity_id for d in first] == sorted([ids["ivan"], ids["anna"]])
    assert first == second


def test_content_hash_changes_with_linked_data(session_factory: sessionmaker[Session]) -> None:
    ids = _seed(session_factory)
    builder = PersonSemanticDocumentBuilder(session_factory)
    (before,) = builder.build([ids["anna"]])

    with session_factory() as session:
        ResearchSeeder(session).alias(ids["anna"], "Анны Петровой")
        session.commit()
    (after,) = builder.build([ids["anna"]])

    assert after.content_hash != before.content_hash
    (again,) = builder.build([ids["anna"]])
    assert again.content_hash == after.content_hash


def test_list_entity_ids_skips_inactive_persons_and_honours_limit(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    with session_factory() as session:
        ResearchSeeder(session).person("Слитый Человек", status="merged")
        session.commit()

    builder = PersonSemanticDocumentBuilder(session_factory)

    assert builder.list_entity_ids() == sorted([ids["ivan"], ids["anna"]])
    assert builder.list_entity_ids(limit=1) == [min(ids["ivan"], ids["anna"])]
    assert EventSemanticDocumentBuilder(session_factory).list_entity_ids() == sorted(
        [ids["arrest"], ids["detention"]]
    )


def test_missing_or_inactive_entities_are_not_built(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)

    assert PersonSemanticDocumentBuilder(session_factory).build([ids["ivan"] + 1000]) == []


def test_event_without_participants_or_linked_entities_has_only_its_own_lines(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("SOTA", "https://sota.vision")
        _, run_id = seed.article(
            source_id, external_id="lonely", title="t", text="В городе прошли обыски."
        )
        event_id = seed.event(
            run_id, "В городе прошли обыски", event_type="search", event_date=None, links=[]
        )
        session.commit()

    (document,) = EventSemanticDocumentBuilder(session_factory).build([event_id])

    assert document.text == (
        "Событие: обыск, дата неизвестна.\nФрагмент: В городе прошли обыски.\nИсточник: SOTA."
    )
