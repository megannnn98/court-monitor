"""«О системе»: the build this console is running, and the data it is looking at.

The status strip says how many people and how many cases; nothing said which code
produced them. This page answers that, next to the counts that make the numbers
reproducible: the build stamp (`web.build_info`), the document and person totals, and
the last monitoring run. Read-only, and it decides nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from db.orm_models import EntityGroupRecord, ParsedArticleRecord
from web.build_info import build_info
from web.dependencies import get_db
from web.ui.layout import _page

router = APIRouter()

__all__ = ["router", "ui_about"]


def _counts(db: Session) -> list[tuple[str, str]]:
    """The totals behind the status strip, so a number on another page can be checked."""
    articles = db.scalar(_count(ParsedArticleRecord.__tablename__)) or 0
    people = db.scalar(_count(EntityGroupRecord.__tablename__)) or 0
    return [
        ("Публикаций в базе", f"{articles:,}".replace(",", " ")),
        ("Людей в базе", f"{people:,}".replace(",", " ")),
    ]


def _count(table: str) -> TextClause:
    """A COUNT over one table by name. The names are literals in this module, never
    anything a request can reach."""
    return text(f"SELECT count(*) FROM {table}")


def _last_run(db: Session) -> str:
    row = db.execute(
        text(
            """
            SELECT started_at FROM operator_operation_runs
            WHERE status = 'succeeded' ORDER BY id DESC LIMIT 1
            """
        )
    ).first()
    if row is None or row[0] is None:
        return "неизвестно"
    started = row[0]
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started.astimezone().strftime("%d.%m.%Y %H:%M")


@router.get("/ui/about", response_class=HTMLResponse)
def ui_about(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    """Build stamp, the totals behind the status strip, and the last successful run."""
    info = build_info()
    rows = [*info.rows(), *_counts(db), ("Последний успешный запуск", _last_run(db))]
    table = "".join(
        f'<tr><th scope="row">{escape(name)}</th><td>{escape(value)}</td></tr>'
        for name, value in rows
    )
    now = datetime.now(UTC).astimezone().strftime("%d.%m.%Y %H:%M")
    body = f"""<section class="band">
  <h2>Сборка</h2>
  <table><tbody>{table}</tbody></table>
  <p class="muted">Коммит и время сборки проставляются при сборке образа
  (<code>BUILD_COMMIT</code>, <code>BUILD_TIME</code>). Запуск из рабочей копии без
  пересборки показывает «{info.commit if info.commit else "неизвестно"}» — это значит, что
  страница говорит не о том коде, который вы правите.</p>
</section>
<section class="band">
  <h2>Что считается</h2>
  <p class="muted">Публикации и люди — те же счётчики, что в полосе показателей наверху
  каждой страницы: <code>parsed_articles</code> и <code>entity_groups</code>. Проверяются
  на {escape(now)}.</p>
  <p class="muted">Эта страница ничего не меняет и ни на что не влияет.</p>
</section>"""
    return _page(
        "О системе",
        body,
        active="about",
        instruction="Какой код и данные стоят за числами в консоли.",
        db=db,
    )
