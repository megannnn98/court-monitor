"""Operator console: the queue for the customer's channel (ADR 0017)."""

from datetime import UTC, datetime, timedelta
from html import escape

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from channel_feed.queue import QueueSource, draft_post, is_published
from channel_feed.unnamed import load_case_events, suggest_names
from web.candidate_rows import (
    _ALL_RF_STATUSES,
    _candidate_rows,
    _news_day,
    _period_start,
    _surname_first,
)
from web.dependencies import get_db, get_published_name_keys
from web.routers.rosfinmonitoring import list_rosfinmonitoring_snapshots
from web.ui.layout import _page

router = APIRouter()


# Suggestions look this far back: a name in another outlet comes within days.
_UNNAMED_PERIOD = timedelta(days=45)


@router.get("/ui/channel")
def ui_channel(
    snapshot_id: int | None = Query(default=None, ge=1),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    date_from: str | None = Query(default=None),
    include_administrative: bool = Query(default=False),
    db: Session = Depends(get_db),  # noqa: B008
    published_keys: frozenset[str] = Depends(get_published_name_keys),
) -> HTMLResponse:
    snapshots = list_rosfinmonitoring_snapshots(limit=20, db=db)
    selected_snapshot_id = snapshot_id or (snapshots[0].id if snapshots else None)
    if selected_snapshot_id is None:
        return _page(
            "Для канала",
            '<p class="muted">Snapshot Росфинмониторинга ещё не загружен.</p>',
            active="channel",
            instruction="Очередь людей для канала @enbv2022.",
            next_action="Импортируйте snapshot Росфинмониторинга через CLI, затем вернитесь сюда.",
            db=db,
        )
    period_start = _period_start(date_from)
    rows = _candidate_rows(
        db,
        snapshot_id=selected_snapshot_id,
        min_confidence=min_confidence,
        period_start=period_start,
        include_administrative=include_administrative,
        include_rf_statuses=_ALL_RF_STATUSES,
    )
    queue = [row for row in rows if not is_published(row.candidate, published_keys)]
    items = "".join(
        f"""<tr>
  <td>{position}</td>
  <td><a href="/ui/persons/{row.candidate.person_id}">{escape(_surname_first(row.candidate.canonical_name))}</a></td>
  <td>{_news_day(row.news.published_at).strftime("%d.%m.%Y") if row.news and row.news.published_at else ""}</td>
  <td>{escape(str(row.candidate.rosfinmonitoring_status))}</td>
  <td><textarea readonly rows="5" cols="60">{escape(draft_post(row.candidate, QueueSource(row.news.url, row.news.event_type) if row.news else None, name=_surname_first(row.candidate.canonical_name)))}</textarea></td>
</tr>"""
        for position, row in enumerate(queue, start=1)
    )
    since = datetime.now(UTC) - _UNNAMED_PERIOD
    suggestions = suggest_names(load_case_events(db, since))
    unnamed = "".join(
        f"""<tr>
  <td>{_news_day(item.unnamed.published_at).strftime("%d.%m.%Y")}</td>
  <td><a href="{escape(item.unnamed.url)}">{escape(item.unnamed.title)}</a> ({escape(item.unnamed.source)})</td>
  <td>{escape(item.named.target or "")}{f' (<a href="/ui/persons/{item.named.target_person_id}">карточка</a>)' if item.named.target_person_id else ""}</td>
  <td><a href="{escape(item.named.url)}">{escape(item.named.title)}</a> ({escape(item.named.source)})</td>
</tr>"""
        for item in suggestions
    )
    period_value = period_start.isoformat() if period_start is not None else ""
    warning = (
        None
        if published_keys
        else "Не удалось прочитать канал: уже опубликованные люди не исключены."
    )
    return _page(
        "Для канала",
        f"""<form method="get" class="toolbar">
  <label>Min confidence <input type="number" name="min_confidence" min="0" max="1" step="0.05" value="{min_confidence}"></label>
  <label>Новости с <input type="date" name="date_from" value="{period_value}"></label>
  <label><input type="checkbox" name="include_administrative" value="1" {"checked" if include_administrative else ""}> Включая административные</label>
  <button>Обновить</button>
</form>
<p class="muted">В очереди: {len(queue)} (уже опубликовано в канале: {len(rows) - len(queue)}). Любой статус РФМ: канал публикует и людей из перечня.</p>
<table><thead><tr><th>№</th><th>Человек</th><th>Дата</th><th>RF status</th><th>Черновик поста</th></tr></thead><tbody>{items}</tbody></table>
<h2>Без имени: возможное имя из другого источника</h2>
<p class="muted">Совпадение по сроку, возрасту, статье и месту в пределах трёх дней. Примерно 3 из 4 подсказок верны — проверьте обе новости.</p>
<table><thead><tr><th>Дата</th><th>Новость без имени</th><th>Возможно, это</th><th>Новость с именем</th></tr></thead><tbody>{unnamed}</tbody></table>""",
        active="channel",
        instruction="Люди с политическим преследованием, которых канал @enbv2022 ещё не публиковал.",
        next_action="Проверьте человека и новость, поправьте черновик и опубликуйте пост.",
        db=db,
        warning=warning,
    )
