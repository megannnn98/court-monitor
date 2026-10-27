"""«Следователь»: the overview, the dossier of a person, the people, the publications and the
operator's queue, on PostgreSQL with real rows."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from analysis.service import GroupFollowers
from analysis.storage.models import EntityGroupPoliticsRecord, EntityGroupRfMatchRecord
from orchestrator.operations import OperationRegistry
from processing.identity.collector import EntityCollector
from processing.storage.models import (
    EntityGroupRoleRecord,
    EntityMentionRecord,
    EntityPairDecisionRecord,
)
from web.app import app
from web.dependencies import OPERATION_COMMAND_LINE, get_db, get_operation_registry

MOOR = "александр моор"
ARREST = "Суд арестовал Александра Моора по ч. 2 ст. 205.2 УК РФ."
QUOTE = "Александра Моора арестовали за антивоенные посты"


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

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
            # The text names no date of the verdict, so the event has none; the page places
            # it by the publication's and says so.
            event_date=None,
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
    EntityCollector(session_factory, downstream=GroupFollowers()).run()
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


def _dossier(client: TestClient, key: str = MOOR) -> dict[str, Any]:
    """The dossier as the React page reads it."""
    dossier: dict[str, Any] = client.get(f"/api/v1/investigations/{quote(key)}").json()
    return dossier


def test_the_overview_is_what_is_new(session_factory: sessionmaker[Session]) -> None:
    _case(session_factory)
    _news(session_factory, MOOR, "new_case", "арестован по новому делу")
    # Иванов's news is no political case's: not on the overview.
    _news(session_factory, "иван иванов", "sentence", "приговор свидетелю")

    with _client(session_factory) as client:
        overview = client.get("/api/v1/overview").json()

    assert overview["new_cases"] == 1
    (new_case,) = overview["latest_new_cases"]
    assert (new_case["key"], new_case["name"], new_case["reason"]) == (
        MOOR,
        "Моор Александр",
        "арестован по новому делу",
    )
    assert overview["sentences"] == 0 and overview["latest_sentences"] == []


def test_a_dossier_opens_and_an_unknown_person_is_not_found(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)

    with _client(session_factory) as client:
        dossier = _dossier(client)
        missing = client.get("/api/v1/investigations/" + quote("никто никакой"))

    assert dossier["name"] == "Моор Александр"
    assert (dossier["article_count"], dossier["mention_count"]) == (3, 3)
    assert dossier["first_published_at"].startswith("2026-09-01")
    assert missing.status_code == 404


def test_the_timeline_is_in_order_and_one_event_told_twice_is_one_item(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)

    with _client(session_factory) as client:
        timeline = _dossier(client)["timeline"]

    # The arrest of 01.09, which the text dated, comes first by that date; the verdict
    # names no date of its own, and says so rather than passing the publication's date off
    # as the event's.
    assert [(item["label"], item["dated"]) for item in timeline] == [
        ("Арест", True),
        ("Приговор", False),
    ]
    arrest, verdict = timeline
    assert arrest["day"].startswith("2026-09-01")
    assert verdict["day"].startswith("2026-09-10")
    # Two publications of the arrest, one item with two sources.
    assert len(arrest["sources"]) == 2
    assert arrest["orgs"] == [{"name": "Ленинский суд", "role_label": "суд"}]
    assert arrest["articles"] == ["205.2"]


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
        publications = _dossier(client)["publications"]

    arrest = next(item for item in publications if item["article_id"] == article)
    # The mention is marked in the quote, and the text opens at it.
    assert arrest["quote"][arrest["start"] : arrest["end"]] == "Александра Моора"
    assert (arrest["text_start"], arrest["text_end"]) == (start, end)
    assert (arrest["title"], arrest["source"]) == ("Арест Моора", "ОВД-Инфо")
    assert arrest["published_at"].startswith("2026-09-01")
    assert arrest["url"] == "https://example.test/arrest"
    # The other person of a publication.
    assert "Иванов Иван" in [other["name"] for item in publications for other in item["others"]]


def test_the_decision_panel_shows_the_verdict_its_reason_and_its_source(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    with session_factory() as session:
        source = session.scalar(text("SELECT id FROM parsed_articles WHERE title = 'Арест Моора'"))

    with _client(session_factory) as client:
        dossier = _dossier(client)

    assert dossier["verdict_label"] == "политическое"
    assert dossier["verdict_method_label"] == "модель по цитатам из публикаций"
    assert dossier["verdict_reason"] == "Преследование за антивоенные посты."
    assert dossier["verdict_quote"] == QUOTE
    # The quote leads to its publication in one step.
    assert dossier["verdict_source_article_id"] == source


def test_the_rosfinmonitoring_status_is_shown_as_it_is(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    with _client(session_factory) as client:
        clear = _dossier(client)
    _listed(session_factory, "name")
    with _client(session_factory) as client:
        maybe = _dossier(client)
    with session_factory.begin() as session:
        session.execute(text("UPDATE entity_group_rf_matches SET level = 'full'"))
    with _client(session_factory) as client:
        listed = _dossier(client)

    assert clear["rf_label"] == "не найден в перечне" and not clear["rf_maybe"]
    assert maybe["rf_maybe"] is True and "тёзка" in maybe["rf_label"]
    assert any("МООР АЛЕКСАНДР ПЕТРОВИЧ" in entry["text"] for entry in maybe["rf_entries"])
    # On the list: who the person is, not a mark against them.
    assert listed["rf_label"] == "в перечне Росфинмониторинга" and not listed["rf_maybe"]


def test_the_latest_news_is_named_in_the_dossier_and_the_overview(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    with _client(session_factory) as client:
        unread = _dossier(client)
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
        dossier = _dossier(client)
        overview = client.get("/api/v1/overview").json()

    assert unread["news"] is None
    assert dossier["news"] == {
        "kind": "sentence",
        "label": "приговор",
        "reason": "суд вынес приговор",
    }
    assert "суд вынес приговор" in [item["reason"] for item in overview["latest_sentences"]]


def _known(session_factory: sessionmaker[Session], *names: str) -> None:
    from analysis.storage.models import AirtableKnownPersonRecord

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
        no_base = _dossier(client)["known"]
    _known(session_factory, "Кто-то Другой")
    with _client(session_factory) as client:
        absent = _dossier(client)["known"]
    _known(session_factory, "Моор Александр Петрович")
    with _client(session_factory) as client:
        present = _dossier(client)["known"]

    # No base synced: the dossier says nothing, rather than «not in the base».
    assert no_base["loaded"] is False and no_base["not_in_base"] is False
    assert absent["loaded"] is True and absent["not_in_base"] is True
    assert present["not_in_base"] is False and present["label"] == "вероятно, есть в базе"
    assert present["names"] == ["Моор Александр Петрович"]


def test_the_dossier_names_the_graph_the_api_serves(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)

    with _client(session_factory) as client:
        address = _dossier(client)["graph_url"]
        graph = client.get(address)

    assert (
        address
        == "/api/investigations/%D0%B0%D0%BB%D0%B5%D0%BA%D1%81%D0%B0%D0%BD%D0%B4%D1%80%20%D0%BC%D0%BE%D0%BE%D1%80/graph"
    )
    assert graph.status_code == 200 and graph.json()["center"].startswith("person:")


def test_the_people_of_the_same_publications_are_a_count_not_a_connection(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory, extra=25)

    with _client(session_factory) as client:
        related = _dossier(client)["related"]

    # Everyone of a shared publication, with the count of the publications they share.
    assert len(related) == 26 and "Иванов Иван" in [person["name"] for person in related]
    assert all(person["shared"] >= 1 for person in related)


def test_a_cut_list_of_people_says_it_is_cut(session_factory: sessionmaker[Session]) -> None:
    _case(session_factory, extra=33)

    with _client(session_factory) as client:
        related = _dossier(client)["related"]

    # 34 people share a publication; the list holds the first 30.
    assert len(related) == 30


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
    small = _statements(session_factory, f"/api/v1/investigations/{quote(MOOR)}")
    _more(session_factory, 12, "Коммерсантъ")

    large = _statements(session_factory, f"/api/v1/investigations/{quote(MOOR)}")

    assert large == small


def test_the_pager_is_short_and_keeps_the_filters() -> None:
    from web.ui.layout import pager

    shown_pager = pager("/ui/sentences", {"reason": "all", "region": "Москва"}, 6, 12)

    shown = re.findall(r">(\d+|…|Назад|Вперёд)<", shown_pager)
    assert shown == ["Назад", "1", "…", "4", "5", "6", "7", "8", "…", "12", "Вперёд"]
    links = re.findall(r'href="(/ui/sentences\?[^"]+)"', shown_pager)
    assert links and all("reason=all" in link and "region=" in link for link in links)
    assert pager("/ui/sentences", {}, 1, 1) == ""


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


def _pair_list(client: TestClient) -> dict[str, Any]:
    listed: dict[str, Any] = client.get("/api/v1/review/pairs").json()
    return listed


def test_a_decided_pair_is_kept_and_the_next_one_is_shown(
    session_factory: sessionmaker[Session],
) -> None:
    _pairs(session_factory)

    with _client(session_factory) as client:
        first = _pair_list(client)
        decided = client.post(
            "/api/v1/review/pairs/decide",
            json={"key_a": "олег орлов", "key_b": "олег петрович орлов", "decision": "different"},
        )
        after = _pair_list(client)

    names = [(item["left"]["name"], item["right"]["name"]) for item in after["items"]]
    assert first["total"] == 2
    assert "Орлов Олег Петрович" in [item["right"]["name"] for item in first["items"]]
    assert decided.status_code == 200
    assert after["total"] == 1 and names == [("Смирнова Анна", "Смирнова Анна Ивановна")]
    with session_factory() as session:
        record = session.get(EntityPairDecisionRecord, ("олег орлов", "олег петрович орлов"))
        assert record is not None and (record.decision, record.source) == ("different", "manual")


def test_empty_states_say_so(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        overview = client.get("/api/v1/overview").json()
        pairs = _pair_list(client)
        roles = client.get("/api/v1/review/roles").json()
        politics = client.get("/api/v1/review/politics").json()
        publications = client.get("/api/v1/publications").json()
        investigations = client.get("/api/v1/investigations").json()
    _case(session_factory)
    with _client(session_factory) as client:
        witness = _dossier(client, "иван иванов")

    assert (overview["new_cases"], overview["sentences"], overview["unnamed"]) == (0, 0, 0)
    assert (overview["pairs"], overview["unclear_roles"], overview["unclear_verdicts"]) == (0, 0, 0)
    assert overview["source_errors"] == 0
    assert pairs["total"] == 0 and roles["items"] == [] and politics["items"] == []
    assert publications["total"] == 0 and investigations["items"] == []
    # A witness: in no event as a participant, and not judged by step 5.
    assert witness["timeline"] == [] and witness["verdict"] is None


def test_names_and_addresses_are_given_as_written(session_factory: sessionmaker[Session]) -> None:
    """The API gives what was loaded and typed as it is: the React pages escape it and link
    only a web address (`DossierPage.test.tsx`)."""
    _case(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE entity_groups SET name = 'Александр <b>Моор</b>' WHERE key = :key"),
            {"key": MOOR},
        )

    with _client(session_factory) as client:
        dossier = _dossier(client)

    assert dossier["name"] == "<b>Моор</b> Александр"


def test_the_old_addresses_still_answer(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        answers = {
            path: client.get(path).status_code
            for path in ("/ui/management", "/ui/logs", "/ui/runs", "/ui/wiki")
        }

    assert set(answers.values()) == {200}, answers


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
        before = _pair_list(client)
        reset = client.post("/ui/pairs/reset-decisions", follow_redirects=False)
        after = _pair_list(client)

    assert before["total"] == 0 and sum(before["decided"].values()) == 2
    assert reset.status_code == 303 and reset.headers["location"] == "/ui/pairs?reset=2"
    # Both pairs are disputed again; nothing is left to reset.
    assert after["total"] == 2 and sum(after["decided"].values()) == 0
    with session_factory() as session:
        assert session.scalar(text("SELECT count(*) FROM entity_pair_decisions")) == 0
