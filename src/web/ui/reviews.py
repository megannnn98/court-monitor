"""Operator console: entity-resolution review queue."""

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from persons.resolution.review import (
    ResolutionReviewAction,
    ResolutionReviewNotFoundError,
    ResolutionReviewStateError,
    ResolutionReviewView,
)
from web.dependencies import get_db
from web.routers.reviews import _person_resolution_reviews
from web.ui.layout import _button, _fmt, _page

router = APIRouter()


def _review_table(reviews: list[ResolutionReviewView]) -> str:
    rows = "\n".join(
        f"""<tr>
  <td><a href="/ui/person-resolution/reviews/{review.decision_id}">{review.decision_id}</a></td>
  <td>{escape(review.incoming_name)}</td>
  <td>{escape(", ".join(review.reasons))}</td>
  <td>{_fmt(review.decision_margin)}</td>
  <td>{_fmt(review.source.title)}</td>
</tr>"""
        for review in reviews
    )
    return f"""<table>
<thead><tr><th>ID</th><th>Упоминание</th><th>Причины</th><th>Margin</th><th>Источник</th></tr></thead>
<tbody>{rows}</tbody>
</table>"""


ER_REVIEW_HELP = """<section class="band">
<h2>Что такое ER-ревью</h2>
<p><strong>ER (Entity Resolution)</strong> — связывание имени из статьи с конкретной Person в базе. Один и тот же человек может быть назван полным именем, инициалами, псевдонимом или с опечаткой. Система предлагает совпадения, но не угадывает личность, когда уверенности недостаточно.</p>
<p><strong>Пример:</strong> в статье найдено упоминание <code>А. П. Иванов</code>. Система показывает Person 42 «Алексей Петров Иванов» и Person 87 «Андрей Павлов Иванов». Откройте source/evidence, сравните город, дату рождения, алиасы и контекст статьи.</p>
<p><strong>Действия:</strong> <em>Связать</em> — это тот же человек; <em>Отдельная персона</em> — кандидат похож по имени, но это другой человек; <em>Создать новую</em> — подходящего кандидата нет. Решение меняет связи упоминаний и событий, поэтому применяйте его только после проверки evidence.</p>
</section>"""


@router.get("/ui")
def ui_root() -> RedirectResponse:
    return RedirectResponse("/ui/person-resolution/reviews", status_code=303)


@router.get("/ui/person-resolution/reviews")
def ui_list_person_resolution_reviews(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    reviews = _person_resolution_reviews(db).list_pending(db, limit=limit)
    if not reviews:
        return _page(
            "ER-ревью",
            ER_REVIEW_HELP + '<section class="empty">Очередь пуста.</section>',
            active="review",
            instruction="Здесь разбираются pending ER decisions: связать упоминание с Person или создать новую.",
            next_action="Когда появятся pending decisions, откройте первое и примените явное решение.",
            db=db,
        )
    return _page(
        "ER-ревью",
        ER_REVIEW_HELP
        + f"""<section class="toolbar">
<span class="muted">Показано: {len(reviews)}</span>
<p><a class="primary" href="/ui/person-resolution/reviews/{reviews[0].decision_id}">Открыть первое</a></p>
</section>
{_review_table(reviews)}""",
        active="review",
        instruction="Разберите pending ER decisions пачкой: список отсортирован от старых к новым.",
        next_action="Откройте первое решение, сравните кандидатов и примените действие.",
        db=db,
    )


@router.get("/ui/person-resolution/reviews/{decision_id}")
def ui_get_person_resolution_review(
    decision_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    try:
        review = _person_resolution_reviews(db).get(db, decision_id)
    except ResolutionReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    candidates = []
    for candidate in review.candidates:
        other = (
            next(
                item.person_id
                for item in review.candidates
                if item.person_id != candidate.person_id
            )
            if len(review.candidates) == 2
            else None
        )
        actions = [
            _button(
                f"/ui/person-resolution/reviews/{review.decision_id}/decision"
                f"?action=link_to_person&person_id={candidate.person_id}",
                "Связать",
            )
        ]
        if other is not None:
            actions.append(
                _button(
                    f"/ui/person-resolution/reviews/{review.decision_id}/decision"
                    f"?action=keep_separate&person_id={candidate.person_id}&source_person_id={other}",
                    "Разные",
                )
            )
            actions.append(
                _button(
                    f"/ui/person-resolution/reviews/{review.decision_id}/decision"
                    f"?action=merge_persons&person_id={candidate.person_id}&source_person_id={other}",
                    "Слить",
                )
            )
        candidates.append(
            f"""<tr>
  <td><a href="/ui/persons/{candidate.person_id}">{candidate.person_id}</a></td>
  <td>{escape(candidate.canonical_name)}</td>
  <td>{_fmt(candidate.resolution_score)}</td>
  <td>{escape(candidate.surname.match)} / {escape(candidate.given_name.match)} / {escape(candidate.patronymic.match)}</td>
  <td>{escape(", ".join(candidate.aliases))}</td>
  <td>{escape(", ".join(candidate.conflicts))}</td>
  <td class="actions">{"".join(actions)}</td>
</tr>"""
        )
    source = ""
    if review.source.article_id is not None:
        source = (
            f'<p>Источник: <a href="/ui/articles/{review.source.article_id}">'
            f"{_fmt(review.source.title)}</a></p>"
        )
    return _page(
        f"ER-ревью {decision_id}",
        f"""<section class="split">
<div>
<section class="band">
  <h2>{escape(review.incoming_name)}</h2>
  <dl>
    <dt>surface</dt><dd>{_fmt(review.surface_text)}</dd>
    <dt>normalized</dt><dd>{_fmt(review.normalized_form)}</dd>
    <dt>reasons</dt><dd>{escape(", ".join(review.reasons))}</dd>
    <dt>margin</dt><dd>{_fmt(review.decision_margin)}</dd>
  </dl>
  {source}
  {_button(f"/ui/person-resolution/reviews/{review.decision_id}/decision?action=create_new_person", "Создать новую персону")}
</section>
<table>
  <thead><tr><th>ID</th><th>Персона</th><th>Score</th><th>ФИО</th><th>Алиасы</th><th>Конфликты</th><th></th></tr></thead>
  <tbody>{"".join(candidates)}</tbody>
</table>
</div>
<aside class="side-panel">
  <h2>Быстрые действия</h2>
  <p>После применения откроется следующий pending item.</p>
  <a class="secondary" href="/ui/person-resolution/reviews">К списку</a>
</aside>
</section>""",
        active="review",
        instruction="Сравните входящее упоминание с кандидатами ER и выберите ручное решение.",
        next_action="Проверьте source/evidence и нажмите безопасное действие в строке кандидата.",
        db=db,
    )


@router.post("/ui/person-resolution/reviews/{decision_id}/decision", response_model=None)
def ui_apply_person_resolution_review(
    decision_id: int,
    action: ResolutionReviewAction,
    person_id: int | None = None,
    source_person_id: int | None = None,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse | HTMLResponse:
    service = _person_resolution_reviews(db)
    try:
        service.apply(
            db,
            decision_id,
            action,
            person_id=person_id,
            source_person_id=source_person_id,
        )
    except ResolutionReviewNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResolutionReviewStateError as exc:
        db.rollback()
        return _page(
            "ER-ревью",
            f'<section class="band"><h2>Не применено</h2><p>{escape(str(exc))}</p></section>',
            active="review",
            instruction="Действие ревью не применилось: состояние данных изменилось или параметры неполные.",
            next_action="Вернитесь к решению, перечитайте кандидатов и выберите действие заново.",
            db=db,
            warning="База могла измениться между открытием страницы и нажатием кнопки.",
        )
    db.commit()
    next_reviews = service.list_pending(db, limit=1)
    if not next_reviews:
        return RedirectResponse("/ui/person-resolution/reviews", status_code=303)
    return RedirectResponse(
        f"/ui/person-resolution/reviews/{next_reviews[0].decision_id}", status_code=303
    )
