"""The exports as files: Excel, built from rows, not requests.

What every Excel file of the site shares stands here: how a sheet is written (text that
can never become a formula, dates as dates), which links are links, and the bytes of a
workbook. The route of a page chooses the rows and wraps the bytes in a response.
"""

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from web.candidate_rows import CANDIDATE_CATEGORIES, _CandidateRow, news_day, surname_first

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DAY_FORMAT = "DD.MM.YYYY"


def is_web_link(url: str) -> bool:
    """Only web links are clickable: a scraped «javascript:» URL stays plain text."""
    return url.startswith(("http://", "https://"))


def excel_day(moment: datetime | None) -> datetime | None:
    """A moment as a naive one: Excel has no time zones, and the cell is a date."""
    return moment.replace(tzinfo=None) if moment else None


def write_sheet(
    sheet: Worksheet,
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    widths: Sequence[int] = (),
    filterable: bool = False,
) -> None:
    """The headers and the rows; `widths` are the columns' in the order of the headers,
    `filterable` freezes the headers and puts Excel's filter on them."""
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    for line in sheet.iter_rows(min_row=2):
        for cell in line:
            if isinstance(cell.value, str):
                # Names, places and titles come from scraped pages: never a formula.
                cell.data_type = "s"
            elif isinstance(cell.value, date):
                cell.number_format = DAY_FORMAT
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    if filterable:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions


def write_notes(workbook: Workbook, title: str, lines: Iterable[str]) -> None:
    """A sheet of plain words, a line a row: what travels with the rows when the file
    leaves the site."""
    notes = workbook.create_sheet(title)
    for line, words in enumerate(lines, start=1):
        notes.cell(row=line, column=1, value=words).data_type = "s"


def workbook_bytes(workbook: Workbook) -> bytes:
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def candidates_xlsx(rows: Sequence[_CandidateRow]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Кандидаты"
    sheet.append(["№", "Фамилия Имя", "Дата новости", "Категория", "Причины", "Ссылка"])
    for position, (candidate, news) in enumerate(rows, start=1):
        link = news.url if news is not None else None
        published = news_day(news.published_at) if news is not None and news.published_at else None
        category = (
            CANDIDATE_CATEGORIES.get(news.event_type, news.event_type)
            if news is not None and news.event_type
            else None
        )
        reasons = "; ".join(candidate.persecution_reasons) or None
        sheet.append(
            [position, surname_first(candidate.canonical_name), published, category, reasons, link]
        )
        row = position + 1
        # Names and URLs come from scraped sources: never let a leading "=" become a formula.
        for column, value in ((2, True), (5, reasons), (6, link)):
            if value is not None:
                sheet.cell(row=row, column=column).data_type = "s"
        if published is not None:
            sheet.cell(row=row, column=3).number_format = DAY_FORMAT
        if link is not None and is_web_link(link):
            sheet.cell(row=row, column=6).hyperlink = link
    return workbook_bytes(workbook)
