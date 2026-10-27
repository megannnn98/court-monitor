"""«Безымянные»: the unnamed figurants, their candidates from the list, a person's word.

Read and taken through `/api/v1/unnamed`, as the React page does; the legacy overview and
cycle pages still show what this work leaves."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from analysis.service import GroupFollowers
from analysis.storage.models import (
    AirtableKnownPersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    UnnamedFigurantRecord,
    UnnamedScreenedRecord,
)
from orchestrator.operations import OperationRegistry
from processing.identity.collector import EntityCollector
from processing.storage.models import EntityMentionRecord
from web.app import app
from web.dependencies import OPERATION_COMMAND_LINE, get_db, get_operation_registry
from web.views.workload import workload

QUOTE = "В Тюмени задержан 17-летний житель города по делу о теракте <b>на железной дороге</b>."
API = "/api/v1/unnamed"
FIGURANT = "k" * 64


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


def _list(client: TestClient, **params: str) -> dict[str, Any]:
    listed: dict[str, Any] = client.get(API, params=params).json()
    return listed


def _counts(listed: dict[str, Any]) -> dict[str, int]:
    return {option["value"]: option["count"] for option in listed["statuses"]}


def _keys(listed: dict[str, Any]) -> list[str]:
    return [item["key"] for item in listed["items"]]


def _card(listed: dict[str, Any], key: str = FIGURANT) -> dict[str, Any]:
    card: dict[str, Any] = next(item for item in listed["items"] if item["key"] == key)
    return card


def _seed(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        article, _ = seed.article(
            source,
            external_id="tyumen",
            title="Тюмень",
            text=QUOTE,
            published_at=datetime(2024, 11, 25, tzinfo=UTC),
        )
        session.add(
            UnnamedFigurantRecord(
                key=FIGURANT,
                article_id=article,
                start_offset=0,
                end_offset=len(QUOTE),
                quote=QUOTE,
                age=17,
                gender="male",
                place="Тюмень",
                initial=None,
                articles=["205"],
                event_type="detention",
                explanation="задержан по делу о теракте",
                published_at=datetime(2024, 11, 25, tzinfo=UTC),
            )
        )
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime(2024, 12, 1, tzinfo=UTC),
            source_url="https://fedsfm.test",
            content_hash="a",
            entry_count=1,
            fetched_at=datetime(2024, 12, 1, tzinfo=UTC),
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=snapshot.id,
                full_name="ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ",
                normalized_name="пуртов егор владимирович",
                matching_key="пуртовегорвладимирович",
                birth_date=datetime(2007, 2, 17, tzinfo=UTC),
                birth_place="Г. ТЮМЕНЬ ТЮМЕНСКОЙ ОБЛАСТИ",
            )
        )
        session.commit()


def test_nobody_yet_says_where_they_come_from(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        listed = _list(client)

    assert listed["none_yet"] is True and listed["items"] == []
    assert _counts(listed)["open"] == 0


def test_a_candidate_removed_from_the_list_is_shown_as_removed(
    session_factory: sessionmaker[Session],
) -> None:
    from analysis.rosfinmonitoring.operator_table import OperatorRow, store

    _seed(session_factory)
    with session_factory.begin() as session:
        store(
            session,
            [
                OperatorRow(
                    "УШЕДШИЙ ИВАН ПЕТРОВИЧ",
                    date(2007, 4, 4),
                    "Г. ТЮМЕНЬ",
                    date(2024, 11, 20),
                    True,
                    "терроризм",
                    "",
                )
            ],
        )

    with _client(session_factory) as client:
        listed = _list(client)

    gone = next(
        candidate
        for candidate in _card(listed)["rf_candidates"]
        if candidate["full_name"] == "УШЕДШИЙ ИВАН ПЕТРОВИЧ"
    )
    assert "родился: Г. ТЮМЕНЬ; исключён из перечня" in "; ".join(gone["reasons"])
    # The day is the table's and the list no longer holds him: neither is said as the list's.
    assert gone["seen"] == "включён 20.11.2024, позже исключён — по таблице оператора"
    assert gone["decision"] is None


def test_a_card_shows_the_text_what_it_tells_and_the_candidates(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        listed = _list(client)
        cycle = client.get("/ui/cycle").text

    card = _card(listed)
    assert card["facts"] == "17 лет · мужчина · Тюмень · задержание · ст. 205"
    # The text as written: escaping it is the page's business.
    assert card["quote"] == QUOTE
    assert (card["start_offset"], card["end_offset"]) == (0, len(QUOTE))
    (candidate,) = card["rf_candidates"]
    assert candidate["full_name"] == "ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ"
    assert candidate["birth_date"] == "2007-02-17"
    assert "; ".join(candidate["reasons"]) == (
        "17 лет на 25.11.2024; мужчина; родился: Г. ТЮМЕНЬ ТЮМЕНСКОЙ ОБЛАСТИ"
    )
    assert candidate["seen"] == "в перечне с 01.12.2024 или раньше"
    assert _counts(listed)["open"] == 1 and card["resolution"] == ""
    # The dashboard counts it and leads there.
    assert 'data-primary-task="unnamed"' in cycle
    assert "Безымянные фигуранты: 1" in cycle


def test_an_rf_entry_finds_its_unnamed_candidate_without_deciding(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        search = _list(client, rf_q="пуртов")
        reverse = _list(client, rf_q="пуртов", rf_key="пуртов егор владимирович|2007-02-17")

    assert [entry["full_name"] for entry in search["rf_entries"]] == ["ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ"]
    assert search["reverse"] == []
    assert [item["key"] for item in reverse["reverse"]] == [FIGURANT]
    assert reverse["reverse"][0]["quote"].startswith("В Тюмени задержан 17-летний житель")
    assert _card(reverse)["resolution"] == ""


def test_a_person_s_word_identifies_and_closes(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        same = client.post(
            f"{API}/resolve",
            json={
                "figurant": FIGURANT,
                "resolution": "rf_entry",
                "normalized_name": "Пуртов Егор Владимирович",
                "rf_name": "пуртов егор владимирович",
                "rf_birth_date": "2007-02-17",
            },
        )
        found = _list(client, status="found")
        still_open = _list(client)
        incomplete = client.post(
            f"{API}/resolve", json={"figurant": FIGURANT, "resolution": "rf_entry"}
        )
        unknown = client.post(f"{API}/resolve", json={"figurant": "x", "resolution": "no_rf_match"})

    assert same.status_code == 200
    assert _counts(found)["found"] == 1 and _keys(found) == [FIGURANT]
    assert _card(found)["identified_as"] == "Пуртов Егор Владимирович"
    assert _card(found)["rf_candidates"][0]["decision"] == "same"
    assert still_open["items"] == [] and still_open["none_yet"] is False
    assert incomplete.status_code == 400 and unknown.status_code == 400
    with session_factory() as session:
        rows = session.execute(
            text(
                "SELECT resolution, normalized_name, rf_name, rf_birth_date "
                "FROM unnamed_identity_resolutions"
            )
        ).all()
        assert [tuple(row) for row in rows] == [
            (
                "rf_entry",
                "Пуртов Егор Владимирович",
                "пуртов егор владимирович",
                datetime(2007, 2, 17, tzinfo=UTC).date(),
            )
        ]


def test_no_rf_match_stays_open_and_insufficient_closes(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        client.post(f"{API}/resolve", json={"figurant": FIGURANT, "resolution": "no_rf_match"})
        no_rf = _list(client, status="no_rf")
        still_open = _list(client)
        cycle = client.get("/ui/cycle").text
        client.post(f"{API}/resolve", json={"figurant": FIGURANT, "resolution": "insufficient"})
        closed = client.get("/ui/cycle").text

    assert _counts(no_rf)["no_rf"] == 1 and _card(no_rf)["resolution"] == "no_rf_match"
    assert _keys(still_open) == [FIGURANT]
    assert 'data-primary-task="unnamed"' in cycle
    assert "Сейчас ничего проверять не нужно" in closed


def test_the_overview_shows_the_open_unnamed_with_their_candidates(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/overview").text
        client.post(f"{API}/resolve", json={"figurant": FIGURANT, "resolution": "insufficient"})
        closed = client.get("/ui/overview").text

    unnamed = page[page.index('id="unnamed-title"') :]
    assert 'Неопознанные фигуранты <span class="count">1</span>' in unnamed
    assert "&lt;b&gt;на железной дороге&lt;/b&gt;" in unnamed and "<b>на" not in unnamed
    assert "17 лет · мужчина · Тюмень · задержание · ст. 205 · вероятных в перечне: 1" in unnamed
    assert '<a href="/ui/unnamed">Все: 1 →</a>' in unnamed
    # Closed as insufficient: off the overview.
    assert "Неопознанных нет." in closed


def test_the_overview_counts_the_candidates_worth_looking_at() -> None:
    from analysis.unnamed.matching import Candidates
    from web.views.overview import _found

    assert _found(Candidates([], 545, None, likely=12)) == "вероятных в перечне: 12"
    # Hundreds of that age and sex, nothing else told: their number says nothing.
    assert _found(Candidates([], 545, None)) == "того возраста в перечне 545 — примет мало"
    assert _found(Candidates([], 0, None)) == "в перечне никого"
    # No age: nothing to search the list by, not «nobody».
    assert _found(Candidates([], 0, None), age_told=False) == (
        "возраст не назван — искать в перечне не по чему"
    )


def _seed_base(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.add(
            AirtableKnownPersonRecord(
                external_id="rec1",
                full_name="Тюменцев <Иван> Ильич",
                normalized_name="тюменцев иван ильич",
                matching_key="тюменцевиванильич",
                gender="male",
                birth_date=datetime(2007, 5, 1, tzinfo=UTC).date(),
                region="Тюменская область",
                city="Тюмень",
                articles="ст. 205 УК РФ ч. 1",
            )
        )


def test_a_card_shows_the_people_of_the_base_and_takes_a_word_on_them(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        without = _card(_list(client))
        _seed_base(session_factory)
        with_base = _card(_list(client))
        rejected = client.post(
            f"{API}/reject",
            json={"figurant": FIGURANT, "candidate": "base:тюменцев иван ильич|2007-05-01"},
        )
        after_reject = _card(_list(client))
        same = client.post(
            f"{API}/resolve",
            json={
                "figurant": FIGURANT,
                "resolution": "supplied_name",
                "normalized_name": "Тюменцев <Иван> Ильич",
                "rf_birth_date": "2007-05-01",
            },
        )
        found = _card(_list(client, status="found"))
        too_long = client.post(f"{API}/reject", json={"figurant": FIGURANT, "candidate": "x" * 256})

    assert without["base_candidates"] == [] and without["base_total"] == 0
    (person,) = with_base["base_candidates"]
    # The base is a person's typing, kept as typed.
    assert person["name"] == "Тюменцев <Иван> Ильич"
    assert (person["birth_date"], person["place"], person["articles"]) == (
        "2007-05-01",
        "Тюменская область, Тюмень",
        "ст. 205 УК РФ ч. 1",
    )
    assert "; ".join(person["reasons"]) == (
        "17 лет на 25.11.2024; место: Тюменская область, Тюмень; та же статья: 205"
    )
    assert person["key"] == "base:тюменцев иван ильич|2007-05-01" and person["decision"] is None
    assert rejected.status_code == 200
    assert after_reject["base_candidates"][0]["decision"] == "different"
    assert same.status_code == 200
    assert found["identified_as"] == "Тюменцев <Иван> Ильич"
    assert found["base_candidates"][0]["decision"] == "same"
    assert too_long.status_code == 400


def test_the_base_only_counts_where_the_place_does_not_fit(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    _seed_base(session_factory)
    with session_factory.begin() as session:
        session.execute(text("UPDATE airtable_known_persons SET region = 'Омск', city = NULL"))

    with _client(session_factory) as client:
        card = _card(_list(client))

    assert card["base_candidates"] == [] and card["base_total"] == 1


def test_a_card_says_where_the_article_is_tried_from_the_place(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with _client(session_factory) as client:
        without = _card(_list(client))
    with session_factory.begin() as session:
        session.add(
            AirtableKnownPersonRecord(
                external_id="sentenced",
                full_name="Осуждённый Пётр Ильич",
                normalized_name="x",
                matching_key="x",
                region="Тюменская область",
                articles="ст. 205 УК РФ ч. 1",
                court="Центральный окружной военный суд",
            )
        )

    with _client(session_factory) as client:
        card = _card(_list(client))

    assert without["courts"] == [] and without["courts_total"] == 0
    assert [(hint["court"], hint["cases"]) for hint in card["courts"]] == [
        ("Центральный окружной военный суд", 1)
    ]
    assert card["courts_total"] == 1


def test_a_card_shows_who_another_publication_names_and_takes_a_word(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with _client(session_factory) as client:
        without = _card(_list(client))
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("Прокуратура", "https://proc.example.test")
        sentence = "В Тюмени задержан Егор <Пуртов>, 17 лет."
        _, run = seed.article(
            source,
            external_id="named",
            title="Задержание <в Тюмени>",
            text=sentence,
            published_at=datetime(2024, 11, 26, tzinfo=UTC),
        )
        mention = seed.mention(run, "Егор <Пуртов>", person_id=None)
        session.get_one(EntityMentionRecord, mention).normalized_data = {
            "first_name": "Егор",
            "last_name": "Пуртов",
            "patronymic": None,
        }
        seed.event(
            run,
            sentence,
            event_type="detention",
            event_date=None,
            links=[],
            entity_links=[(mention, "target")],
        )
        session.commit()
    EntityCollector(session_factory, downstream=GroupFollowers()).run()
    with session_factory.begin() as session:
        key = session.scalar(text("SELECT key FROM entity_groups"))
        session.execute(
            text(
                "INSERT INTO entity_group_roles (group_id, role, kind, method, reason, quote) "
                "SELECT id, 'figurant', 'accused', 'model', '', '' FROM entity_groups"
            )
        )

    with _client(session_factory) as client:
        named = _card(_list(client))["named_candidates"]
        candidate_key = named[0]["candidate_key"]
        rejected = client.post(
            f"{API}/reject", json={"figurant": FIGURANT, "candidate": candidate_key}
        )
        after_reject = _card(_list(client))["named_candidates"]
        undone = client.post(
            f"{API}/reject", json={"figurant": FIGURANT, "candidate": candidate_key, "undo": True}
        )
        returned = _card(_list(client))["named_candidates"]
        same = client.post(
            f"{API}/resolve",
            json={
                "figurant": FIGURANT,
                "resolution": "existing_person",
                "existing_person_key": key,
            },
        )
        found = _card(_list(client, status="found"))

    assert without["named_candidates"] == []
    (person,) = named
    # The person leads to the dossier, the publication to the name in its text.
    assert person["key"] == key and candidate_key == f"person:{key}"
    assert person["title"] == "Задержание <в Тюмени>"
    assert (person["start"], person["end"]) == (18, 31)
    assert "; ".join(person["reasons"]) == (
        "Прокуратура, 26.11.2024: то же место, та же стадия дела; возраст 17 назван в тексте около имени"
    )
    assert person["decision"] is None
    assert (rejected.status_code, undone.status_code, same.status_code) == (200, 200, 200)
    # «Не он» said, then taken back.
    assert after_reject[0]["decision"] == "different"
    assert returned[0]["decision"] is None
    # «Это он» said: the person is the one identified.
    assert found["resolution"] == "existing_person"
    assert found["named_candidates"][0]["decision"] == "same"


def _facts(key: str, article: int, quote_: str, age: int | None) -> dict[str, object]:
    return {
        "key": key,
        "article_id": article,
        "start_offset": 0,
        "end_offset": len(quote_),
        "quote": quote_,
        "age": age,
        "gender": "male",
        "place": "Омск",
        "initial": None,
        "articles": [],
        "event_type": "sentence",
        "explanation": "осуждён за угон",
        "published_at": datetime(2024, 11, 26, tzinfo=UTC),
    }


def _aside(session_factory: sessionmaker[Session]) -> None:
    """Beside the Tyumen card: one with no age told, and one the search set aside."""
    _seed(session_factory)
    with session_factory.begin() as session:
        article = session.scalar(text("SELECT id FROM parsed_articles LIMIT 1"))
        session.add(
            UnnamedFigurantRecord(**_facts("n" * 64, article, "Подросток арестован.", None))
        )
        session.add(
            UnnamedScreenedRecord(
                **_facts("s" * 64, article, "Подросток осуждён за угон.", 16),
                reason="criminal_motive",
            )
        )


def test_a_card_without_an_age_waits_apart_from_the_queue(
    session_factory: sessionmaker[Session],
) -> None:
    _aside(session_factory)

    with _client(session_factory) as client:
        queue = _list(client)
        apart = _list(client, status="no_age")
    with session_factory() as session:
        counted = workload(session).unnamed

    # Nobody of the list is offered without an age: not work to do, still there to read.
    assert _counts(queue)["open"] == 1 and _counts(queue)["no_age"] == 1
    assert _keys(queue) == [FIGURANT]
    assert _keys(apart) == ["n" * 64] and _card(apart, "n" * 64)["has_age"] is False
    assert counted == 1


def test_whom_the_search_set_aside_is_listed_and_taken_back_by_a_press(
    session_factory: sessionmaker[Session],
) -> None:
    _aside(session_factory)

    with _client(session_factory) as client:
        listed = _list(client)
        taken = client.post(f"{API}/keep", json={"figurant": "s" * 64})
        after = _list(client)
        twice = client.post(f"{API}/keep", json={"figurant": "s" * 64})

    (aside,) = listed["screened"]
    assert aside["key"] == "s" * 64 and aside["quote"] == "Подросток осуждён за угон."
    assert "обычное уголовное дело" in aside["reason"]
    assert aside["explanation"] == "осуждён за угон"
    # Not a card until taken back.
    assert "s" * 64 not in _keys(listed)
    assert taken.status_code == 200 and taken.json() == {"figurant": "s" * 64}
    assert "s" * 64 in _keys(after) and after["screened"] == []
    assert _counts(after)["open"] == 2
    assert twice.status_code == 400


def test_the_api_reads_the_cards_and_refuses_what_it_should(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    word = {
        "figurant": FIGURANT,
        "resolution": "rf_entry",
        "normalized_name": "Пуртов Егор Владимирович",
        "rf_name": "пуртов егор владимирович",
        "rf_birth_date": "2007-02-17",
    }

    with _client(session_factory) as client:
        opened = _list(client)
        searched = _list(client, rf_q="пуртов")
        rejected = client.post(
            f"{API}/reject",
            json={"figurant": FIGURANT, "candidate": "пуртов егор владимирович|2007-02-17"},
        )
        after_reject = _list(client)
        client.post(
            f"{API}/reject",
            json={
                "figurant": FIGURANT,
                "candidate": "пуртов егор владимирович|2007-02-17",
                "undo": True,
            },
        )
        resolved = client.post(f"{API}/resolve", json=word)
        found = _list(client, status="found")
        incomplete = client.post(
            f"{API}/resolve", json={"figurant": FIGURANT, "resolution": "rf_entry"}
        )
        foreign = client.post(
            f"{API}/clear", json={"figurant": FIGURANT}, headers={"Origin": "https://evil.example"}
        )
        cleared = client.post(f"{API}/clear", json={"figurant": FIGURANT})
        back = _list(client)

    counts = _counts(opened)
    assert counts["open"] == 1 and counts["found"] == 0 and counts["all"] == 1
    card = opened["items"][0]
    assert card["facts"] == "17 лет · мужчина · Тюмень · задержание · ст. 205"
    assert card["quote"] == QUOTE and card["resolution"] == "" and card["has_age"] is True
    candidate = card["rf_candidates"][0]
    assert candidate["full_name"] == "ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ"
    assert candidate["display_name"] == "Пуртов Егор Владимирович"
    assert candidate["rf_name"] == "пуртов егор владимирович" and candidate["decision"] is None
    assert searched["rf_entries"][0]["key"] == "пуртов егор владимирович|2007-02-17"
    assert rejected.status_code == 200
    assert after_reject["items"][0]["rf_candidates"][0]["decision"] == "different"
    assert resolved.json() == {"figurant": FIGURANT}
    assert found["items"][0]["identified_as"] == "Пуртов Егор Владимирович"
    assert found["items"][0]["note"].startswith(
        "Опознан оператором: запись РФМ пуртов егор владимирович, 17.02.2007"
    )
    assert incomplete.status_code == 400 and incomplete.json()["detail"] == "Неполное решение"
    assert foreign.status_code == 403
    assert cleared.status_code == 200 and back["items"][0]["resolution"] == ""


def test_the_api_overview_shows_what_the_page_does(session_factory: sessionmaker[Session]) -> None:
    """`/api/v1/overview` for the React page: the same unnamed, counts and decisions."""
    _seed(session_factory)

    with _client(session_factory) as client:
        overview = client.get("/api/v1/overview").json()

    assert overview["unnamed"] == 1
    item = overview["latest_unnamed"][0]
    assert item["key"] == FIGURANT and item["quote"] == QUOTE
    assert item["facts"] == "17 лет · мужчина · Тюмень · задержание · ст. 205"
    assert item["found"] == "вероятных в перечне: 1"
    assert overview["new_cases"] == 0 and overview["latest_sentences"] == []
    assert {"pairs", "unclear_roles", "unclear_verdicts", "source_errors"} <= set(overview)
