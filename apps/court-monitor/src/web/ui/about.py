"""«О системе»: the build this console is running, and the data it is looking at.

The status strip says how many people and how many cases; nothing said which code
produced them. This page answers that, next to the counts that make the numbers
reproducible: the build stamp (`web.build_info`), the document and person totals, and
the last monitoring run. Read-only, and it decides nothing.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from web.about import read_about
from web.build_info import BuildInfo
from web.dependencies import get_db
from web.response_models import AboutResponse
from web.ui.layout import _page

router = APIRouter()

__all__ = ["router", "ui_about"]


def _counts(about: AboutResponse) -> list[tuple[str, str]]:
    """The totals behind the status strip, so a number on another page can be checked."""
    return [
        ("Публикаций в базе", f"{about.articles:,}".replace(",", " ")),
        ("Людей в базе", f"{about.people:,}".replace(",", " ")),
    ]


def _last_run(started: datetime | None) -> str:
    if started is None:
        return "неизвестно"
    return str(started.astimezone().strftime("%d.%m.%Y %H:%M"))


@router.get("/ui/about", response_class=HTMLResponse)
def ui_about(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    """Build stamp, the totals behind the status strip, and the last successful run."""
    about = read_about(db)
    info = BuildInfo(
        commit=about.commit, built_at=about.built_at, version=about.version, tag=about.tag
    )
    rows = [
        *info.rows(),
        *_counts(about),
        ("Последний успешный запуск", _last_run(about.last_successful_run_at)),
    ]
    table = "".join(
        f'<tr><th scope="row">{escape(name)}</th><td>{escape(value)}</td></tr>'
        for name, value in rows
    )
    now = about.checked_at.astimezone().strftime("%d.%m.%Y %H:%M")
    body = f"""<section class="band">
  <h2>Сборка</h2>
  <table><tbody>{table}</tbody></table>
  <p class="muted">Коммит и время сборки проставляются при сборке образа
  (<code>BUILD_COMMIT</code>, <code>BUILD_TIME</code>, <code>BUILD_TAG</code>). Тег вида
  «0.36.0-3-g495d9e9» значит: три коммита после тега 0.36.0. Запуск из рабочей копии без
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
