"""«Выгрузить всех людей»: the whole base of the people as one Excel file.

The result and the people page export what their filters show. This is the other thing: the
operator keeps her own table and wants everybody the system has found, with no filter between
her and the rows — the people with a name on one sheet, and the cases that name nobody
(«15-летний житель Канаша») on another, so that nothing she can see on the site is missing
from the file. She filters in Excel.

Both sheets say as little as the data does. A person nobody has judged has no verdict and no
role, and the cell is empty rather than «не ясно»: an empty cell is «не определено», and
«не ясно» is an answer the model gave.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, Response
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import (
    EntityGroupChargeRecord,
    EntityGroupNewsRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
)
from entities.known_base import KnownBase
from entities.news import KIND_LABELS as NEWS_LABELS
from entities.rf_check import FULL
from entities.unnamed import EVENT_LABELS
from entities.unnamed_cases import Case, all_cases
from rosfinmonitoring.inclusion_dates import ATTRIBUTION as INCLUSION_ATTRIBUTION
from web.dependencies import get_db
from web.ui.entities import _article_order, _role_label, display_name
from web.ui.political import (
    _RF_ENTRIES,
    _case_done,
    _done_keys,
    _regions_text,
    _unnamed_done,
    entry_included_text,
    known_answer_text,
)

router = APIRouter()

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_VERDICTS = {"political": "политическое", "criminal": "уголовное", "unclear": "не ясно"}
_PEOPLE_HEADERS = (
    "№",
    "Фамилия Имя",
    "Роль в деле",
    "Дело",
    "Статьи УК",
    "Регион",
    "Упоминаний",
    "Публикаций",
    "Последняя новость",
    "Свежая новость",
    "Перечень РФМ",
    "В базе Airtable",
    "Обработано",
)
_UNNAMED_HEADERS = (
    "№",
    "Кто",
    "Политическая статья",
    "Статьи УК",
    "Место",
    "Предложений",
    "Первая новость",
    "Последняя новость",
    "Событие",
    "Что сказано",
    "Источник",
    "Обработано",
)


def notes_text(stamp: str) -> list[str]:
    return [
        f"Выгрузка всей базы людей на {stamp}: без фильтров.",
        (
            "«Люди» — все, кого система нашла по имени. «Без имени» — случаи, где публикация "
            "не называет человека; опознанные в этот лист не входят: они в «Людях»."
        ),
        (
            "Пустая «Роль в деле» или «Дело» — шаг ещё не определял; «не ясно» — модель не "
            "смогла решить."
        ),
        (
            "Дата в «Перечне РФМ» — свойство записи перечня, а не человека: она не делает "
            "совпадение подтверждением личности. " + INCLUSION_ATTRIBUTION
        ),
        (
            "«В базе Airtable» — сверка только по имени (даты рождения и региона там нет): "
            "«вероятно» и «тёзки» — не уверенность."
        ),
    ]


def _day(moment: datetime | None) -> datetime | None:
    """A moment as a naive one: Excel has no time zones, and the cell is a date."""
    return moment.replace(tzinfo=None) if moment else None


def _write(sheet: Worksheet, headers: tuple[str, ...], rows: list[list[Any]]) -> None:
    sheet.append(list(headers))
    for row in rows:
        sheet.append(row)
    for line in sheet.iter_rows(min_row=2):
        for cell in line:
            if isinstance(cell.value, str):
                # Names, places and titles come from scraped pages: never a formula.
                cell.data_type = "s"
            elif isinstance(cell.value, datetime):
                cell.number_format = "DD.MM.YYYY"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def _rf_text(entries: list[Any]) -> str | None:
    """The list's word on the person: the strongest entry, the one with the patronymic first."""
    if not entries:
        return None
    level, full_name, birth_date, birth_place, inclusion_date = entries[0]
    entry = ", ".join(
        part
        for part in (full_name, f"{birth_date:%d.%m.%Y} г.р." if birth_date else "", birth_place)
        if part
    )
    if level != FULL:
        return f"возможно тёзка: {entry}"
    included = entry_included_text(inclusion_date)
    return f"в перечне: {entry}" + (f", {included}" if included else "")


def people_rows(db: Session) -> list[list[Any]]:
    """Every person with a name, the most mentioned first."""
    groups = list(
        db.scalars(
            select(EntityGroupRecord).order_by(
                EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key
            )
        )
    )
    ids = [group.id for group in groups]
    roles = {
        group_id: (role, kind)
        for group_id, role, kind in db.execute(
            select(
                EntityGroupRoleRecord.group_id,
                EntityGroupRoleRecord.role,
                EntityGroupRoleRecord.kind,
            )
        )
    }
    verdicts = {
        group_id: verdict
        for group_id, verdict in db.execute(
            select(EntityGroupPoliticsRecord.group_id, EntityGroupPoliticsRecord.verdict)
        )
    }
    news = {
        group_id: kind
        for group_id, kind in db.execute(
            select(EntityGroupNewsRecord.group_id, EntityGroupNewsRecord.kind)
        )
    }
    articles: dict[int, set[str]] = {}
    for group_id, article in db.execute(
        select(EntityGroupChargeRecord.group_id, EntityGroupChargeRecord.article).distinct()
    ):
        articles.setdefault(group_id, set()).add(article)
    entries: dict[int, list[Any]] = {}
    for group_id, *entry in db.execute(_RF_ENTRIES, {"groups": ids}):
        entries.setdefault(group_id, []).append(tuple(entry))
    base = KnownBase.from_session(db)
    done = _done_keys(db, [group.key for group in groups])
    rows: list[list[Any]] = []
    for position, group in enumerate(groups, start=1):
        role = roles.get(group.id)
        rows.append(
            [
                position,
                display_name(group.name),
                _role_label(*role) if role else None,
                _VERDICTS.get(verdicts.get(group.id, "")),
                ", ".join(sorted(articles.get(group.id, ()), key=_article_order)) or None,
                _regions_text(group) or None,
                group.mention_count,
                group.article_count,
                _day(group.last_published_at),
                NEWS_LABELS.get(news[group.id], news[group.id]) if group.id in news else None,
                _rf_text(entries.get(group.id, [])),
                known_answer_text(base.match(group.name), loaded=bool(len(base))),
                "да" if group.key in done else None,
            ]
        )
    return rows


def _case_source(case: Case) -> str:
    latest = case.latest
    return f"{latest.source}: {latest.title[:120]}" if latest.title else latest.source


def unnamed_rows(db: Session) -> tuple[list[list[Any]], list[str]]:
    """Every unnamed person not yet identified, the latest news first; and the addresses
    of their latest sources, to make each a link."""
    marks = _unnamed_done(db)
    oldest = datetime.min.replace(tzinfo=UTC)
    cases = sorted(all_cases(db), key=lambda case: case.last_published_at or oldest, reverse=True)
    rows = [
        [
            position,
            case.name,
            "да" if case.political else "нет",
            ", ".join(case.articles) or None,
            case.place or None,
            len(case.sentences),
            _day(case.first_published_at),
            _day(case.last_published_at),
            EVENT_LABELS.get(case.latest.event_type, case.latest.event_type),
            case.latest.explanation,
            _case_source(case),
            "да" if _case_done(case, marks) else None,
        ]
        for position, case in enumerate(cases, start=1)
    ]
    return rows, [case.latest.url for case in cases]


def people_xlsx(db: Session, *, today: datetime | None = None) -> bytes:
    workbook = Workbook()
    people = workbook.active
    assert people is not None
    people.title = "Люди"
    _write(people, _PEOPLE_HEADERS, people_rows(db))
    for letter, width in zip("ABCDEFGHIJKLM", (5, 28, 26, 14, 18, 22, 11, 11, 16, 20, 50, 36, 12)):
        people.column_dimensions[letter].width = width
    unnamed = workbook.create_sheet("Без имени")
    rows, links = unnamed_rows(db)
    _write(unnamed, _UNNAMED_HEADERS, rows)
    for line, url in enumerate(links, start=2):
        if url.startswith(("http://", "https://")):
            unnamed.cell(row=line, column=11).hyperlink = url
    for letter, width in zip("ABCDEFGHIJKL", (5, 34, 12, 18, 22, 11, 16, 16, 18, 60, 50, 12)):
        unnamed.column_dimensions[letter].width = width
    notes = workbook.create_sheet("Пояснения")
    stamp = (today or datetime.now(UTC)).astimezone().strftime("%d.%m.%Y %H:%M")
    for line, text in enumerate(notes_text(stamp), start=1):
        notes.cell(row=line, column=1, value=text).data_type = "s"
    notes.column_dimensions["A"].width = 140
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# Not under `/ui/entities/`: there `/ui/entities/{key}` is a person's page, and «export.xlsx»
# would be taken for a key.
@router.get("/ui/people/export.xlsx")
def ui_people_export(db: Session = Depends(get_db)) -> Response:  # noqa: B008
    """The whole base of the people, whatever the page's filters say."""
    name = f"people_{datetime.now(UTC).date().isoformat()}.xlsx"
    return Response(
        people_xlsx(db),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
