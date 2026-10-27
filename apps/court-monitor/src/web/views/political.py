"""«Результат»: the politically persecuted — what the whole pipeline is for.

A figurant of a criminal case (step 4) whose case is political persecution (step 5), on
the Rosfinmonitoring list or not: the list does not make a case known, it confirms who a
person is (the operator's word) — its birth date and place are shown beside the name. A
name the list carries without a patronymic may be a namesake: marked, and can be hidden.
The period filter answers «new or old case» by the date of the latest publication; the
same rows go to Excel.
"""

from __future__ import annotations

from datetime import date, timedelta

from openpyxl import Workbook
from sqlalchemy.orm import Session

from analysis.news.case_news import KIND_LABELS
from analysis.operator_base.known_base import COUNTED, KnownMatch
from analysis.rosfinmonitoring.rf_entry import (
    INCLUSION_NOTES,
    rf_word,
)
from results.done_marks import mark_done
from web.exports import (
    excel_day,
    is_web_link,
    workbook_bytes,
    write_notes,
    write_sheet,
)
from web.views.political_filters import (
    Filters,
)
from web.views.political_rows import LINKS, ListRow, latest_news

PAGE_SIZE = 100


def _article_text(articles: list[tuple[str, bool]]) -> str:
    return ", ".join(article for article, _ in articles)


# Who said so. A person who decides a verdict by hand is not the model, and telling the
# operator their own decision came from the model costs them the reason to trust it.
_BASIS_LABELS = {
    "article": "статья УК",
    "manual": "решено вручную",
    "memorial": "правило: реестр «Мемориала»",
    "base": "база оператора",
    "model": "модель",
}

# What `decide_politics` writes as the reason. The label already says this, so printing
# it after a colon reads as «решено вручную: решено оператором вручную».
_MANUAL_REASON = "решено оператором вручную"


def basis_text(row: ListRow) -> str:
    """Who decided the verdict, and why — the two never say the same thing twice."""
    label = _BASIS_LABELS.get(row.basis.method, "модель")
    reason = row.basis.reason
    if reason in ("", _MANUAL_REASON):
        return label
    return f"{label}: {reason}"


def known_answer_text(match: KnownMatch | None, *, loaded: bool) -> str | None:
    """The answer of the operator's base as a cell: what, and which records."""
    if not loaded:
        return None
    if match is None:
        return "нет в базе"
    names = "; ".join(match.names)
    return f"{match.label} ({names})" if match.level in COUNTED else f"{match.label}: {names}"


def _known_text(row: ListRow, *, loaded: bool) -> str | None:
    return known_answer_text(row.known, loaded=loaded)


def political_xlsx(rows: list[ListRow], *, known_loaded: bool = False) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Результат"
    headers = [
        "№",
        "Фамилия Имя",
        "Регион",
        "Статьи УК",
        "Почему политическое",
        "Мемориал",
        "Первая новость",
        "Последняя новость",
        "Перечень РФМ",
        *(f"Источник {number}" for number in range(1, LINKS + 1)),
        "Свежая новость",
        "В базе Airtable",
        # Last: the files she already works from keep their columns where they were.
        "Статья перечня",
    ]
    first_source = headers.index("Источник 1") + 1
    sources = [
        [(title, url, source) for title, url, _, source in row.links if is_web_link(url)][:LINKS]
        for row in rows
    ]
    write_sheet(
        sheet,
        headers,
        [
            [
                position,
                row.shown_name,
                row.regions or None,
                _article_text(row.articles) or None,
                basis_text(row),
                row.memorial,
                excel_day(row.first_published),
                excel_day(row.last_published),
                rf_word(row.rf_level, row.rf_entry, row.rf_inclusion_date, row.rf_inclusion_source)
                or None,
                *(f"{source}: {title[:80]}" for title, _, source in found),
                *(None for _ in range(LINKS - len(found))),
                KIND_LABELS.get(row.news_kind, row.news_kind) if row.news_kind else None,
                _known_text(row, loaded=known_loaded),
                row.listing.text if row.listing else None,
            ]
            for position, (row, found) in enumerate(zip(rows, sources, strict=True), start=1)
        ],
    )
    for line, found in enumerate(sources, start=2):
        for column, (_, url, _) in enumerate(found, start=first_source):
            sheet.cell(row=line, column=column).hyperlink = url
    write_notes(workbook, "Источник дат", INCLUSION_NOTES)
    return workbook_bytes(workbook)


def export_name(chosen: Filters, rows: list[ListRow], today: date) -> str:
    """`result_<from>_<to>.xlsx`: the dates chosen, else the period's start, else the
    earliest latest news of the rows; up to the date chosen, else today."""
    start = chosen.date_from
    if start is None and chosen.months:
        start = today - timedelta(days=30 * chosen.months)
    if start is None:
        dates = [row.last_published for row in rows if row.last_published is not None]
        start = min(dates).date() if dates else today
    end = chosen.date_to or today
    return f"result_{start.isoformat()}_{end.isoformat()}.xlsx"


def set_done(db: Session, key: str, *, done: bool) -> None:
    """Tick or untick «обработано» and commit; read by the legacy form and
    `POST /api/v1/political/done`. LookupError for nobody by this key.

    A tick remembers the latest news the operator has seen: a later one brings the person
    back to the list."""
    seen = latest_news(db, key)
    mark_done(db, key, done=done, news_at=seen)
    db.commit()
