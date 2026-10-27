"""«Расследование»: the dossier of one person — who, which case, what happened when, on
what evidence, why political, whether on the Rosfinmonitoring list, who and what around.

Nothing here decides: it reads what the steps wrote (the entity, its role, its verdict,
its Rosfinmonitoring matches, the events it is the target of, the charges) and shows
each conclusion beside its evidence. A fixed number of queries per page, whatever the
person's size: the timeline and the publications are capped. The graph of events is
not built here: the page's script reads it from `web.investigation_graph`.

The dates: an extracted event carries the date of its publication (or none, when its
text names another year); no event date is invented, and the page says so.
"""

from __future__ import annotations

import hashlib
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from analysis.news.case_news import KIND_LABELS as NEWS_LABELS
from analysis.news.case_news import NEW_CASE, SENTENCE
from analysis.politics.classification import CRIMINAL, POLITICAL, VERDICT_LABELS
from analysis.rosfinmonitoring.inclusion_dates import ATTRIBUTION as INCLUSION_ATTRIBUTION
from analysis.rosfinmonitoring.rf_articles import listing
from analysis.rosfinmonitoring.rf_check import FULL
from processing.roles.officials import OFFICIAL_KINDS
from processing.roles.roles import FIGURANT
from web.dependencies import get_db
from web.ui.entities import _events, _political, _regions
from web.ui.layout import _page, copy_button
from web.views.dossier import (
    ORG_ROLES,
    RELATED_LIMIT,
    TIMELINE_LIMIT,
    VERDICT_METHODS,
    Dossier,
    _day,
    identification,
    load,
    rf_entry_text,
    rf_status,
    search,
    warnings,
)
from web.views.entities import (
    EVENT_LABELS,
    NAME_SOURCES,
    ROLE_METHODS,
    article_order,
    display_name,
    role_label,
)
from web.views.layout import external_url

router = APIRouter()

_STATIC = Path(__file__).resolve().parents[1] / "static"

# The graph's scripts, in the order they load: the library, the logic, the page.
_GRAPH_SCRIPTS = (
    "vendor/vis-network/vis-network.min.js",
    "investigation-graph-core.js",
    "investigation-graph.js",
)

_GRAPH_SCRIPT_TAGS = "".join(
    f'<script defer src="/static/{name}?v='
    f'{hashlib.sha256((_STATIC / name).read_bytes()).hexdigest()[:12]}"></script>'
    for name in _GRAPH_SCRIPTS
)

# The switches of the graph (`investigation-graph-core.js` knows the same names).
_GRAPH_FILTERS = (
    ("events", "События", True),
    ("people", "Людей", True),
    ("publications", "Публикации", True),
    ("orgs", "Суды и органы", True),
    ("articles", "Статьи", True),
    ("cooccurrence", "Совместные упоминания", False),
)

_VERDICT_BADGES = {POLITICAL: "succeeded", CRIMINAL: "", "unclear": "pending"}


def _marked(quote_text: str, start: int, end: int) -> str:
    """The excerpt with the mention (or the event's trigger) marked."""
    return (
        f"…{escape(quote_text[:start])}<mark>{escape(quote_text[start:end])}</mark>"
        f"{escape(quote_text[end:])}…"
    )


def _badge(label: str, css: str = "") -> str:
    return f'<span class="badge {css}">{escape(label)}</span>'


def _article_link(article: str) -> str:
    return (
        f'<a href="/ui/entities?{urlencode({"article": article, "figurants": "all", "rf": "all"})}">'
        f"ст. {escape(article)}</a>{_political(article)}"
    )


def _header(dossier: Dossier) -> str:
    entity = dossier.entity
    role = (
        _badge(role_label(entity.role, entity.kind), "succeeded" if entity.role == FIGURANT else "")
        if entity.role
        else _badge("роль не определена", "pending")
    )
    verdict = (
        _badge(
            f"дело: {VERDICT_LABELS.get(entity.verdict, entity.verdict)}",
            _VERDICT_BADGES.get(entity.verdict, ""),
        )
        if entity.verdict
        else _badge("политичность не оценивалась")
    )
    rf_label, rf_badge = rf_status(dossier.rf)
    disputes = (
        f'<a class="badge pending" href="/ui/pairs?{urlencode({"key": entity.key})}">'
        f"нерешённых спорных пар: {len(dossier.disputes)}</a>"
        if dossier.disputes
        else _badge("спорных пар нет")
    )
    variants = ", ".join(f"{escape(str(form))} ({count})" for form, count in entity.variants)
    regions = _regions(entity.regions) if entity.regions else "не указан"
    official = entity.kind in OFFICIAL_KINDS
    return f"""<section class="band dossier-head" aria-labelledby="dossier-name">
  <div class="dossier-title">
    <h2 id="dossier-name">{escape(display_name(entity.name))}
      {copy_button(display_name(entity.name))}</h2>
    <p class="badges">{role} {verdict} {_badge(rf_label, rf_badge)} {disputes}</p>
  </div>
  <dl class="facts">
    <dt>Как писали</dt><dd>{variants}</dd>
    <dt>Регион</dt><dd>{regions}</dd>
    <dt>Публикаций</dt><dd>{entity.article_count} · упоминаний: {entity.mention_count}</dd>
    <dt>Первая публикация</dt><dd>{_day(entity.first_published_at)}</dd>
    <dt>Последняя публикация</dt><dd>{_day(entity.last_published_at)}</dd>
    <dt>События дела</dt><dd>{_events(entity.event_types) or "—"}</dd>
    <dt>Имя</dt><dd>{NAME_SOURCES.get(entity.name_source, "по правилам склейки")}{
        {"male": " · мужчина", "female": " · женщина"}.get(entity.gender or "", "")
    }</dd>
  </dl>
  <details class="manual">
    <summary>Исправить имя и ручные решения</summary>
    <form method="post" action="/ui/entities/{quote(entity.key)}/name" class="toolbar">
      <label>Имя [Отчество] Фамилия
        <input type="text" name="name" value="{escape(entity.name, quote=True)}" maxlength="200"
          required></label>
      <button type="submit" class="secondary">Исправить имя</button>
    </form>
    <form method="post" action="/ui/entities/{quote(entity.key)}/official">
      <input type="hidden" name="official" value="{"no" if official else "yes"}">
      <button type="submit" class="secondary">{
        "Не должностное лицо" if official else "Это должностное лицо"
    }</button>
    </form>
    <p class="muted">Ручные решения сохраняются и применяются при каждой пересборке. Спорные пары —
    на странице <a href="/ui/pairs">«Пары»</a>.</p>
  </details>
</section>"""


def _decision(dossier: Dossier) -> str:
    entity = dossier.entity
    evidence = (
        f'<a href="/ui/articles/{dossier.verdict_source}">исходная публикация</a>'
        if dossier.verdict_source is not None
        else '<a href="#evidence">публикации ниже</a>'
    )
    if entity.verdict:
        verdict = f"""<p>{_badge(VERDICT_LABELS.get(entity.verdict, entity.verdict), _VERDICT_BADGES.get(entity.verdict, ""))}
      <span class="muted">— {escape(VERDICT_METHODS.get(entity.verdict_method, entity.verdict_method))}</span></p>
    <p><b>Причина:</b> {escape(entity.verdict_reason)}</p>
    {f"<blockquote>{escape(entity.verdict_quote)}</blockquote>" if entity.verdict_quote else ""}
    <p class="muted">Доказательство: {evidence}. Уверенность не хранится: шаг 5 пишет вердикт,
    способ и цитату.</p>"""
    else:
        verdict = (
            '<p class="muted">Шаг 5 не оценивал это дело: оценивают только фигурантов '
            "уголовных дел.</p>"
        )
    if entity.role:
        role = f"""<p>{_badge(role_label(entity.role, entity.kind))}
      <span class="muted">— {escape(ROLE_METHODS.get(entity.role_method, entity.role_method))}</span></p>
    <p><b>Причина:</b> {escape(entity.role_reason)}</p>
    {f"<blockquote>{escape(entity.role_quote)}</blockquote>" if entity.role_quote else ""}"""
    else:
        role = '<p class="muted">Шаг 5 ещё не определял роль.</p>'
    rf_label, rf_badge = rf_status(dossier.rf)
    # The day of inclusion is said about the entry, and only where the entry was matched
    # with the patronymic: on a namesake the day belongs to somebody else.
    rf_items = "".join(
        f"<li>{_badge('ФИО с отчеством' if row.level == FULL else 'имя и фамилия', '' if row.level == FULL else 'pending')} "
        f"{escape(rf_entry_text(row))}</li>"
        for row in dossier.rf
    )
    # What the person's articles say of the list, beside what the list itself says.
    expected = listing(
        dossier.charges, FULL if any(row.level == FULL for row in dossier.rf) else None
    )
    rf_expected = f"<p>{escape(expected.text)}.</p>" if expected else ""
    return f"""<section class="band decision" aria-labelledby="decision-title">
  <h2 id="decision-title">Решение системы</h2>
  <div class="decision-grid">
    <div><h3>Политичность дела</h3>{verdict}</div>
    <div><h3>Роль в деле</h3>{role}{_news(entity)}{_known(dossier)}</div>
    <div><h3>Росфинмониторинг</h3>
      <p>{_badge(rf_label, rf_badge)}</p>
      {f"<ul>{rf_items}</ul>" if rf_items else ""}
      {rf_expected}
      <p class="muted">Перечень подтверждает личность: дата рождения и место. Последний снимок
      перечня: {_day(dossier.snapshot_date)}. Даты рождения в новостях нет — тёзку отличает
      только отчество. Дата включения — свойство записи перечня, а не человека: она не
      делает совпадение подтверждением личности. {escape(INCLUSION_ATTRIBUTION)}</p>
    </div>
  </div>
  <h3>Ограничения</h3>
  <ul class="warnings">{"".join(f"<li>{escape(item)}</li>" for item in warnings(dossier))}</ul>
</section>"""


def _charges(dossier: Dossier) -> str:
    """The articles of the person's events: which parts, political or not, whether the
    person is the only one charged in some event, in how many publications."""
    if not dossier.charges:
        return """<section class="band" id="charges" aria-labelledby="charges-title">
  <h2 id="charges-title">Статьи УК</h2>
  <p class="empty">Ни одно событие не называет статью УК рядом с этим человеком.</p>
</section>"""
    items = []
    for article in sorted(dossier.charges, key=article_order):
        charge = dossier.charges[article]
        parts = ", ".join(f"ч. {part}" for part in sorted(charge["parts"], key=article_order))
        shared = (
            ""
            if charge["sole"]
            else ' <span class="badge" title="Во всех событиях обвиняемыми названы и другие '
            'люди">общая</span>'
        )
        items.append(
            f"<li>{_article_link(article)}{f' ({escape(parts)})' if parts else ''}{shared} "
            f'<span class="muted">публикаций: {len(charge["publications"])}</span></li>'
        )
    return f"""<section class="band" id="charges" aria-labelledby="charges-title">
  <h2 id="charges-title">Статьи УК</h2>
  <p class="muted">Статья названа основанием события, где этот человек — обвиняемый. «Общая» — в
  событии обвиняемыми названы и другие люди: статья может относиться не к нему.</p>
  <ul class="charges">{"".join(items)}</ul>
</section>"""


def _news(entity: Any) -> str:
    """What the latest news is: the operator's new cases and sentences first."""
    if not entity.news_kind:
        return ""
    return f"""<h3>Свежая новость</h3>
    <p>{_badge(NEWS_LABELS.get(entity.news_kind, entity.news_kind), "succeeded" if entity.news_kind in (NEW_CASE, SENTENCE) else "")}</p>
    <p class="muted">{escape(entity.news_reason)}</p>"""


def _known(dossier: Dossier) -> str:
    """Whether the operator's own base («Найденные люди») already holds the person — by
    the name alone, and the block says how much that proves."""
    if not dossier.known_loaded:
        return ""
    match = dossier.known
    if match is None:
        badge = _badge("нет в базе", "succeeded")
        detail = "В вашей таблице «Найденные люди» никого с таким именем: вероятно, новый человек."
    else:
        badge = _badge(match.label, "pending" if match.level == "namesakes" else "")
        names = "".join(f"<li>{escape(name)}</li>" for name in match.names)
        more = (
            f'<li class="muted">…и ещё {match.count - len(match.names)}</li>'
            if match.count > len(match.names)
            else ""
        )
        detail = f"<ul>{names}{more}</ul>"
    return f"""<h3>База Airtable</h3>
    <p>{badge}</p>
    {detail}
    <p class="muted">Сверка только по имени: даты рождения и региона в таблице нет.</p>"""


def _timeline(dossier: Dossier) -> str:
    if not dossier.timeline:
        return """<section class="band" id="timeline" aria-labelledby="timeline-title">
  <h2 id="timeline-title">Хронология</h2>
  <p class="empty">Нет событий, где этот человек назван участником. Упоминания — в разделе
  «Доказательства».</p>
</section>"""
    rows = []
    for item in dossier.timeline:
        date = (
            f"<time>{_day(item.day)}</time>"
            if item.dated
            else '<time>дата события не установлена</time><span class="muted">публикация '
            f"{_day(item.day)}</span>"
        )
        articles = ", ".join(
            _article_link(article) for article in sorted(item.articles, key=article_order)
        )
        orgs = ", ".join(
            f'{escape(name)} <span class="muted">({ORG_ROLES.get(role, role)})</span>'
            for role, name in sorted(item.orgs)
        )
        sources = "".join(
            f'<li><a href="/ui/articles/{source.article_id}?start={source.text_start}&amp;end={source.text_end}">'
            f'{escape(source.title)}</a> <span class="muted">— {escape(source.source)}, '
            f"{_day(source.published_at)}</span>"
            f'<p class="quote">{_marked(source.quote, source.start, source.end)}</p></li>'
            for source in item.sources
        )
        confidence = f"достоверность {item.confidence:.2f}" if item.confidence is not None else ""
        rows.append(
            f"""<li class="timeline-item">
  <div class="timeline-date">{date}</div>
  <div class="timeline-body">
    <p><b>{escape(EVENT_LABELS.get(item.event_type, item.event_type))}</b>
      {f"· {articles}" if articles else ""}
      <span class="muted">· источников: {len(item.sources)} · {escape(confidence)}
      · извлечено: {"правилами" if "rule" in item.extractor else escape(item.extractor)}</span></p>
    {f"<p>{orgs}</p>" if orgs else ""}
    <ul class="sources">{sources}</ul>
  </div>
</li>"""
        )
    capped = (
        f'<p class="muted">Показаны первые {TIMELINE_LIMIT} событий.</p>'
        if dossier.events_capped
        else ""
    )
    return f"""<section class="band" id="timeline" aria-labelledby="timeline-title">
  <h2 id="timeline-title">Хронология</h2>
  <p class="muted">События, где человек назван участником. События одного типа в один день из
  разных публикаций — одна строка с несколькими источниками.</p>
  <ol class="timeline">{"".join(rows)}</ol>
  {capped}
</section>"""


def _related(dossier: Dossier) -> str:
    """The people of the same publications, as a table: what «Совместные упоминания» draws,
    readable without the script. A count of shared publications — not a connection."""
    if not dossier.related:
        return ""
    rows = "".join(
        f'<tr><td><a href="/ui/investigations/{quote(key)}">{escape(display_name(name))}</a></td>'
        f'<td class="num">{shared}</td></tr>'
        for key, name, shared in dossier.related
    )
    count = (
        f"первые {RELATED_LIMIT}"
        if len(dossier.related) >= RELATED_LIMIT
        else str(len(dossier.related))
    )
    return f"""<details id="links"><summary>Люди из тех же публикаций ({count})</summary>
  <table><caption>Совместные упоминания: счёт общих публикаций, не установленная связь</caption>
  <thead><tr><th scope="col">Человек</th><th scope="col">Общих публикаций</th></tr></thead>
  <tbody>{rows}</tbody></table></details>"""


def _event_graph(dossier: Dossier) -> str:
    """The place of the interactive graph: the switches, the canvas, the panel. The graph
    itself is read by the page's script from `/api/investigations/{key}/graph`; nothing
    of it is written here, so without the script the section says so and the rest of the
    dossier stands as it is."""
    address = f"/api/investigations/{quote(dossier.entity.key, safe='')}/graph"
    switches = "".join(
        f'<label class="check"><input type="checkbox" data-filter="{name}"'
        f"{' checked' if on else ''}> {label}</label>"
        for name, label, on in _GRAPH_FILTERS
    )
    return f"""<section class="band" id="graph" aria-labelledby="graph-title">
  <h2 id="graph-title">Граф событий</h2>
  <p class="muted">Человек связан с событием, в котором он назван; событие — со своими
  публикациями, судом, органом и статьёй. Двое связаны только через событие, где названы оба.
  Сообщения одного вида за один день об одном человеке показаны одним событием («Приговор ×7»):
  это сведение для чтения, каждое сообщение остаётся в своей публикации.
  «Назван в событии» — не «обвиняемый»: извлечение называет так каждого, кто стоит в предложении.
  «Совместные упоминания» — счёт общих публикаций, а не установленная связь.</p>
  <div id="investigation-graph" class="ig" data-graph-url="{escape(address, quote=True)}"
    data-expand-url="{escape(address, quote=True)}/expand">
    <fieldset class="ig-filters"><legend>Показывать</legend>{switches}</fieldset>
    <div class="ig-body">
      <div class="ig-canvas" role="application"
        aria-label="Граф событий: {escape(display_name(dossier.entity.name), quote=True)}"></div>
      <div class="ig-panel" role="region" aria-live="polite" aria-label="Выбранный узел"></div>
    </div>
    <p class="ig-toolbar"><button type="button" class="secondary ig-reset">Вернуть расположение</button>
    <span class="ig-status" role="status">Граф строится в браузере и требует JavaScript.
    Остальное досье от него не зависит.</span></p>
    <p class="legend ig-legend">● человек · ◆ событие · ■ публикация · ▲ суд · ▼ орган ·
    ⬢ статья · сплошная линия — из текста публикации · пунктир — совместное упоминание ·
    пунктирная рамка — узел ещё не раскрыт (двойное нажатие или кнопка в панели)</p>
  </div>
  {_related(dossier)}
  {_GRAPH_SCRIPT_TAGS}
</section>"""


def _evidence(dossier: Dossier) -> str:
    if not dossier.publications:
        return """<section class="band" id="evidence" aria-labelledby="evidence-title">
  <h2 id="evidence-title">Доказательства</h2><p class="empty">Публикаций нет.</p></section>"""
    cards = []
    for publication in dossier.publications:
        others = ", ".join(
            f'<a href="/ui/investigations/{quote(key)}">{escape(display_name(name))}</a>'
            for key, name in sorted(publication.others, key=lambda item: item[1])[:12]
        )
        events = ", ".join(
            escape(EVENT_LABELS.get(event, event)) for event in sorted(publication.events)
        )
        articles = ", ".join(
            _article_link(article) for article in sorted(publication.articles, key=article_order)
        )
        url = external_url(publication.url)
        external = (
            f' · <a href="{escape(url, quote=True)}" rel="noopener noreferrer" '
            'target="_blank">источник</a>'
            if url
            else ""
        )
        identified = identification(publication)
        identification_html = (
            f'<p class="muted">{escape(identified)}</p>' if identified is not None else ""
        )
        cards.append(
            f"""<article class="evidence">
  <h3><a href="/ui/articles/{publication.article_id}?start={publication.text_start}&amp;end={publication.text_end}">{escape(publication.title)}</a></h3>
  <p class="muted">{escape(publication.source)} · {_day(publication.published_at)}{external}</p>
  {identification_html}
  <p class="quote">{_marked(publication.quote, publication.start, publication.end)}</p>
  <dl class="facts compact">
    {f"<dt>События</dt><dd>{events}</dd>" if events else ""}
    {f"<dt>Статьи УК</dt><dd>{articles}</dd>" if articles else ""}
    {f"<dt>Другие люди</dt><dd>{others}</dd>" if others else ""}
  </dl>
</article>"""
        )
    shown = (
        f"Последние {len(dossier.publications)} из {dossier.entity.article_count}."
        if dossier.entity.article_count > len(dossier.publications)
        else f"Все публикации: {len(dossier.publications)}."
    )
    return f"""<section class="band" id="evidence" aria-labelledby="evidence-title">
  <h2 id="evidence-title">Доказательства</h2>
  <p class="muted">{shown} Упоминание подсвечено; заголовок открывает полный текст с той же
  подсветкой.</p>
  {"".join(cards)}
</section>"""


@router.get("/ui/investigations/{key}", response_class=HTMLResponse)
def ui_investigation(key: str, db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    dossier = load(db, key)
    if dossier is None:
        raise HTTPException(status_code=404, detail="Человек не найден")
    body = f"""<p><a href="/ui/investigations">← Найти человека</a></p>
<div class="page-toc" role="navigation" aria-label="Разделы досье">
  <a href="#decision-title">Решение</a> <a href="#charges">Статьи УК</a>
  <a href="#timeline">Хронология</a>
  <a href="#evidence">Доказательства</a> <a href="#graph">Граф событий</a>
</div>
{_header(dossier)}
{_decision(dossier)}
{_charges(dossier)}
{_timeline(dossier)}
{_evidence(dossier)}
{_event_graph(dossier)}
"""
    return _page(
        display_name(dossier.entity.name),
        body,
        active="investigations",
        instruction="Досье: кто это, что и когда произошло, на каких публикациях основан вывод.",
        db=db,
    )


@router.get("/ui/investigations", response_class=HTMLResponse)
def ui_investigations(
    q: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    found, roles = search(db, q)
    heading = f"Найдено по «{escape(q.strip())}»" if q.strip() else "Свежие политические дела"
    rows = "".join(
        f'<tr><td><a href="/ui/investigations/{quote(entity.key)}">'
        f"{escape(display_name(entity.name))}</a></td>"
        f"<td>{escape(role_label(*roles[entity.id])) if entity.id in roles else '—'}</td>"
        f'<td class="num">{entity.article_count}</td><td>{_day(entity.last_published_at)}</td></tr>'
        for entity in found
    )
    table = (
        f"""<table><caption>{heading}</caption>
<thead><tr><th scope="col">Человек</th><th scope="col">Роль</th>
<th scope="col">Публикаций</th><th scope="col">Последняя</th></tr></thead>
<tbody>{rows}</tbody></table>"""
        if rows
        else '<p class="empty">Никого не найдено.</p>'
    )
    body = f"""<form method="get" class="toolbar" role="search">
  <label for="dossier-search" class="visually-hidden">Имя или вариант написания</label>
  <input id="dossier-search" type="search" name="q" value="{escape(q, quote=True)}"
    placeholder="Имя или вариант написания">
  <button type="submit">Найти</button>
</form>
<section class="band">{table}</section>"""
    return _page(
        "Найти человека",
        body,
        active="investigations",
        instruction="Найдите человека и откройте его досье.",
        db=db,
    )


@router.get("/ui/entities/{key}", response_model=None)
def ui_entity_moved(key: str) -> RedirectResponse:
    """The entity card became the dossier; its old address still leads there."""
    return RedirectResponse(f"/ui/investigations/{quote(key)}", status_code=307)
