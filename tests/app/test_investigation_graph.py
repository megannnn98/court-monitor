"""The graph of an investigation: what the events state, and nothing more."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import EntityMentionRecord
from entities.collector import EntityCollector
from web import investigation_graph
from web.app import app
from web.dependencies import get_db
from web.investigation_graph import NotExpandable, UnknownNode, expand, initial_graph

IVANOV = "иван иванов"
PETROV = "петр петров"
DETENTION = "Ивана Иванова и Петра Петрова задержали по ч. 1 ст. 280.3 УК РФ."
TEXT = f"Вступление. {DETENTION} Дело ведёт Следственный комитет, арест избрал Тверской суд."


def _person(
    session: Session, seed: DatabaseSeeder, run: int, surface: str, first: str, last: str
) -> int:
    mention = seed.mention(run, surface, person_id=None)
    session.get_one(EntityMentionRecord, mention).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": None,
    }
    return mention


def _law(session: Session, seed: DatabaseSeeder, run: int, surface: str, **data: Any) -> int:
    mention = seed.mention(run, surface, person_id=None, entity_type="legal_reference")
    session.get_one(EntityMentionRecord, mention).normalized_data = {
        "code": "УК РФ",
        "article": "280.3",
        "part": "1",
        "clause": None,
        **data,
    }
    return mention


def _seed(session_factory: sessionmaker[Session], *, arrests: int = 1) -> dict[str, int]:
    """Иванов and Петров are detained in one event (a court, a body, an article of the
    Code). Иванов stands in the publication of Смирнова's arrest, named in no event of it.
    Петров is
    arrested `arrests` times, each in a publication of its own. Сидоров is named in the
    detention but step 3 makes no person of a mention it is not given."""
    ids: dict[str, int] = {}
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("ОВД-Инфо <b>", "https://ovd.info")
        article, run = seed.article(
            source,
            external_id="detention",
            title="Задержание <двоих>",
            text=TEXT,
            published_at=datetime(2026, 6, 14, 12, tzinfo=UTC),
        )
        ids["detention_article"] = article
        ids["detention"] = seed.event(
            run,
            DETENTION,
            event_type="detention",
            event_date=datetime(2026, 6, 14, 12, tzinfo=UTC),
            links=[],
            entity_links=[
                (_person(session, seed, run, "Ивана Иванова", "Иван", "Иванов"), "target"),
                (_person(session, seed, run, "Петра Петрова", "Пётр", "Петров"), "target"),
                (_law(session, seed, run, "ч. 1 ст. 280.3 УК РФ"), "legal_basis"),
                (seed.mention(run, "Тверской суд", person_id=None, entity_type="court"), "court"),
                (
                    seed.mention(
                        run, "Следственный комитет", person_id=None, entity_type="organization"
                    ),
                    "authority",
                ),
            ],
        )
        _, run = seed.article(
            source,
            external_id="hearing",
            title="Заседание",
            text="Иван Иванов пришёл на заседание. Анну Смирнову арестовали.",
            published_at=datetime(2026, 6, 20, tzinfo=UTC),
        )
        _person(session, seed, run, "Иван Иванов", "Иван", "Иванов")
        ids["smirnova_arrest"] = seed.event(
            run,
            "Анну Смирнову арестовали.",
            event_type="arrest",
            event_date=None,
            links=[],
            entity_links=[
                (_person(session, seed, run, "Анну Смирнову", "Анна", "Смирнова"), "target")
            ],
        )
        for number in range(arrests):
            _, run = seed.article(
                source,
                external_id=f"arrest-{number}",
                title=f"Арест {number}",
                text=f"Петра Петрова арестовали по ст. 20.2 КоАП РФ, раз {number}.",
                published_at=datetime(2026, 7, 1 + number, tzinfo=UTC),
            )
            ids[f"arrest-{number}"] = seed.event(
                run,
                "Петра Петрова арестовали",
                event_type="arrest",
                event_date=None,
                links=[],
                entity_links=[
                    (_person(session, seed, run, "Петра Петрова", "Пётр", "Петров"), "target"),
                    (
                        _law(
                            session,
                            seed,
                            run,
                            "ст. 20.2 КоАП РФ",
                            code="КоАП РФ",
                            article="20.2",
                            part=None,
                        ),
                        "legal_basis",
                    ),
                ],
            )
        session.commit()
    EntityCollector(session_factory).run()
    with session_factory() as session:
        for key, group in session.execute(text("SELECT key, id FROM entity_groups")).all():
            ids[key] = group
    return ids


def _by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def _edges(graph: dict[str, Any], edge_type: str | None = None) -> list[tuple[str, str, str]]:
    return sorted(
        (edge["from"], edge["to"], edge["type"])
        for edge in graph["edges"]
        if edge_type is None or edge["type"] == edge_type
    )


def test_the_first_answer_is_the_person_and_their_events(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    ivanov, detention = f"person:{IVANOV}", f"event:{ids['detention']}"

    with session_factory() as session:
        graph = initial_graph(session, IVANOV)

    nodes = _by_id(graph["nodes"])
    assert graph["center"] == ivanov and graph["more"] == []
    assert nodes[ivanov] == {
        "id": ivanov,
        "type": "person",
        "label": "Иванов Иван",
        "href": "/ui/investigations/%D0%B8%D0%B2%D0%B0%D0%BD%20%D0%B8%D0%B2%D0%B0%D0%BD%D0%BE%D0%B2",
        "expandable": True,
    }
    start = TEXT.index(DETENTION)
    assert nodes[detention] == {
        "id": detention,
        "type": "event",
        "label": "Задержание",
        "event_type": "detention",
        "date": "2026-06-14",
        "dated": True,
        "confidence": 0.8,
        "publication": f"publication:{ids['detention_article']}",
        "publication_title": "Задержание <двоих>",
        "publication_source": "ОВД-Инфо <b>",
        # The event leads to its very words in the publication.
        "href": f"/ui/articles/{ids['detention_article']}?start={start}&end={start + len(DETENTION)}",
        "expandable": True,
    }
    assert _edges(graph, "target") == [(ivanov, detention, "target")]
    edge = next(edge for edge in graph["edges"] if edge["type"] == "target")
    assert (edge["label"], edge["source"]) == ("назван в событии", "extracted")
    # Nothing of the event's surroundings is read before it is asked for.
    assert {node["type"] for node in graph["nodes"]} == {"person", "event"}


def test_people_are_never_connected_as_a_fact(session_factory: sessionmaker[Session]) -> None:
    ids = _seed(session_factory)
    ivanov = f"person:{IVANOV}"

    with session_factory() as session:
        first = initial_graph(session, IVANOV)
        opened = expand(session, f"event:{ids['detention']}")

    people = {node["id"] for node in first["nodes"] + opened["nodes"] if node["type"] == "person"}
    between = [
        edge
        for edge in first["edges"] + opened["edges"]
        if edge["from"] in people and edge["to"] in people
    ]
    # Петров shares the detention's publication; Смирнова a publication where Иванов is
    # named in no event.
    assert sorted(
        (edge["to"], edge["type"], edge["source"], edge["label"]) for edge in between
    ) == sorted(
        (f"person:{key}", "cooccurrence", "derived", "общих публикаций: 1")
        for key in ("анна смирнов", PETROV)
    )
    assert all(edge["from"] == ivanov and edge["shared_publications"] == 1 for edge in between)


def test_an_event_opens_into_what_it_names(session_factory: sessionmaker[Session]) -> None:
    ids = _seed(session_factory)
    detention = f"event:{ids['detention']}"
    publication = f"publication:{ids['detention_article']}"
    start = TEXT.index(DETENTION)

    with session_factory() as session:
        graph = expand(session, detention)

    nodes = _by_id(graph["nodes"])
    assert _edges(graph) == [
        (detention, "article:УК:280.3", "legal_basis"),
        (detention, "org:authority:следственный комитет", "authority"),
        (detention, "org:court:тверской суд", "court"),
        (detention, publication, "evidence"),
        (f"person:{IVANOV}", detention, "target"),
        (f"person:{PETROV}", detention, "target"),
    ]
    assert nodes[publication] == {
        "id": publication,
        "type": "publication",
        "label": "Задержание <двоих>",
        "publication_source": "ОВД-Инфо <b>",
        "date": "2026-06-14",
        "href": f"/ui/articles/{ids['detention_article']}",
        "expandable": False,
    }
    edges = _by_id(graph["edges"])
    assert edges[f"{detention}>{publication}:evidence"]["href"] == (
        f"/ui/articles/{ids['detention_article']}?start={start}&end={start + len(DETENTION)}"
    )
    assert (nodes["org:court:тверской суд"]["type"], nodes["org:court:тверской суд"]["label"]) == (
        "court",
        "Тверской суд",
    )
    assert nodes["org:authority:следственный комитет"]["type"] == "authority"
    article = nodes["article:УК:280.3"]
    assert (article["type"], article["label"], article["expandable"]) == (
        "criminal_article",
        "ст. 280.3 УК РФ",
        False,
    )
    assert article["href"] == "/ui/entities?article=280.3&figurants=all&rf=all"
    assert edges[f"{detention}>article:УК:280.3:legal_basis"]["title"] == "ч. 1"
    assert graph["more"] == [] and "center" not in graph


def test_a_person_opens_into_their_events_and_an_administrative_article_is_no_crime(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    petrov = f"person:{PETROV}"

    with session_factory() as session:
        graph = expand(session, petrov)
        arrest = expand(session, f"event:{ids['arrest-0']}")

    # The arrest has no date of its own: it is dated by its publication, and says so.
    assert _edges(graph) == [
        (petrov, f"event:{ids['detention']}", "target"),
        (petrov, f"event:{ids['arrest-0']}", "target"),
    ]
    nodes = _by_id(graph["nodes"])
    assert (
        nodes[f"event:{ids['arrest-0']}"]["date"],
        nodes[f"event:{ids['arrest-0']}"]["dated"],
    ) == (
        "2026-07-01",
        False,
    )
    koap = _by_id(arrest["nodes"])["article:КоАП:20.2"]
    assert (koap["type"], koap["label"], koap["href"]) == (
        "administrative_article",
        "ст. 20.2 КоАП РФ",
        None,
    )
    # No part in the reference: the edge says the reference as the text wrote it.
    assert _by_id(arrest["edges"])[f"event:{ids['arrest-0']}>article:КоАП:20.2:legal_basis"][
        "title"
    ] == ("ст. 20.2 КоАП РФ")


def test_the_role_is_the_one_the_extraction_gave(session_factory: sessionmaker[Session]) -> None:
    """Only `target` is written today; another role is shown as it is, from either end."""
    ids = _seed(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE event_entity_mentions SET role = 'witness' WHERE event_id = :event "
                "AND mention_id IN (SELECT mention_id FROM entity_group_mentions WHERE group_id = :group)"
            ),
            {"event": ids["detention"], "group": ids[PETROV]},
        )
    petrov, detention = f"person:{PETROV}", f"event:{ids['detention']}"

    with session_factory() as session:
        person = expand(session, petrov)
        event = expand(session, detention)

    assert (petrov, detention, "witness") in _edges(person)
    assert (petrov, detention, "witness") in _edges(event)
    assert (f"person:{IVANOV}", detention, "target") in _edges(event)
    assert _by_id(person["edges"])[f"{petrov}>{detention}:witness"]["label"] == "witness"


def test_opening_twice_gives_the_same_ids(session_factory: sessionmaker[Session]) -> None:
    ids = _seed(session_factory)

    with session_factory() as session:
        first = initial_graph(session, IVANOV)
        once = expand(session, f"event:{ids['detention']}")
        twice = expand(session, f"event:{ids['detention']}")
        person = expand(session, f"person:{IVANOV}")

    assert once == twice
    for graph in (first, once, person):
        assert len({node["id"] for node in graph["nodes"]}) == len(graph["nodes"])
        assert len({edge["id"] for edge in graph["edges"]}) == len(graph["edges"])
    # The same event and the same edge, whichever way they were reached.
    assert (
        _by_id(first["nodes"])[f"event:{ids['detention']}"]
        == (_by_id(once["nodes"])[f"event:{ids['detention']}"])
    )
    assert {edge["id"] for edge in person["edges"]} <= {edge["id"] for edge in first["edges"]}
    assert {edge["id"] for edge in first["edges"] if edge["type"] == "target"} <= {
        edge["id"] for edge in once["edges"]
    }


def test_the_limits_cut_and_count_what_they_cut(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(session_factory, arrests=4)
    monkeypatch.setattr(investigation_graph, "INITIAL_EVENTS", 2)
    monkeypatch.setattr(investigation_graph, "PERSON_EVENTS", 3)
    monkeypatch.setattr(investigation_graph, "EVENT_PEOPLE", 1)
    monkeypatch.setattr(investigation_graph, "COOCCURRENCE_PEOPLE", 1)
    petrov = f"person:{PETROV}"

    with session_factory() as session:
        first = initial_graph(session, PETROV)
        person = expand(session, petrov)
        event = expand(session, f"event:{ids['detention']}")

    # Five events in all; the latest are kept.
    assert first["more"] == [{"node": petrov, "type": "event", "count": 3}]
    assert _edges(first, "target") == [
        (petrov, f"event:{ids['arrest-2']}", "target"),
        (petrov, f"event:{ids['arrest-3']}", "target"),
    ]
    assert len(_edges(first, "cooccurrence")) == 1
    assert person["more"] == [{"node": petrov, "type": "event", "count": 2}]
    assert len(_edges(event, "target")) == 1
    assert event["more"] == [{"node": f"event:{ids['detention']}", "type": "person", "count": 1}]


def test_a_name_step_3_made_no_person_of_is_counted_not_drawn(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("DELETE FROM entity_group_mentions WHERE group_id = :group"),
            {"group": ids[PETROV]},
        )

    with session_factory() as session:
        graph = expand(session, f"event:{ids['detention']}")

    assert _edges(graph, "target") == [(f"person:{IVANOV}", f"event:{ids['detention']}", "target")]
    assert graph["more"] == [
        {"node": f"event:{ids['detention']}", "type": "unresolved_person", "count": 1}
    ]


def test_one_court_and_one_article_are_one_node_each_however_written(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The court named twice in the event is one court, counted once. «УК» and «УК РФ» are
    one Code, as step 3 reads them. A reference that names no Code is not drawn: which Code
    it is would be a guess."""
    ids = _seed(session_factory)
    with session_factory.begin() as session:
        run = session.scalar(
            text("SELECT extraction_run_id FROM extracted_events WHERE id = :event"),
            {"event": ids["detention"]},
        )
        for offset, (kind, surface, data, role) in enumerate(
            [
                ("court", "Тверской суд", {}, "court"),
                (
                    "legal_reference",
                    "ст. 280.3 УК",
                    {"code": "УК", "article": "280.3"},
                    "legal_basis",
                ),
                ("legal_reference", "ст. 280.3", {"code": "", "article": "280.3"}, "legal_basis"),
            ]
        ):
            mention = EntityMentionRecord(
                extraction_run_id=run,
                entity_type=kind,
                surface_text=surface,
                normalized_text=surface.lower(),
                start_offset=500 + offset,
                end_offset=510 + offset,
                confidence=0.9,
                normalized_data=data,
                extractor_name="rule-based",
                extractor_version="1.0.0",
                normalizer_version="1.0.0",
                person_id=None,
            )
            session.add(mention)
            session.flush()
            session.execute(
                text(
                    "INSERT INTO event_entity_mentions (event_id, mention_id, role) "
                    "VALUES (:event, :mention, :role)"
                ),
                {"event": ids["detention"], "mention": mention.id, "role": role},
            )
    monkeypatch.setattr(investigation_graph, "EVENT_NEIGHBOURS", 2)

    with session_factory() as session:
        graph = expand(session, f"event:{ids['detention']}")

    kinds = [node["type"] for node in graph["nodes"]]
    assert (kinds.count("court"), kinds.count("authority"), kinds.count("criminal_article")) == (
        1,
        1,
        1,
    )
    assert "administrative_article" not in kinds
    # Two bodies within a limit of two: nothing is cut, though three mentions name them.
    assert graph["more"] == []
    article = _by_id(graph["nodes"])["article:УК:280.3"]
    assert article["label"] == "ст. 280.3 УК РФ"


def test_a_person_is_named_by_the_key_that_survives_a_rebuild(
    session_factory: sessionmaker[Session],
) -> None:
    """Step 3 rebuilds the groups and hands the ids out anew. A page left open asks for
    the person by the key, so it opens the same person — never another one's events."""
    ids = _seed(session_factory)
    with session_factory() as session:
        before = expand(session, f"person:{PETROV}")
        opened = expand(session, f"event:{ids['detention']}")
    EntityCollector(session_factory).run()

    with session_factory() as session:
        rebuilt = session.scalar(
            text("SELECT id FROM entity_groups WHERE key = :key"), {"key": PETROV}
        )
        after = expand(session, f"person:{PETROV}")
        assert expand(session, f"event:{ids['detention']}") == opened

    assert rebuilt != ids[PETROV], "the rebuild is expected to hand out new ids"
    assert after == before and before["nodes"][0]["id"] == f"person:{PETROV}"


def test_more_courts_than_the_limit_are_counted(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(session_factory)
    monkeypatch.setattr(investigation_graph, "EVENT_NEIGHBOURS", 1)

    with session_factory() as session:
        graph = expand(session, f"event:{ids['detention']}")

    assert len([node for node in graph["nodes"] if node["type"] in ("court", "authority")]) == 1
    assert graph["more"] == [
        {"node": f"event:{ids['detention']}", "type": "organization", "count": 1}
    ]


def test_what_is_not_there_and_what_does_not_open(session_factory: sessionmaker[Session]) -> None:
    ids = _seed(session_factory)

    with session_factory() as session:
        with pytest.raises(UnknownNode):
            initial_graph(session, "никто")
        for missing in ("person:никто", "event:999999"):
            with pytest.raises(UnknownNode):
                expand(session, missing)
        for closed in (
            f"publication:{ids['detention_article']}",
            "org:court:тверской суд",
            "event:x",
            "person:",
            # Digits to `str.isdigit`, not to `int`; and digits that are not the database's.
            "event:²",
            "event:٣",
            # Longer than any id of the database: refused, not an overflow in the query.
            "event:99999999999999999999",
            "",
        ):
            with pytest.raises(NotExpandable):
                expand(session, closed)


def test_the_queries_do_not_grow_with_the_case(session_factory: sessionmaker[Session]) -> None:
    ids = _seed(session_factory, arrests=6)

    def counted(work: Any) -> int:
        with session_factory() as session:
            statements: list[str] = []
            connection = session.connection()
            sa_event.listen(
                connection, "before_cursor_execute", lambda *args: statements.append(args[2])
            )
            work(session)
            return len(statements)

    assert counted(lambda session: initial_graph(session, PETROV)) == 3
    assert counted(lambda session: expand(session, f"event:{ids['detention']}")) == 2
    assert counted(lambda session: expand(session, f"person:{PETROV}")) == 2


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


def test_the_api_serves_the_graph_and_says_what_is_wrong(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)

    with _client(session_factory) as client, session_factory() as session:
        first = client.get(f"/api/investigations/{IVANOV}/graph")
        opened = client.get(
            f"/api/investigations/{IVANOV}/graph/expand",
            params={"node": f"event:{ids['detention']}"},
        )
        assert first.json() == initial_graph(session, IVANOV)
        assert opened.json() == expand(session, f"event:{ids['detention']}")
        assert client.get("/api/investigations/никто/graph").status_code == 404
        assert (
            client.get(
                "/api/investigations/никто/graph/expand", params={"node": "event:1"}
            ).status_code
            == 404
        )
        base = f"/api/investigations/{IVANOV}/graph/expand"
        assert client.get(base, params={"node": "event:999999"}).status_code == 404
        assert client.get(base, params={"node": "org:court:x"}).status_code == 400
        assert client.get(base).status_code == 422
        assert client.get(base, params={"node": "event:²"}).status_code == 400
        assert client.get(base, params={"node": f"person:{PETROV}"}).status_code == 200
    assert first.status_code == 200 and opened.status_code == 200
