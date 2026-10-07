"""«Спросить»: a question in plain words, the answer, and the counts it stands on.

`entities.ask` does the work. The page shows the answer with every count the model
called for under it, so a number is checked by eye; a number of the answer that no count
holds is named above it. The questions asked before are listed below.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import ChatQuestionRecord
from entities.ask import (
    FAILED,
    LIST,
    MARK_CLOSE,
    MARK_OPEN,
    MAX_QUESTION,
    REFUSED,
    SEARCH,
    ask,
    asker_from_env,
    daily_budget,
    spent_today,
    unverified_numbers,
)
from web.dependencies import get_db, session_factory_for
from web.ui.layout import _page

router = APIRouter()

HISTORY = 20
EXAMPLES = (
    "В каких регионах самые суровые наказания за антивоенные высказывания?",
    "Сколько приговоров за госизмену было в 2026 году и какие сроки давали?",
    "Какие самые большие сроки давали заочно?",
    "Что известно о задержаниях на одиночных пикетах?",
)

# «[№ 123]»; a model may drop the sign, so a bare number of an id's length is one too.
_REFERENCE = re.compile(r"\[(?:№\s*(\d+)|(\d{3,}))\]")


def _answer_html(answer: str) -> str:
    """The answer by paragraphs; «[№ 123]» is a link to the publication."""

    def link(found: re.Match[str]) -> str:
        article = found.group(1) or found.group(2)
        return f'<a href="/ui/articles/{article}">[№ {article}]</a>'

    linked = _REFERENCE.sub(link, escape(answer))
    return "".join(f"<p>{part}</p>" for part in linked.split("\n") if part.strip())


def _number(value: object) -> str:
    return f"{value:g}".replace(".", ",") if isinstance(value, float) else str(value)


_STATS_COLUMNS = (
    ("name", "Группа"),
    ("cases", "Дел"),
    ("imprisoned", "Лишение свободы"),
    ("mean_years", "Средний срок, лет"),
    ("median_years", "Медиана, лет"),
    ("max_years", "Наибольший, лет"),
    ("suspended", "Условно"),
    ("fined", "Штраф"),
    ("in_absentia", "Заочно"),
)


def _stats_html(result: dict[str, Any]) -> str:
    def line(group: dict[str, Any], tag: str = "td") -> str:
        cells = "".join(
            f"<{tag}>{escape(_number(group.get(key, '')))}</{tag}>" for key, _ in _STATS_COLUMNS
        )
        return f"<tr>{cells}</tr>"

    head = "".join(f'<th scope="col">{label}</th>' for _, label in _STATS_COLUMNS)
    rows = "".join(line(group) for group in result.get("groups", []))
    small = "".join(line(group) for group in result.get("small_groups", []))
    if small:
        rows += (
            f'<tr><th colspan="{len(_STATS_COLUMNS)}" scope="colgroup">'
            f"Слишком мало сроков для сравнения</th></tr>{small}"
        )
    notes = [
        f"без этой группы (не названа в публикации): {result['group_unknown']}"
        if result.get("group_unknown")
        else "",
        f"год приговора неизвестен, не вошли: {result['year_unknown']}"
        if result.get("year_unknown")
        else "",
        f"групп отброшено как слишком маленькие: {result['small_groups_left_out']}"
        if result.get("small_groups_left_out")
        else "",
    ]
    note = "; ".join(part for part in notes if part)
    return (
        f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody>"
        f"<tfoot>{line(result.get('total', {}), 'th')}</tfoot></table>"
        + (f'<p class="muted">Дел {note}.</p>' if note else "")
    )


def _list_html(result: dict[str, Any]) -> str:
    def row(case: dict[str, Any]) -> str:
        more = f" и ещё {case['publications'] - 1}" if case.get("publications", 1) > 1 else ""
        fine = f", штраф {case['fine_rub']:,} ₽".replace(",", " ") if case.get("fine_rub") else ""
        term = f"{_number(case['years'])} лет" if case.get("years") else ""
        absentia = " (заочно)" if case.get("in_absentia") else ""
        return (
            f"<tr><td>{escape(case['person'])}</td><td>{escape(case['region'] or '—')}</td>"
            f"<td>{escape(case['punishment'])}{escape(absentia)}</td>"
            f"<td>{escape(term + fine) or '—'}</td>"
            f"<td>{escape(case['sentenced_on'] or '—')}</td>"
            f'<td>{escape(case["reason"])}<br><span class="muted">{escape(case["charge"])}'
            "</span></td>"
            f'<td><a href="/ui/articles/{case["article_id"]}">{escape(case["title"])}</a>'
            f'<br><span class="muted">{escape(case["source"])}{escape(more)}</span></td></tr>'
        )

    rows = "".join(row(case) for case in result.get("cases", []))
    shown = len(result.get("cases", []))
    return (
        '<table><thead><tr><th scope="col">Человек</th><th scope="col">Регион</th>'
        '<th scope="col">Наказание</th><th scope="col">Срок</th><th scope="col">Дата</th>'
        '<th scope="col">За что</th><th scope="col">Публикация</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
        f'<p class="muted">Показано {shown} из {result.get("total", shown)}.</p>'
    )


def _search_html(result: dict[str, Any]) -> str:
    def item(found: dict[str, Any]) -> str:
        snippet = (
            escape(found["snippet"]).replace(MARK_OPEN, "<mark>").replace(MARK_CLOSE, "</mark>")
        )
        return (
            f'<li><a href="/ui/articles/{found["article_id"]}">{escape(found["title"])}</a> '
            f'<span class="muted">[№ {found["article_id"]}] {escape(found["source"])}, '
            f"{escape(found['published'] or 'без даты')}</span><br>{snippet}</li>"
        )

    found = result.get("publications", [])
    if not found:
        return '<p class="empty">Ничего не найдено.</p>'
    return (
        f'<ul class="found">{"".join(item(one) for one in found)}</ul>'
        f'<p class="muted">Показано {len(found)} из {result.get("total", len(found))}.</p>'
    )


def _result_html(result: dict[str, Any]) -> str:
    table = {LIST: _list_html, SEARCH: _search_html}.get(result.get("tool", ""), _stats_html)
    return f"<h3>{escape(str(result.get('what', '')))}</h3>{table(result)}"


def _shown(record: ChatQuestionRecord) -> str:
    results = [call["result"] for call in record.calls]
    unverified = (
        unverified_numbers(record.answer, record.question, results)
        if record.outcome not in (REFUSED, FAILED)
        else []
    )
    warning = (
        '<p class="warning">Этих чисел нет в подсчётах под ответом, модель вывела их сама — '
        f"проверьте по таблицам: {escape(', '.join(unverified))}.</p>"
        if unverified
        else ""
    )
    basis = (
        '<details open class="basis"><summary>На чём основан ответ</summary>'
        + "".join(_result_html(result) for result in results)
        + "</details>"
        if results
        else ""
    )
    css = " failed" if record.outcome == FAILED else ""
    return (
        f'<section class="band answer{css}" id="answer"><h2>{escape(record.question)}</h2>'
        f"{warning}{_answer_html(record.answer)}{basis}"
        f'<p class="muted">{record.asked_at.astimezone():%d.%m.%Y %H:%M} · '
        f"{escape(record.model)} · ${record.cost_usd:.4f}</p></section>"
    )


@router.get("/ui/ask", response_class=HTMLResponse)
def ui_ask(
    q: int | None = Query(default=None, ge=1),
    note: str = Query(default="", max_length=300),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    history = db.scalars(
        select(ChatQuestionRecord).order_by(ChatQuestionRecord.id.desc()).limit(HISTORY)
    ).all()
    record = db.get(ChatQuestionRecord, q) if q else (history[0] if history else None)
    examples = "".join(
        '<button type="button" class="chip" onclick="document.getElementById('
        f"'ask-question').value = this.textContent\">{escape(example)}</button>"
        for example in EXAMPLES
    )
    past = "".join(
        f'<li><a href="/ui/ask?q={one.id}#answer">{escape(one.question)}</a> '
        f'<span class="muted">{one.asked_at.astimezone():%d.%m %H:%M}</span></li>'
        for one in history
    )
    body = f"""<form method="post" action="/ui/ask" class="ask-form"
  onsubmit="this.querySelector('button[type=submit]').disabled = true;
    this.querySelector('button[type=submit]').textContent = 'Считаю…'">
  <label for="ask-question">Вопрос</label>
  <textarea id="ask-question" name="question" rows="3" maxlength="{MAX_QUESTION}" required
    placeholder="Например: в каких регионах самые суровые наказания за антивоенные высказывания?"
    ></textarea>
  <button type="submit">Спросить</button>
  <span class="muted">Сегодня потрачено ${spent_today(db):.2f} из ${daily_budget():.2f}.
    Ответ занимает до 20 секунд.</span>
</form>
<p class="chips">{examples}</p>
{f'<p class="warning">{escape(note)}</p>' if note else ""}
{_shown(record) if record else '<p class="empty">Вопросов ещё не было.</p>'}
{f'<section class="band"><h2>Прежние вопросы</h2><ul>{past}</ul></section>' if past else ""}"""
    return _page(
        "Спросить",
        body,
        active="ask",
        instruction=(
            "Вопрос обычными словами. Система выбирает подсчёты по приговорам, выписанным из "
            "публикаций, или ищет по текстам публикаций, и пишет ответ по их результатам. "
            "Все числа берутся из таблиц под ответом; сверяйте ответ с ними. Это приговоры, "
            "о которых написали источники, а не вся судебная статистика."
        ),
        db=db,
    )


@router.post("/ui/ask", response_model=None)
async def ui_ask_question(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    question = form.get("question", [""])[0]
    asked = ask(session_factory_for(db), asker_from_env(), question, budget_usd=daily_budget())
    if asked.id is None:
        # Nothing was asked: the reason is all there is to show.
        return RedirectResponse(
            f"/ui/ask?{urlencode({'note': asked.answer[:300]})}", status_code=303
        )
    return RedirectResponse(f"/ui/ask?q={asked.id}#answer", status_code=303)
