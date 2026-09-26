"""«Очередь»: what waits for the operator, one screen.

- The pairs of entities that may be one person, one at a time: both sides with their
  names, regions, articles, events and shared publications, why the pair was proposed
  and why it was not merged by itself. «Один человек» and «Разные люди» are the
  decisions of `entities.disputes.decide` (kept by key, as «manual», for every rebuild;
  the automatic merges never overwrite them); «Отложить» only moves on, for this visit.
- The unclear answers of steps 5 and 6, and the failed extractions, as lists to open.
"""

from __future__ import annotations

from html import escape
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.orm_models import (
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
)
from entities.disputes import DIFFERENT, SAME, Pair
from entities.politics import UNCLEAR as UNCLEAR_VERDICT
from entities.roles import UNCLEAR as UNCLEAR_ROLE
from web.dependencies import get_db
from web.ui.disputes import _KIND_HINTS, _why_not_merged
from web.ui.entities import (
    _article_links,
    _articles_by_group,
    _events,
    _regions,
    _rf_levels,
    _rf_mark,
    _role_mark,
    _roles,
    display_name,
)
from web.ui.layout import _page
from web.ui.workload import dispute_pairs

router = APIRouter()

LIST_LIMIT = 100
SHARED_LIMIT = 5

_SHARED = text(
    """
    WITH pubs AS (
        SELECT gm.group_id, r.article_id FROM entity_group_mentions gm
        JOIN entity_mentions m ON m.id = gm.mention_id
        JOIN article_extraction_runs r ON r.id = m.extraction_run_id
        WHERE gm.group_id IN (:a, :b)
    )
    SELECT a.id, a.title, a.published_at FROM parsed_articles a
    WHERE a.id IN (SELECT article_id FROM pubs WHERE group_id = :a)
      AND a.id IN (SELECT article_id FROM pubs WHERE group_id = :b)
    ORDER BY a.published_at DESC NULLS LAST LIMIT :limit
    """
)
_FAILED_EXTRACTIONS = text(
    """
    SELECT a.id, a.title, left(r.error_message, 240), r.created_at
    FROM article_extraction_runs r JOIN parsed_articles a ON a.id = r.article_id
    WHERE r.status = 'failed'
      AND NOT EXISTS (SELECT 1 FROM article_extraction_runs ok
                      WHERE ok.article_id = r.article_id AND ok.status = 'succeeded')
    ORDER BY r.created_at DESC LIMIT :limit
    """
)


def pair_id(pair: Pair) -> str:
    """How a pair is named in the address: its two keys."""
    return "|".join(pair.keys)


def _side(
    entity: EntityGroupRecord,
    roles: dict[int, tuple[str, str | None]],
    listed: dict[int, str],
    charges: dict[int, list[tuple[str, bool]]],
    label: str,
) -> str:
    forms = ", ".join(f"{escape(str(form))} ({count})" for form, count in entity.variants[:6])
    articles = _article_links(charges.get(entity.id, []), {"figurants": "all", "rf": "all"})
    return f"""<div class="pair-side" aria-label="{label}">
  <p class="muted">{label}</p>
  <h3><a href="/ui/investigations/{quote(entity.key)}">{escape(display_name(entity.name))}</a></h3>
  <p class="badges">{_role_mark(roles.get(entity.id))}{_rf_mark(listed.get(entity.id))}</p>
  <dl class="facts compact">
    <dt>Как писали</dt><dd>{forms}</dd>
    <dt>Регион</dt><dd>{_regions(entity.regions) if entity.regions else "не указан"}</dd>
    <dt>Статьи УК</dt><dd>{articles or "—"}</dd>
    <dt>События</dt><dd>{_events(entity.event_types) or "—"}</dd>
    <dt>Публикаций</dt><dd>{entity.article_count} · упоминаний: {entity.mention_count}</dd>
  </dl>
</div>"""


def _pair_card(
    db: Session, pair: Pair, pairs: list[Pair], position: int, total: int, skip: list[str]
) -> str:
    records = {
        record.id: record
        for record in db.scalars(
            select(EntityGroupRecord).where(EntityGroupRecord.id.in_((pair.left.id, pair.right.id)))
        )
    }
    left, right = records.get(pair.left.id), records.get(pair.right.id)
    if left is None or right is None:
        return '<p class="empty">Пара уже решена.</p>'
    ids = [left.id, right.id]
    roles, listed, charges = _roles(db, ids), _rf_levels(db, ids), _articles_by_group(db, ids)
    shared = db.execute(_SHARED, {"a": left.id, "b": right.id, "limit": SHARED_LIMIT}).all()
    shared_html = (
        "<ul>"
        + "".join(
            f'<li><a href="/ui/articles/{article_id}">{escape(title)}</a> '
            f'<span class="muted">{published_at.astimezone():%d.%m.%Y}</span></li>'
            if published_at
            else f'<li><a href="/ui/articles/{article_id}">{escape(title)}</a></li>'
            for article_id, title, published_at in shared
        )
        + "</ul>"
        if shared
        else '<p class="muted">Общих публикаций нет.</p>'
    )
    note = _why_not_merged(db, pairs).get(pair.keys, "")
    blockers = note or (
        "Автоматически сливаются только пары, где один человек однозначен (одна сторона в "
        "перечне Росфинмониторинга или один регион); здесь это не так."
    )
    key_a, key_b = pair.keys
    postponed = urlencode([("skip", item) for item in [*skip, pair_id(pair)]])
    hidden_skip = "".join(
        f'<input type="hidden" name="skip" value="{escape(item, quote=True)}">' for item in skip
    )
    return f"""<article class="pair-card" aria-labelledby="pair-title">
  <h3 id="pair-title">Пара {position} из {total}</h3>
  <p><b>Почему предложена:</b> {escape(_KIND_HINTS[pair.kind])}.</p>
  <p><b>Почему не слита автоматически:</b> {escape(blockers)}</p>
  <div class="pair-sides">{_side(left, roles, listed, charges, "Первая сущность")}{
        _side(right, roles, listed, charges, "Вторая сущность")
    }</div>
  <h4>Общие публикации</h4>
  {shared_html}
  <form method="post" action="/ui/disputes/decide" class="decide-bar">
    <input type="hidden" name="key_a" value="{escape(key_a, quote=True)}">
    <input type="hidden" name="key_b" value="{escape(key_b, quote=True)}">
    <input type="hidden" name="back" value="queue">
    {hidden_skip}
    <button name="decision" value="{SAME}" type="submit">Один человек</button>
    <button name="decision" value="{DIFFERENT}" type="submit" class="secondary">Разные люди</button>
    <a class="button-link secondary" href="/ui/queue?{postponed}#pairs">Отложить</a>
  </form>
</article>"""


def _entity_rows(db: Session, rows: list[tuple[str, str, str]], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{empty}</p>'
    body = "".join(
        f'<tr><td><a href="/ui/investigations/{quote(key)}">{escape(display_name(name))}</a></td>'
        f"<td>{escape(reason)}</td></tr>"
        for key, name, reason in rows
    )
    return (
        '<table><thead><tr><th scope="col">Человек</th><th scope="col">Почему не решено</th>'
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


@router.get("/ui/queue", response_class=HTMLResponse)
def ui_queue(
    key: str = Query(default="", max_length=300),
    skip: list[str] = Query(default_factory=list),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    pairs = dispute_pairs(db)
    skipped = set(skip)
    waiting = [pair for pair in pairs if pair_id(pair) not in skipped]
    if key:
        # From a dossier: that person's pairs first.
        waiting.sort(key=lambda pair: key not in pair.keys)
    current = waiting[0] if waiting else None
    if current is not None:
        pair_html = _pair_card(
            db, current, pairs, pairs.index(current) + 1, len(pairs), sorted(skipped)
        )
    elif pairs:
        pair_html = (
            f'<p class="empty">Все {len(pairs)} пар отложены. '
            '<a href="/ui/queue#pairs">Начать сначала</a>.</p>'
        )
    else:
        pair_html = '<p class="empty">Спорных пар нет.</p>'
    roles = [
        (entity_key, name, reason)
        for entity_key, name, reason in db.execute(
            select(EntityGroupRecord.key, EntityGroupRecord.name, EntityGroupRoleRecord.reason)
            .join(EntityGroupRoleRecord, EntityGroupRoleRecord.group_id == EntityGroupRecord.id)
            .where(EntityGroupRoleRecord.role == UNCLEAR_ROLE)
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
            .limit(LIST_LIMIT)
        ).all()
    ]
    verdicts = [
        (entity_key, name, reason)
        for entity_key, name, reason in db.execute(
            select(EntityGroupRecord.key, EntityGroupRecord.name, EntityGroupPoliticsRecord.reason)
            .join(
                EntityGroupPoliticsRecord,
                EntityGroupPoliticsRecord.group_id == EntityGroupRecord.id,
            )
            .where(EntityGroupPoliticsRecord.verdict == UNCLEAR_VERDICT)
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
            .limit(LIST_LIMIT)
        ).all()
    ]
    failed = db.execute(_FAILED_EXTRACTIONS, {"limit": LIST_LIMIT}).all()
    failed_html = (
        '<table><thead><tr><th scope="col">Публикация</th><th scope="col">Ошибка</th>'
        "</tr></thead><tbody>"
        + "".join(
            f'<tr><td><a href="/ui/articles/{article_id}">{escape(title)}</a></td>'
            f"<td>{escape(message or '')}</td></tr>"
            for article_id, title, message, _created_at in failed
        )
        + "</tbody></table>"
        if failed
        else '<p class="empty">Сбоев нет.</p>'
    )
    body = f"""<p class="queue-summary chips">
  <a class="chip" href="#pairs">Спорные пары: {len(pairs)}</a>
  <a class="chip" href="#roles">Неясная роль: {len(roles)}</a>
  <a class="chip" href="#verdicts">Неясная политичность: {len(verdicts)}</a>
  <a class="chip" href="#failed">Сбои извлечения: {len(failed)}</a>
</p>
<section class="band" id="pairs" aria-labelledby="pairs-title">
  <h2 id="pairs-title">Спорные совпадения людей</h2>
  <p class="muted">Решение сохраняется вручную и применяется при каждой пересборке; автоматика его
  не перезаписывает. «Отложить» только показывает следующую пару.</p>
  {pair_html}
</section>
<section class="band" id="roles" aria-labelledby="roles-title">
  <h2 id="roles-title">Неясная роль в деле</h2>
  <p class="muted">Шаг 5 не понял, заведено ли на человека дело. Откройте досье и проверьте
  цитаты.</p>
  {_entity_rows(db, roles, "Неясных ролей нет.")}
</section>
<section class="band" id="verdicts" aria-labelledby="verdicts-title">
  <h2 id="verdicts-title">Неясная политичность</h2>
  <p class="muted">Шаг 6 не смог отнести дело ни к политическим, ни к обычным уголовным.</p>
  {_entity_rows(db, verdicts, "Неясных дел нет.")}
</section>
<section class="band" id="failed" aria-labelledby="failed-title">
  <h2 id="failed-title">Сбои извлечения</h2>
  <p class="muted">Публикации, из которых не удалось извлечь людей и события; следующая загрузка
  попробует снова.</p>
  {failed_html}
</section>"""
    return _page(
        "Очередь",
        body,
        active="queue",
        instruction="Всё, что ждёт решения оператора, на одном экране.",
        next_action="Решите пару — откроется следующая; неясные случаи откройте в досье.",
        db=db,
    )
