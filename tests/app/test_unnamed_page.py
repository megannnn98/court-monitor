"""«Безымянные»: the unnamed figurants, their candidates from the list, a person's word."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from html import escape
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import (
    AirtableKnownPersonRecord,
    EntityMentionRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    UnnamedFigurantRecord,
    UnnamedScreenedRecord,
)
from entities.collector import EntityCollector
from operator_console import OperationRegistry
from web.app import app
from web.dependencies import get_db, get_operation_registry
from web.ui.workload import workload

QUOTE = "В Тюмени задержан 17-летний житель города по делу о теракте <b>на железной дороге</b>."


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
                key="k" * 64,
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
        page = client.get("/ui/unnamed").text

    assert "<title>Безымянные</title>" in page
    assert "Безымянных фигурантов пока нет: их находит шаг 5" in page
    assert '<span>Безымянные</span><span class="nav-count">' not in page
    assert 'href="/ui/unnamed" aria-current="page"' not in page


def test_a_candidate_removed_from_the_list_is_shown_as_removed(
    session_factory: sessionmaker[Session],
) -> None:
    from datetime import date

    from rosfinmonitoring.operator_table import OperatorRow, store

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
        page = client.get("/ui/unnamed").text

    row = page[page.index("УШЕДШИЙ ИВАН ПЕТРОВИЧ") :]
    row = row[: row.index("</tr>")]
    assert "родился: Г. ТЮМЕНЬ; исключён из перечня" in row
    # The day is the table's and the list no longer holds him: neither is said as the list's.
    assert "включён 20.11.2024, позже исключён — по таблице оператора" in row
    assert "в перечне с" not in row
    assert ">Это он<" in row


def test_a_card_shows_the_text_what_it_tells_and_the_candidates(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/unnamed").text
        cycle = client.get("/ui/cycle").text

    assert "17 лет · мужчина · Тюмень · задержание · ст. 205" in page
    # Scraped text is escaped; the sentence leads to its publication.
    assert "&lt;b&gt;на железной дороге&lt;/b&gt;" in page and "<b>на железной" not in page
    assert '<a href="/ui/articles/' in page and "?start=0&amp;end=" in page
    assert "ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ" in page and "17.02.2007" in page
    assert "17 лет на 25.11.2024; мужчина; родился: Г. ТЮМЕНЬ ТЮМЕНСКОЙ ОБЛАСТИ" in page
    assert "в перечне с 01.12.2024 или раньше" in page
    assert "Не разобраны (1)" in page and '<span class="badge pending">не разобран</span>' in page
    # The dashboard counts it and leads here.
    assert 'data-primary-task="unnamed"' in cycle
    assert "Безымянные фигуранты: 1" in cycle


def test_an_rf_entry_finds_its_unnamed_candidate_without_deciding(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        search = client.get("/ui/unnamed", params={"rf_q": "пуртов"}).text
        reverse = client.get(
            "/ui/unnamed",
            params={
                "rf_q": "пуртов",
                "rf_key": "пуртов егор владимирович|2007-02-17",
            },
        ).text

    assert "ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ" in search
    assert "Подходящие безымянные публикации (1)" in reverse
    assert "В Тюмени задержан 17-летний житель" in reverse
    assert "опознан:" not in reverse


def test_a_person_s_word_identifies_and_closes(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        same = client.post(
            "/ui/unnamed/resolve",
            data={
                "figurant": "k" * 64,
                "resolution": "rf_entry",
                "normalized_name": "Пуртов Егор Владимирович",
                "rf_name": "пуртов егор владимирович",
                "rf_birth_date": "2007-02-17",
                "back": "status=open&page=1",
            },
            follow_redirects=False,
        )
        found = client.get("/ui/unnamed", params={"status": "found"}).text
        still_open = client.get("/ui/unnamed").text
        incomplete = client.post(
            "/ui/unnamed/resolve", data={"figurant": "k" * 64, "resolution": "rf_entry"}
        )
        unknown = client.post(
            "/ui/unnamed/resolve", data={"figurant": "x", "resolution": "no_rf_match"}
        )

    assert same.status_code == 303
    assert same.headers["location"] == f"/ui/unnamed?status=open&page=1&person_q=#u-{'k' * 64}"
    assert "Опознаны (1)" in found and "опознан: Пуртов Егор Владимирович" in found
    assert '<span class="badge succeeded">это он</span>' in found
    assert "В этом разделе никого." in still_open
    assert incomplete.status_code == 400 and unknown.status_code == 400
    with session_factory() as session:
        assert session.execute(
            text(
                "SELECT resolution, normalized_name, rf_name, rf_birth_date "
                "FROM unnamed_identity_resolutions"
            )
        ).all() == [
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
        client.post(
            "/ui/unnamed/resolve",
            data={"figurant": "k" * 64, "resolution": "no_rf_match"},
        )
        no_rf = client.get("/ui/unnamed", params={"status": "no_rf"}).text
        still_open = client.get("/ui/unnamed").text
        cycle = client.get("/ui/cycle").text
        client.post(
            "/ui/unnamed/resolve",
            data={"figurant": "k" * 64, "resolution": "insufficient"},
        )
        closed = client.get("/ui/cycle").text

    assert "Нет записи РФМ (1)" in no_rf
    assert '<span class="badge">подходящей записи РФМ нет</span>' in no_rf
    assert "Не разобраны (1)" in still_open
    assert 'data-primary-task="unnamed"' in cycle
    assert "Сейчас ничего проверять не нужно" in closed


def test_the_overview_shows_the_open_unnamed_with_their_candidates(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/overview").text
        client.post(
            "/ui/unnamed/resolve",
            data={"figurant": "k" * 64, "resolution": "insufficient", "back": "status=open"},
            follow_redirects=False,
        )
        closed = client.get("/ui/overview").text

    unnamed = page[page.index('id="unnamed-title"') :]
    assert 'Неопознанные фигуранты <span class="count">1</span>' in unnamed
    assert "&lt;b&gt;на железной дороге&lt;/b&gt;" in unnamed and "<b>на" not in unnamed
    assert "17 лет · мужчина · Тюмень · задержание · ст. 205 · вероятных в перечне: 1" in unnamed
    assert '<a href="/ui/unnamed">Все: 1 →</a>' in unnamed
    # Closed as insufficient: off the overview.
    assert "Неопознанных нет." in closed


def test_the_overview_counts_the_candidates_worth_looking_at() -> None:
    from entities.unnamed import Candidates
    from web.ui.overview import _found

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
        without = client.get("/ui/unnamed").text
        _seed_base(session_factory)
        page = client.get("/ui/unnamed").text
        rejected = client.post(
            "/ui/unnamed/reject",
            data={
                "figurant": "k" * 64,
                "candidate": "base:тюменцев иван ильич|2007-05-01",
                "back": "status=open&page=1",
            },
            follow_redirects=False,
        )
        after_reject = client.get("/ui/unnamed").text
        same = client.post(
            "/ui/unnamed/resolve",
            data={
                "figurant": "k" * 64,
                "resolution": "supplied_name",
                "normalized_name": "Тюменцев <Иван> Ильич",
                "rf_birth_date": "2007-05-01",
                "back": "status=open&page=1",
            },
            follow_redirects=False,
        )
        found = client.get("/ui/unnamed", params={"status": "found"}).text
        too_long = client.post(
            "/ui/unnamed/reject", data={"figurant": "k" * 64, "candidate": "x" * 256}
        )

    assert "Кандидаты из базы Airtable</caption>" not in without
    assert "В базе Airtable" not in without.split("</article>")[0].split("<article")[1]
    assert "Кандидаты из базы Airtable</caption>" in page
    # The base is a person's typing: escaped like everything else.
    assert "Тюменцев &lt;Иван&gt; Ильич</th>" in page and "<Иван>" not in page
    assert (
        "<td>01.05.2007</td><td>Тюменская область, Тюмень</td><td>ст. 205 УК РФ ч. 1</td>" in page
    )
    assert "17 лет на 25.11.2024; место: Тюменская область, Тюмень; та же статья: 205" in page
    assert (
        'name="resolution" value="supplied_name"><input type="hidden" name="normalized_name" '
        'value="Тюменцев &lt;Иван&gt; Ильич"><input type="hidden" name="rf_birth_date" '
        'value="2007-05-01">'
    ) in page
    assert 'name="candidate" value="base:тюменцев иван ильич|2007-05-01"' in page
    assert rejected.status_code == 303
    assert 'Ильич <span class="badge">не он</span></th>' in after_reject
    assert same.status_code == 303
    assert "опознан: Тюменцев &lt;Иван&gt; Ильич" in found
    assert 'Ильич <span class="badge succeeded">это он</span></th>' in found
    # A confirmed person is taken back with «Отменить решение»: no «Не он» beside them.
    assert 'name="candidate" value="base:' not in found
    assert too_long.status_code == 400


def test_the_base_only_counts_where_the_place_does_not_fit(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    _seed_base(session_factory)
    with session_factory.begin() as session:
        session.execute(text("UPDATE airtable_known_persons SET region = 'Омск', city = NULL"))

    with _client(session_factory) as client:
        page = client.get("/ui/unnamed").text

    assert "Кандидаты из базы Airtable</caption>" not in page
    assert "В базе Airtable 1 человек этого возраста и пола; по месту из них никто" in page


def test_a_card_says_where_the_article_is_tried_from_the_place(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with _client(session_factory) as client:
        without = client.get("/ui/unnamed").text
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
        page = client.get("/ui/unnamed").text

    assert "Где судят" not in without
    assert (
        "Где судят по этой статье из этого места (приговоры в базе Airtable, всего 1): "
        "Центральный окружной военный суд — 1.</p>"
    ) in page


def test_a_card_shows_who_another_publication_names_and_takes_a_word(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with _client(session_factory) as client:
        without = client.get("/ui/unnamed").text
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
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        key = session.scalar(text("SELECT key FROM entity_groups"))
        session.execute(
            text(
                "INSERT INTO entity_group_roles (group_id, role, kind, method, reason, quote) "
                "SELECT id, 'figurant', 'accused', 'model', '', '' FROM entity_groups"
            )
        )

    with _client(session_factory) as client:
        page = client.get("/ui/unnamed").text
        rejected = client.post(
            "/ui/unnamed/reject",
            data={"figurant": "k" * 64, "candidate": f"person:{key}", "back": "status=open&page=1"},
            follow_redirects=False,
        )
        after_reject = client.get("/ui/unnamed").text
        undone = client.post(
            "/ui/unnamed/reject",
            data={"figurant": "k" * 64, "candidate": f"person:{key}", "undo": "1"},
            follow_redirects=False,
        )
        returned = client.get("/ui/unnamed").text
        same = client.post(
            "/ui/unnamed/resolve",
            data={
                "figurant": "k" * 64,
                "resolution": "existing_person",
                "existing_person_key": key,
                "back": "status=open&page=1",
            },
            follow_redirects=False,
        )
        found = client.get("/ui/unnamed", params={"status": "found"}).text

    caption = "Названные в других публикациях</caption>"
    assert caption not in without and caption in page
    table = page[page.index(caption) :]
    table = table[: table.index("</table>")]
    # The person leads to the dossier, the publication to the name in its text; escaped.
    assert f'<a href="/ui/investigations/{quote(key)}">' in table
    assert "Задержание &lt;в Тюмени&gt;</a>" in table and "<в Тюмени>" not in table
    assert re.search(r'href="/ui/articles/\d+\?start=18&amp;end=31"', table)
    assert (
        "Прокуратура, 26.11.2024: то же место, та же стадия дела; возраст 17 назван в тексте около имени"
        in table
    )
    # The key holds what the text wrote: escaped in the form as everywhere.
    assert f'name="existing_person_key" value="{escape(key, quote=True)}"' in table
    assert f'name="candidate" value="person:{escape(key, quote=True)}"' in table
    assert "<пуртов>" not in table and "<Пуртов>" not in table
    assert rejected.status_code == 303 and undone.status_code == 303 and same.status_code == 303
    assert '<span class="badge">не он</span>' in after_reject
    # «Не он» said: «Это он» stays, and «Вернуть» takes the word back.
    rejected_table = after_reject[after_reject.index(caption) :].split("</table>")[0]
    assert 'name="resolution" value="existing_person"' in rejected_table
    assert (
        'name="undo" value="1"><button type="submit" class="secondary">Вернуть<' in rejected_table
    )
    assert ">Не он<" not in rejected_table
    table_again = returned[returned.index(caption) :].split("</table>")[0]
    assert "не он</span>" not in table_again and ">Не он<" in table_again
    # «Это он» said: no button beside the person — «Отменить решение» is below the card.
    confirmed_table = found[found.index(caption) :].split("</table>")[0]
    assert 'name="resolution" value="existing_person"' not in confirmed_table
    assert 'name="candidate" value="person:' not in confirmed_table
    assert 'name="undo"' not in confirmed_table
    assert '<span class="badge succeeded">это он</span>' in found[found.index(caption) :]


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
        queue = client.get("/ui/unnamed").text
        apart = client.get("/ui/unnamed", params={"status": "no_age"}).text
    with session_factory() as session:
        counted = workload(session).unnamed

    # Nobody of the list is offered without an age: not work to do, still there to read.
    assert "Не разобраны (1)" in queue and "Без возраста (1)" in queue
    assert f'id="u-{"n" * 64}"' not in queue and f'id="u-{"k" * 64}"' in queue
    assert f'id="u-{"n" * 64}"' in apart and f'id="u-{"k" * 64}"' not in apart
    assert counted == 1


def test_whom_the_search_set_aside_is_listed_and_taken_back_by_a_press(
    session_factory: sessionmaker[Session],
) -> None:
    _aside(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/unnamed").text
        folded = page[page.index('<details class="band" id="screened">') :]
        taken = client.post(
            "/ui/unnamed/keep", data={"figurant": "s" * 64, "back": "status=open&page=1"}
        )
        after = client.get("/ui/unnamed").text
        twice = client.post("/ui/unnamed/keep", data={"figurant": "s" * 64}, follow_redirects=False)

    assert "<summary>Отсеяно автоматически (1)</summary>" in folded
    assert "Подросток осуждён за угон." in folded and "обычное уголовное дело" in folded
    assert "осуждён за угон</span>" in folded and "Вернуть на разбор</button>" in folded
    # Not a card until taken back.
    assert f'id="u-{"s" * 64}"' not in page
    assert taken.history[0].status_code == 303
    assert taken.history[0].headers["location"].endswith(f"#u-{'s' * 64}")
    assert f'id="u-{"s" * 64}"' in after and "Отсеяно автоматически" not in after
    assert "Не разобраны (2)" in after
    assert twice.status_code == 400
