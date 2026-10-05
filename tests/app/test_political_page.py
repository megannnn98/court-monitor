"""«Результат»: the politically persecuted off the Rosfinmonitoring list, and its Excel."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import (
    AirtableKnownPersonRecord,
    EntityDoneMarkRecord,
    EntityGroupNewsRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    UnnamedFigurantRecord,
    UnnamedIdentityResolutionRecord,
)
from web.app import app
from web.dependencies import get_db
from web.ui.political import political_xlsx
from web.ui.political_rows import named_row


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


def _seed(session_factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=now,
            source_url="https://example.test",
            content_hash="x",
            entry_count=1,
            fetched_at=now,
        )
        session.add(snapshot)
        session.flush()
        entry = RosfinmonitoringEntryRecord(
            snapshot_id=snapshot.id,
            full_name="ИВАНОВ ИВАН ИВАНОВИЧ",
            normalized_name="иванов иван иванович",
            matching_key="ивановиваниванович",
        )
        session.add(entry)
        # Смирнова on the list with her patronymic: who she is, confirmed.
        confirmed = RosfinmonitoringEntryRecord(
            snapshot_id=snapshot.id,
            full_name="СМИРНОВА АННА ПЕТРОВНА",
            normalized_name="смирнова анна петровна",
            matching_key="смирновааннапетровна",
            birth_date=datetime(1990, 2, 1, tzinfo=UTC),
            birth_place="Г. МОСКВА",
        )
        session.add(confirmed)
        for key, name, verdict, days, regions in (
            ("анна смирнова", "Анна Смирнова", "political", 10, [["Москва", 1]]),
            ("иван иванов", "Иван Иванов", "political", 400, []),
            ("александр беда", "Александр Беда", "criminal", 5, []),
        ):
            entity = EntityGroupRecord(
                key=key,
                name=name,
                variants=[[name, 1]],
                mention_count=1,
                article_count=1,
                event_types={},
                regions=regions,
                last_published_at=now - timedelta(days=days),
            )
            session.add(entity)
            session.flush()
            session.add(
                EntityGroupPoliticsRecord(
                    group_id=entity.id,
                    verdict=verdict,
                    method="model",
                    reason=f"так про {name}",
                    quote="цитата",
                )
            )
            if key == "иван иванов":
                session.flush()
                session.add(
                    EntityGroupRfMatchRecord(group_id=entity.id, entry_id=entry.id, level="name")
                )
            if key == "анна смирнова":
                session.flush()
                session.add(
                    EntityGroupRfMatchRecord(
                        group_id=entity.id, entry_id=confirmed.id, level="full"
                    )
                )


def test_the_list_is_the_political_the_period_tells_new_from_old(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/political").text
        fresh = client.get("/ui/political", params={"months": 3}).text
        # The box that hid the maybe-namesakes is gone: an old address hides nobody.
        old_box = client.get(
            "/ui/political", params={"months": 0, "hide_maybe_listed": "true"}
        ).text

    assert "<title>Результат</title>" in page
    assert '<span>Результаты</span><span class="nav-count">2</span>' in page
    # The funnel, in one line, down to the result.
    assert "2 политические дела — результат" in page
    assert "Воронка за всё время (публикаций пока нет):" in page
    # The whole funnel closes the journal: the link goes to it, not to the page's top.
    assert '<a href="/ui/runs#funnel">Подробнее</a>' in page
    assert "Найдено: 2." in page and "Беда" not in page
    assert "Смирнова Анна" in page and "Москва" in page and "модель: так про Анна Смирнова" in page
    assert 'Иванов Иван</a> <span class="badge pending">возможно в перечне</span>' in page
    assert "Скрыть возможных" not in page and "hide_maybe_listed" not in page
    # Иванов's latest news is a year old: not a new case.
    assert "Найдено: 1." in fresh and "Иванов" not in fresh
    assert "Найдено: 2." in old_box and "Иванов Иван" in old_box
    # On the list with the patronymic: in the result, the list's word beside the name.
    assert 'Смирнова Анна</a> <span class="badge">в перечне РФМ</span>' in page
    assert "СМИРНОВА АННА ПЕТРОВНА, 01.02.1990 г.р., Г. МОСКВА" in page


def test_the_excel_has_every_row_of_the_filters(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/political/export.xlsx", params={"months": 0})

    assert response.status_code == 200
    # All the time: from the earliest latest news (Иванов's, 400 days ago) to today.
    today = datetime.now(UTC).date()
    assert response.headers["content-disposition"] == (
        f'attachment; filename="result_{(today - timedelta(days=400)).isoformat()}_'
        f'{today.isoformat()}.xlsx"'
    )
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet is not None
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][:3] == ("№", "Фамилия Имя", "Регион")
    assert rows[0][8] == "Перечень РФМ"
    assert rows[0][9:14] == (
        "Источник 1",
        "Источник 2",
        "Источник 3",
        "Свежая новость",
        "В базе Airtable",
    )
    assert [(row[1], row[2], row[8]) for row in rows[1:]] == [
        (
            "Смирнова Анна",
            "Москва",
            "в перечне: СМИРНОВА АННА ПЕТРОВНА, 01.02.1990 г.р., Г. МОСКВА",
        ),
        ("Иванов Иван", None, "возможно тёзка: ИВАНОВ ИВАН ИВАНОВИЧ"),
    ]


def test_the_excel_makes_each_source_a_separate_link() -> None:
    entity = EntityGroupRecord(
        id=1,
        key="анна смирнова",
        name="Анна Смирнова",
        variants=[],
        mention_count=1,
        article_count=1,
        event_types={},
        regions=[],
    )
    politics = EntityGroupPoliticsRecord(
        group_id=1,
        verdict="political",
        method="model",
        reason="основание",
        quote="цитата",
    )
    workbook = load_workbook(
        BytesIO(
            political_xlsx(
                [
                    replace(
                        named_row(entity, politics),
                        links=[
                            ("Первый источник", "https://first.example.test", None, "Первый"),
                            ("Второй источник", "https://second.example.test", None, "Второй"),
                        ],
                    )
                ]
            )
        )
    )
    sheet = workbook.active
    assert sheet is not None
    assert sheet.cell(row=2, column=10).value == "Первый: Первый источник"
    assert sheet.cell(row=2, column=10).hyperlink.target == "https://first.example.test"
    assert sheet.cell(row=2, column=11).value == "Второй: Второй источник"
    assert sheet.cell(row=2, column=11).hyperlink.target == "https://second.example.test"
    assert sheet.cell(row=2, column=12).value is None


def test_a_period_of_dates_and_the_tick_box(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    today = datetime.now(UTC).date()
    recent = {"date_from": (today - timedelta(days=30)).isoformat(), "date_to": today.isoformat()}
    old = {
        "date_from": (today - timedelta(days=500)).isoformat(),
        "date_to": (today - timedelta(days=300)).isoformat(),
    }

    with _client(session_factory) as client:
        page = client.get("/ui/political").text
        in_recent = client.get("/ui/political", params=recent).text
        in_old = client.get("/ui/political", params=old).text
        # Dates given, the months are not the choice: a year-old case, though «3 months».
        dates_win = client.get("/ui/political", params={**old, "months": 3}).text
        excel = client.get("/ui/political/export.xlsx", params=old)

    assert '<button type="submit" name="months" value="3" class="chip"' in page
    # Day/month/year whatever the browser's language: no native date field.
    assert '<input type="text" name="date_from" value="" placeholder="дд/мм/гггг"' in page
    # A calendar at hand; its own field has no name, so it sends nothing.
    assert page.count('class="secondary date-open" title="Выбрать в календаре"') == 2
    assert (
        '<input type="date" class="date-native" tabindex="-1" aria-hidden="true" value="">' in page
    )
    assert "showPicker()" in page
    # The export sends the form as it is: dates picked without «Показать» count.
    assert '<button type="submit" class="secondary" formaction="/ui/political/export.xlsx">' in page
    assert page.index('<input type="hidden" name="months" value="0">') < page.index(
        'name="months" value="3"'
    )
    # Смирнова's latest news is 10 days old, Иванов's 400.
    assert "Найдено: 1." in in_recent and "Смирнова Анна" in in_recent
    assert "Найдено: 1." in in_old and "Иванов Иван" in in_old
    shown = date.fromisoformat(old["date_from"]).strftime("%d/%m/%Y")
    assert f'name="date_from" value="{shown}"' in in_old
    assert (
        f'class="date-native" tabindex="-1" aria-hidden="true" value="{old["date_from"]}"' in in_old
    )
    assert "Иванов Иван" in dates_win
    assert (
        f'filename="result_{old["date_from"]}_{old["date_to"]}.xlsx"'
        in (excel.headers["content-disposition"])
    )
    rows = list(load_workbook(BytesIO(excel.content)).active.iter_rows(values_only=True))  # type: ignore[union-attr]
    assert [row[1] for row in rows[1:]] == ["Иванов Иван"]


def test_the_period_is_kept_for_a_reload_and_the_menu(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    today = datetime.now(UTC).date()
    old = {
        "date_from": (today - timedelta(days=500)).isoformat(),
        "date_to": (today - timedelta(days=300)).isoformat(),
    }

    with _client(session_factory) as client:
        client.get("/ui/political", params=old)
        # The menu's link, or a reload of it: no filters in the address.
        again = client.get("/ui/political").text
        # «За всё время» chosen is chosen, not the remembered dates.
        client.get("/ui/political", params={"months": 0, "date_from": "", "date_to": ""})
        everything = client.get("/ui/political").text
        # Dates the page's script kept, picked but not shown yet: encoded.
        # In the browser it replaces the server's (same name and path); here, one of two.
        client.cookies.clear()
        client.cookies.set(
            "political_filters",
            f"months%3D0%26date_from%3D{old['date_from']}%26date_to%3D{old['date_to']}"
            "%26hide_maybe_listed%3Dtrue",
        )
        # Kept by the old box ticked: hides nobody now.
        picked = client.get("/ui/political").text

    shown = date.fromisoformat(old["date_from"]).strftime("%d/%m/%Y")
    assert "Найдено: 1." in again and f'name="date_from" value="{shown}"' in again
    assert "Найдено: 2." in everything
    assert "Найдено: 1." in picked and "Иванов Иван" in picked
    assert 'id="political-filters"' in picked and "document.cookie" in picked


def test_the_form_s_day_month_year_is_read(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    today = datetime.now(UTC).date()
    written = {
        "date_from": (today - timedelta(days=30)).strftime("%d/%m/%Y"),
        "date_to": today.strftime("%d/%m/%Y"),
    }

    with _client(session_factory) as client:
        page = client.get("/ui/political", params=written).text

    # Смирнова's latest news is 10 days old, Иванов's 400.
    assert "Найдено: 1." in page and "Смирнова Анна" in page
    assert f'value="{written["date_from"]}"' in page


def _news(session_factory: sessionmaker[Session]) -> None:
    """Смирнова's latest news: a new case. Иванов's: never read."""
    with session_factory.begin() as session:
        smirnova = session.query(EntityGroupRecord).filter_by(key="анна смирнова").one()
        session.add(
            EntityGroupNewsRecord(
                group_id=smirnova.id,
                kind="new_case",
                method="model",
                reason="возбуждено дело",
                quote="цитата",
                published_at=smirnova.last_published_at,
            )
        )


def test_the_latest_news_is_marked_and_chosen(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    _news(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/political", params={"months": 0}).text
        new_cases = client.get("/ui/political", params={"months": 0, "news": "new_case"}).text
        # Kept for the reload, as the period is.
        again = client.get("/ui/political").text
        sentences = client.get("/ui/political", params={"months": 0, "news": "sentence"}).text
        nonsense = client.get("/ui/political", params={"months": 0, "news": "drop table"}).text
        excel = client.get("/ui/political/export.xlsx", params={"months": 0, "news": "new_case"})

    assert "<th>Свежая новость</th>" in page
    assert '<span class="badge succeeded" title="возбуждено дело">новое дело</span>' in page
    assert '<option value="new_case">Новые дела (1)</option>' in page
    assert '<option value="sentence">Приговоры (0)</option>' in page
    assert "Найдено: 1." in new_cases and "Иванов Иван" not in new_cases
    assert '<option value="new_case" selected>' in new_cases
    assert "Найдено: 1." in again and '<option value="new_case" selected>' in again
    assert "Найдено: 0." in sentences
    assert "Найдено: 2." in nonsense
    rows = list(load_workbook(BytesIO(excel.content)).active.iter_rows(values_only=True))  # type: ignore[union-attr]
    news = rows[0].index("Свежая новость")
    assert [(row[1], row[news]) for row in rows[1:]] == [("Смирнова Анна", "новое дело")]


def _known(session_factory: sessionmaker[Session], *names: str, active: bool = True) -> None:
    with session_factory.begin() as session:
        for number, name in enumerate(names):
            session.add(
                AirtableKnownPersonRecord(
                    external_id=f"{name}-{number}",
                    full_name=name,
                    normalized_name=name.lower(),
                    matching_key=name.lower().replace(" ", ""),
                    active=active,
                )
            )


def _third(session_factory: sessionmaker[Session]) -> None:
    """A political person nobody has: the base's answer for them is «not there»."""
    with session_factory.begin() as session:
        entity = EntityGroupRecord(
            key="олег новиков",
            name="Олег Новиков",
            variants=[["Олег Новиков", 1]],
            mention_count=1,
            article_count=1,
            event_types={},
            regions=[],
            last_published_at=datetime.now(UTC) - timedelta(days=2),
        )
        session.add(entity)
        session.flush()
        session.add(
            EntityGroupPoliticsRecord(
                group_id=entity.id, verdict="political", method="model", reason="", quote=""
            )
        )


def _base_world(session_factory: sessionmaker[Session]) -> None:
    """Смирнова: one record, with a patronymic the news lacks. Иванов: two namesakes.
    Новиков: not in the base."""
    _seed(session_factory)
    _third(session_factory)
    _known(
        session_factory,
        "Смирнова Анна Петровна",
        "Иванов Иван Иванович",
        "Иванов Иван Петрович",
        "Кто-то Другой",
    )


def test_the_list_says_who_the_operator_s_base_already_holds(
    session_factory: sessionmaker[Session],
) -> None:
    _base_world(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/political", params={"months": 0}).text

    assert "<th>В базе Airtable</th>" in page
    # Смирнова: one record that holds her name and more — probably, not certainly.
    assert re.search(r"Смирнова Анна</a>.*?>вероятно, есть в базе</span>", page, re.DOTALL)
    assert "Смирнова Анна Петровна" in page
    # Иванов: two records fit; the page counts them and picks neither.
    assert re.search(r"Иванов Иван</a>.*?>тёзки в базе: 2</span>", page, re.DOTALL)
    # Новиков: nobody by this name — the new one.
    assert re.search(r"Новиков Олег</a>.*?>нет в базе</span>", page, re.DOTALL)
    assert 'name="known"' in page
    for option in (
        '<option value="none">Нет в базе (1)</option>',
        '<option value="probably">Вероятно, есть в базе (1)</option>',
        '<option value="namesakes">Тёзки в базе (1)</option>',
        '<option value="in_base">Есть в базе (0)</option>',
    ):
        assert option in page


def test_the_choice_of_an_answer_narrows_the_list_and_its_count(
    session_factory: sessionmaker[Session],
) -> None:
    _base_world(session_factory)

    with _client(session_factory) as client:
        new = client.get("/ui/political", params={"months": 0, "known": "none"}).text
        namesakes = client.get("/ui/political", params={"months": 0, "known": "namesakes"}).text
        nonsense = client.get("/ui/political", params={"months": 0, "known": "drop table"}).text

    assert "Найдено: 1." in new and "Новиков Олег" in new and "Смирнова" not in new
    assert "Найдено: 1." in namesakes and "Иванов Иван" in namesakes
    # Narrowed, the counts stay those of the whole list: the choice is not a filter on them.
    assert '<option value="none" selected>Нет в базе (1)</option>' in new
    assert "Найдено: 3." in nonsense


def test_with_no_base_loaded_nobody_is_called_new(session_factory: sessionmaker[Session]) -> None:
    """Nothing synced yet: «not in the base» would be a statement about nothing."""
    _seed(session_factory)

    with _client(session_factory) as client:
        # An answer that, taken at its word, would leave nobody: with no base it is ignored.
        page = client.get("/ui/political", params={"months": 0, "known": "probably"}).text
        excel = client.get("/ui/political/export.xlsx", params={"months": 0, "known": "probably"})

    assert "нет в базе" not in page and 'name="known"' not in page
    assert "Найдено: 2." in page
    rows = list(load_workbook(BytesIO(excel.content)).active.iter_rows(values_only=True))  # type: ignore[union-attr]
    assert len(rows) == 3 and all(row[-1] is None for row in rows[1:])


def test_the_excel_has_the_base_s_answer_and_follows_the_choice(
    session_factory: sessionmaker[Session],
) -> None:
    _base_world(session_factory)

    with _client(session_factory) as client:
        everyone = client.get("/ui/political/export.xlsx", params={"months": 0})
        new = client.get("/ui/political/export.xlsx", params={"months": 0, "known": "none"})

    def answers(response: object) -> dict[str, str | None]:
        sheet = load_workbook(BytesIO(response.content)).active  # type: ignore[attr-defined]
        rows = list(sheet.iter_rows(values_only=True))
        column = rows[0].index("В базе Airtable")
        return {row[1]: row[column] for row in rows[1:]}

    assert answers(everyone) == {
        "Смирнова Анна": "вероятно, есть в базе: Смирнова Анна Петровна",
        "Иванов Иван": "тёзки в базе: 2 (Иванов Иван Иванович; Иванов Иван Петрович)",
        "Новиков Олег": "нет в базе",
    }
    assert answers(new) == {"Новиков Олег": "нет в базе"}


def test_an_inactive_record_of_the_base_does_not_count(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    _known(session_factory, "Смирнова Анна Петровна", active=False)
    _known(session_factory, "Кто-то Другой")

    with _client(session_factory) as client:
        page = client.get("/ui/political", params={"months": 0}).text

    assert 'name="known"' in page and "нет в базе" in page
    assert "вероятно, есть в базе" not in page


def _tick(client: TestClient, key: str, done: int = 1, back: str = "") -> None:
    response = client.post(
        "/ui/political/done",
        content=f"key={key}&done={done}&back={back}",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"].startswith("/ui/political?")


def test_a_person_ticked_as_done_leaves_the_list_and_can_be_shown_again(
    session_factory: sessionmaker[Session],
) -> None:
    """Ирина ticks the people she has dealt with, so the list holds only what is left."""
    _seed(session_factory)
    with _client(session_factory) as client:
        before = client.get("/ui/political").text
        assert "Смирнова Анна" in before and "Иванов Иван" in before
        assert "Найдено: 2." in before
        assert "Показать обработанных (0)</label>" in before, "the way back is always in sight"
        # The tick-box label, not the select's: that one draws the box as a wide field.
        assert '<label class="check"><input type="checkbox" name="done"' in before

        _tick(client, "анна смирнова")
        hidden = client.get("/ui/political?months=0").text
        assert "Смирнова Анна" not in hidden and "Иванов Иван" in hidden
        assert "Найдено: 1." in hidden
        assert "Показать обработанных (1)</label>" in hidden
        assert 'name="done" value="show" onchange="this.form.submit()">' in hidden

        shown = client.get("/ui/political?months=0&done=show").text
        assert "Смирнова Анна" in shown and "Найдено: 2." in shown
        assert '<tr class="done">' in shown
        assert 'name="done" value="show" onchange="this.form.submit()" checked>' in shown

        # The file follows the page: hidden there, hidden here.
        book = load_workbook(BytesIO(client.get("/ui/political/export.xlsx?months=0").content))
        names = [row[1] for row in book["Результат"].iter_rows(min_row=2, values_only=True)]
        assert names == ["Иванов Иван"]

        _tick(client, "анна смирнова", done=0)
        assert "Смирнова Анна" in client.get("/ui/political?months=0").text


def test_a_later_news_brings_a_done_person_back(session_factory: sessionmaker[Session]) -> None:
    """What was dealt with is what was known then. A news after the tick is new work."""
    _seed(session_factory)
    with _client(session_factory) as client:
        _tick(client, "анна смирнова")
        assert "Смирнова Анна" not in client.get("/ui/political?months=0").text
        with session_factory.begin() as session:
            entity = session.query(EntityGroupRecord).filter_by(key="анна смирнова").one()
            entity.last_published_at = datetime.now(UTC)
        assert "Смирнова Анна" in client.get("/ui/political?months=0").text


def test_the_mark_survives_a_rebuild_of_the_people(session_factory: sessionmaker[Session]) -> None:
    """Step 3 rebuilds the groups and their ids change: the mark is kept by the key."""
    _seed(session_factory)
    with _client(session_factory) as client:
        _tick(client, "анна смирнова")
        with session_factory.begin() as session:
            marks = session.query(EntityDoneMarkRecord).all()
            assert [(mark.key, mark.news_at is not None) for mark in marks] == [
                ("анна смирнова", True)
            ]


def test_ticking_nobody_is_refused_and_the_way_back_stays_on_the_list(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with _client(session_factory) as client:
        missing = client.post(
            "/ui/political/done",
            content="key=никто&done=1",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert missing.status_code == 404
        response = client.post(
            "/ui/political/done",
            content="key=иван иванов&done=1&back=https%3A%2F%2Fevil.test%2F%3Fpage%3D2",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert response.headers["location"].startswith("/ui/political?")


def test_a_name_has_a_copy_button_and_the_page_loads_the_script(
    session_factory: sessionmaker[Session],
) -> None:
    """Ирина copies the name into her own base: one click beside it."""
    _seed(session_factory)
    with _client(session_factory) as client:
        text = client.get("/ui/political?months=0").text
        assert 'data-copy="Смирнова Анна"' in text
        assert re.search(r'<script defer src="/static/local-ui\.js\?v=[0-9a-f]{12}">', text)
        script = client.get("/static/local-ui.js").text
    # The three things the script is for, each by the line that does it.
    assert "sessionStorage.setItem(KEY, String(window.scrollY))" in script
    assert 'link.target = "_blank"' in script and "link.host !== location.host" in script
    assert "navigator.clipboard.writeText(button.dataset.copy)" in script


def _unnamed(
    session_factory: sessionmaker[Session],
    key: str,
    *,
    age: int | None,
    place: str,
    articles: list[str],
    gender: str = "male",
    initial: str | None = None,
    event: str = "detention",
    day: int = 20,
) -> None:
    quote = f"Задержан человек {key}."
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source(f"news-{key}", f"https://{key}.example.test")
        article, _ = seed.article(
            source,
            external_id=key,
            title=f"Новость {key}",
            text=quote,
            published_at=datetime(2026, 9, day, tzinfo=UTC),
        )
        session.add(
            UnnamedFigurantRecord(
                key=key,
                article_id=article,
                start_offset=0,
                end_offset=len(quote),
                quote=quote,
                age=age,
                gender=gender,
                place=place,
                initial=initial,
                articles=articles,
                event_type=event,
                explanation=f"дело об участии в запрещённой организации ({key})",
                published_at=datetime(2026, 9, day, tzinfo=UTC),
            )
        )
        session.commit()


def test_the_unnamed_with_a_political_article_are_rows_of_the_list_and_of_the_file(
    session_factory: sessionmaker[Session],
) -> None:
    """Ирина: «в работе они есть, а в результатах нет». One person a row, named by what
    the text tells; a case with no political article stays on «Безымянные»."""
    _seed(session_factory)
    _unnamed(session_factory, "a", age=15, place="Канаш", articles=["205.5"], day=20)
    _unnamed(session_factory, "b", age=15, place="Канаш", articles=[], day=21)
    _unnamed(session_factory, "c", age=None, place="Благовещенск", articles=["282.2"])
    _unnamed(session_factory, "d", age=16, place="Вологда", articles=["228.1"])
    _unnamed(session_factory, "e", age=30, place="Тотьма", articles=[])
    with _client(session_factory) as client:
        page = client.get("/ui/political?months=0").text
        assert "Найдено: 4." in page
        assert page.count("15-летний житель Канаша</a>") == 1, "two sentences, one row"
        assert "житель Благовещенска</a>" in page
        assert "Вологды" not in page and "Тотьмы" not in page
        assert page.count(">без имени</span>") == 2
        assert 'data-copy="15-летний житель Канаша"' in page
        assert "/ui/unnamed?status=all#u-b" in page, "the link goes to the latest sentence"

        only = client.get("/ui/political?months=0&who=unnamed").text
        assert "Найдено: 2." in only and "Смирнова Анна" not in only
        named = client.get("/ui/political?months=0&who=named").text
        assert "Найдено: 2." in named and "без имени</span>" not in named

        book = load_workbook(
            BytesIO(client.get("/ui/political/export.xlsx?months=0&who=unnamed").content)
        )
        rows = list(book["Результат"].iter_rows(min_row=2, values_only=True))
        assert sorted(row[1] for row in rows) == ["15-летний житель Канаша", "житель Благовещенска"]
        kanash = next(row for row in rows if row[1] == "15-летний житель Канаша")
        assert kanash[2] == "Канаш" and kanash[3] == "205.5"
        assert (kanash[6].date(), kanash[7].date()) == (date(2026, 9, 20), date(2026, 9, 21))
        assert kanash[9].endswith("Новость b") and kanash[10].endswith("Новость a")


def test_an_identified_figurant_is_no_longer_an_unnamed_row(
    session_factory: sessionmaker[Session],
) -> None:
    """Once the operator says who it is, the person is in the result under the name."""
    _seed(session_factory)
    _unnamed(session_factory, "a", age=15, place="Канаш", articles=["205.5"])
    _unnamed(session_factory, "c", age=None, place="Благовещенск", articles=["282.2"])
    with session_factory.begin() as session:
        session.add(
            UnnamedIdentityResolutionRecord(
                figurant_key="a", resolution="supplied_name", normalized_name="Иван Петров"
            )
        )
        session.add(UnnamedIdentityResolutionRecord(figurant_key="c", resolution="no_rf_match"))
    with _client(session_factory) as client:
        page = client.get("/ui/political?months=0&who=unnamed").text
    assert "Канаша" not in page
    assert "житель Благовещенска</a>" in page, "«нет записи РФМ» does not close the case"


def test_an_unnamed_row_is_ticked_done_and_comes_back_with_a_later_sentence(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    _unnamed(session_factory, "a", age=15, place="Канаш", articles=["205.5"], day=20)
    key = "unnamed:канаш|male|15|"
    with _client(session_factory) as client:
        assert f'name="key" value="{key}"' in client.get("/ui/political?months=0").text
        _tick(client, key)
        hidden = client.get("/ui/political?months=0&who=unnamed").text
        assert "Канаша" not in hidden and "Показать обработанных (1)" in hidden
        shown = client.get("/ui/political?months=0&who=unnamed&done=show").text
        assert '<tr class="done">' in shown and "15-летний житель Канаша" in shown

        _unnamed(session_factory, "b", age=15, place="Канаш", articles=[], day=25)
        assert "15-летний житель Канаша" in client.get("/ui/political?months=0&who=unnamed").text

        missing = client.post(
            "/ui/political/done",
            content="key=unnamed:нигде|male|1|&done=1",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert missing.status_code == 404


def test_the_filters_of_the_list_hold_for_the_unnamed_too(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    _unnamed(session_factory, "a", age=15, place="Канаш", articles=["205.5"], event="sentence")
    _unnamed(session_factory, "c", age=None, place="Благовещенск", articles=["282.2"], day=2)
    with _client(session_factory) as client:
        sentences = client.get("/ui/political?months=0&who=unnamed&news=sentence").text
        assert "Канаша" in sentences and "Благовещенска" not in sentences
        late = client.get("/ui/political?who=unnamed&date_from=10/09/2026").text
        assert "Канаша" in late and "Благовещенска" not in late
        # With no record of the base that tells the same, an unnamed person is new too.
        _known(session_factory, "Петров Пётр Петрович")
        new = client.get("/ui/political?months=0&known=none").text
        assert "Канаша" in new and "Благовещенска" in new


def _nameless(
    session_factory: sessionmaker[Session], name: str, *, opened: date, **values: object
) -> None:
    """A record of the base that names nobody: no birth date, the age in its name."""
    with session_factory.begin() as session:
        session.add(
            AirtableKnownPersonRecord(
                external_id=f"rec-{name}-{opened}",
                full_name=name,
                normalized_name=name.lower(),
                matching_key=name.lower(),
                case_opened_on=opened,
                **{"gender": "male", "region": "Чувашия", "city": "Канаш", **values},  # type: ignore[arg-type]
            )
        )


def test_an_unnamed_person_is_looked_for_among_the_base_s_nameless_records(
    session_factory: sessionmaker[Session],
) -> None:
    """«41-летний житель Ленинградской области» stands in the base under that very name;
    the result said «нет в базе», having no name to look up."""
    _seed(session_factory)
    _unnamed(session_factory, "a", age=41, place="Ленинградская область", articles=["205.5"])
    _unnamed(session_factory, "b", age=15, place="Канаш", articles=["205.5"])
    _unnamed(session_factory, "c", age=30, place="Вологда", articles=["282.2"])
    _unnamed(session_factory, "d", age=None, place="Тотьма", articles=["282.2"])
    # The base is loaded: it holds named people too.
    _known(session_factory, "Петров Пётр Петрович")
    _nameless(
        session_factory,
        "41-летний житель <Ленинградской> области",
        opened=date(2026, 9, 19),
        region="Ленинградская область",
        city=None,
    )
    # Two records tell of a boy of Канаш: which one, if any, is for the operator.
    _nameless(session_factory, "15-летний житель Канаша", opened=date(2026, 9, 1))
    _nameless(session_factory, "Подросток 15-летний, Канаш (2)", opened=date(2026, 8, 1))
    # A record the base no longer counts says nothing.
    _nameless(
        session_factory,
        "30-летний житель Вологды",
        opened=date(2026, 9, 1),
        region="Вологодская область",
        city="Вологда",
        active=False,
    )
    # Not them: a woman, another town, a named person of that age and place.
    _nameless(
        session_factory, "15-летняя жительница Канаша", opened=date(2026, 9, 1), gender="female"
    )
    _nameless(
        session_factory,
        "30-летний житель Омска",
        opened=date(2026, 9, 1),
        region="Омская область",
        city="Омск",
    )
    with session_factory.begin() as session:
        session.add(
            AirtableKnownPersonRecord(
                external_id="named",
                full_name="Иванов 15-летний Иван",
                normalized_name="x",
                matching_key="x",
                gender="male",
                region="Чувашия",
                city="Канаш",
                birth_date=date(2011, 1, 1),
            )
        )

    with _client(session_factory) as client:
        page = client.get("/ui/political?months=0&who=unnamed").text
        similar = client.get("/ui/political?months=0&known=similar").text
        workbook = load_workbook(
            BytesIO(client.get("/ui/political/export.xlsx?months=0&who=unnamed").content)
        )

    def answer(place: str) -> str:
        row = re.search(rf"{place}.*?</tr>", page, re.DOTALL)
        assert row is not None
        return row.group(0)

    # The page says what the answer is worth.
    assert "сигнал проверить запись, а не основание не заводить\nновое дело" in page
    # One record fits: probably her record, named as the base writes it — and escaped.
    leningrad = answer("Ленинградской области")
    assert ">вероятно, есть в базе</span>" in leningrad
    assert "41-летний житель &lt;Ленинградской&gt; области" in leningrad
    kanash = answer("Канаша")
    assert ">похожие записи в базе: 2</span>" in kanash
    assert "15-летний житель Канаша; Подросток 15-летний, Канаш (2)" in kanash
    assert "жительница" not in kanash and "Иванов" not in kanash
    # Nobody of Вологда in the base; and no age told — nothing to compare.
    assert ">нет в базе</span>" in answer("Вологды") and ">нет в базе</span>" in answer("Тотьмы")
    for option in (
        '<option value="probably">Вероятно, есть в базе (1)</option>',
        '<option value="similar">Похожие записи в базе (1)</option>',
        '<option value="none">Нет в базе (2)</option>',
    ):
        assert option in page
    assert "Канаша" in similar and "Ленинградской" not in similar
    rows = list(workbook["Результат"].iter_rows(values_only=True))
    known = rows[0].index("В базе Airtable")
    cells = {row[1]: row[known] for row in rows[1:]}
    assert cells["41-летний житель Ленинградской области"] == (
        "вероятно, есть в базе: 41-летний житель <Ленинградской> области"
    )
    assert cells["15-летний житель Канаша"] == (
        "похожие записи в базе: 2 (15-летний житель Канаша; Подросток 15-летний, Канаш (2))"
    )


def test_a_base_of_nameless_records_alone_is_a_base(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    _unnamed(session_factory, "b", age=15, place="Канаш", articles=["205.5"])
    with _client(session_factory) as client:
        empty = client.get("/ui/political?months=0&who=unnamed").text
        _nameless(session_factory, "15-летний житель Канаша", opened=date(2026, 9, 1))
        page = client.get("/ui/political?months=0&who=unnamed").text
        chosen = client.get("/ui/political?months=0&who=unnamed&known=none").text

    # No base at all: nobody is called new. A base of records without a name is a base.
    assert 'name="known"' not in empty and "вероятно, есть в базе" not in empty
    assert ">вероятно, есть в базе</span>" in page and 'name="known"' in page
    assert "Канаша" not in chosen


def test_the_base_is_asked_with_the_age_of_the_day_it_was_told(
    session_factory: sessionmaker[Session],
) -> None:
    """The age is told on the 5th; a later sentence of the same person, on the 20th, tells
    none. A record «19-летний» of a year ago fits a man of 21 only if the two weeks to
    the later sentence are counted — and they must not be."""
    _seed(session_factory)
    _unnamed(session_factory, "a", age=21, place="Канаш", articles=["205.5"], day=5)
    _unnamed(session_factory, "b", age=None, place="Канаш", articles=["205.5"], day=20)
    _nameless(session_factory, "19-летний житель Канаша", opened=date(2025, 9, 10))

    with _client(session_factory) as client:
        page = client.get("/ui/political?months=0&who=unnamed").text

    row = re.search(r"Канаша.*?</tr>", page, re.DOTALL)
    assert row is not None and page.count("без имени</span>") == 1, "one person, two sentences"
    assert ">нет в базе</span>" in row.group(0)
