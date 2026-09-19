"""The `/export` spreadsheet: a row per article, text stays text, links stay clickable."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from telegram_bot.excel import HEADERS, people_filename, people_xlsx
from telegram_bot.models import NewsArticleReference, PeopleFromNewsResult, PersonFromNews

MOSCOW = ZoneInfo("Europe/Moscow")


def article(
    article_id: int, title: str, *, url: str = "https://news.example/1", day: int = 18
) -> NewsArticleReference:
    return NewsArticleReference(
        article_id=article_id,
        title=title,
        url=url,
        source_name="ОВД-Инфо",
        published_at=datetime(2026, 9, day, 10, tzinfo=UTC),
    )


def person(
    name: str, *, articles: list[NewsArticleReference], person_id: int = 1
) -> PersonFromNews:
    return PersonFromNews(
        person_id=person_id,
        canonical_name=name,
        article_count=len(articles),
        latest_published_at=datetime(2026, 9, 18, 10, tzinfo=UTC),
        sources=["ОВД-Инфо"],
        articles=articles,
    )


def result(people: list[PersonFromNews]) -> PeopleFromNewsResult:
    return PeopleFromNewsResult(
        date_from=datetime(2026, 9, 1, tzinfo=UTC).date(),
        date_to=datetime(2026, 9, 19, tzinfo=UTC).date(),
        total=len(people),
        limit=len(people),
        people=people,
    )


def sheet_of(payload: bytes) -> Any:
    return load_workbook(BytesIO(payload)).active


def test_the_header_row_names_every_column() -> None:
    sheet = sheet_of(people_xlsx(result([]), MOSCOW))

    assert tuple(cell.value for cell in sheet[1]) == HEADERS


def test_one_row_per_person_and_article() -> None:
    people = [
        person("Иванов Иван", articles=[article(1, "Первая"), article(2, "Вторая", day=15)]),
        person("Петров Пётр", articles=[article(3, "Третья")], person_id=2),
    ]

    sheet = sheet_of(people_xlsx(result(people), MOSCOW))

    assert sheet.max_row == 4
    assert [sheet.cell(row=row, column=2).value for row in (2, 3, 4)] == [
        "Иванов Иван",
        "Иванов Иван",
        "Петров Пётр",
    ]
    assert [sheet.cell(row=row, column=7).value for row in (2, 3, 4)] == [
        "Первая",
        "Вторая",
        "Третья",
    ]
    # The position numbers a person, not a row.
    assert [sheet.cell(row=row, column=1).value for row in (2, 3, 4)] == [1, 1, 2]


def test_a_person_without_articles_still_gets_a_row() -> None:
    sheet = sheet_of(people_xlsx(result([person("Иванов Иван", articles=[])]), MOSCOW))

    assert sheet.max_row == 2
    assert sheet.cell(row=2, column=2).value == "Иванов Иван"
    assert sheet.cell(row=2, column=8).value is None


def test_dates_are_written_in_the_bot_timezone() -> None:
    sheet = sheet_of(
        people_xlsx(result([person("Иванов Иван", articles=[article(1, "Т")])]), MOSCOW)
    )

    # 10:00 UTC on 18 September is 13:00 in Moscow, and Excel holds no timezone.
    moscow_noon = datetime(2026, 9, 18, 10, tzinfo=UTC).astimezone(MOSCOW).replace(tzinfo=None)
    assert sheet.cell(row=2, column=4).value == moscow_noon
    assert sheet.cell(row=2, column=5).value == moscow_noon
    assert moscow_noon.hour == 13


def test_a_title_starting_with_an_equals_sign_stays_text() -> None:
    dangerous = '=HYPERLINK("http://evil.example","click")'

    sheet = sheet_of(
        people_xlsx(result([person("=CMD|calc", articles=[article(1, dangerous)])]), MOSCOW)
    )

    assert sheet.cell(row=2, column=7).value == dangerous
    assert sheet.cell(row=2, column=7).data_type == "s"
    assert sheet.cell(row=2, column=2).data_type == "s"


def test_a_web_link_is_clickable_and_another_scheme_is_not() -> None:
    people = [
        person("Иванов Иван", articles=[article(1, "Т", url="https://news.example/7")]),
        person("Петров Пётр", articles=[article(2, "Т", url="javascript:alert(1)")], person_id=2),
    ]

    sheet = sheet_of(people_xlsx(result(people), MOSCOW))

    assert sheet.cell(row=2, column=8).hyperlink is not None
    assert sheet.cell(row=3, column=8).hyperlink is None
    assert sheet.cell(row=3, column=8).value == "javascript:alert(1)"


def test_unicode_survives_the_round_trip() -> None:
    sheet = sheet_of(
        people_xlsx(
            result([person("Ёлкина Анна", articles=[article(1, "Приговор — 5 лет")])]), MOSCOW
        )
    )

    assert sheet.cell(row=2, column=2).value == "Ёлкина Анна"
    assert sheet.cell(row=2, column=7).value == "Приговор — 5 лет"


def test_the_filename_carries_the_period() -> None:
    assert people_filename(result([])) == "people-2026-09-01-2026-09-19.xlsx"
