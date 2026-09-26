"""«Расследование»: the dossier of one person — who, which case, what happened when, on
what evidence, why political, whether on the Rosfinmonitoring list, who and what around.

Nothing here decides: it reads what the steps wrote (the entity, its role, its verdict,
its Rosfinmonitoring matches, the events it is the target of, the charges) and shows
each conclusion beside its evidence. A fixed number of queries per page, whatever the
person's size: the timeline, the publications and the graph are capped.

The dates: an extracted event carries the date of its publication (or none, when its
text names another year); no event date is invented, and the page says so.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Text, cast, or_, select, text
from sqlalchemy.orm import Session

from db.orm_models import (
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
)
from entities.news import KIND_LABELS as NEWS_LABELS
from entities.news import NEW_CASE, SENTENCE
from entities.officials import OFFICIAL_KINDS
from entities.politics import CRIMINAL, POLITICAL
from entities.rf_check import FULL
from entities.roles import FIGURANT
from web.dependencies import get_db
from web.ui.entities import (
    _EVENT_LABELS,
    _NAME_SOURCES,
    _ROLE_METHODS,
    _article_order,
    _events,
    _political,
    _regions,
    _role_label,
    display_name,
)
from web.ui.layout import _page, external_url
from web.ui.workload import dispute_pairs

router = APIRouter()

TIMELINE_LIMIT = 200
PUBLICATION_LIMIT = 30
RELATED_LIMIT = 30
GRAPH_LIMIT = 18
QUOTE_CONTEXT = 160

VERDICT_LABELS = {POLITICAL: "политическое", CRIMINAL: "обычное уголовное", "unclear": "неясно"}
_VERDICT_BADGES = {POLITICAL: "succeeded", CRIMINAL: "", "unclear": "pending"}
_VERDICT_METHODS = {
    "article": "правило: статья УК из списка политических (или только обычной уголовщины)",
    "memorial": "правило: категория реестра «Мемориала»",
    "model": "модель по цитатам из публикаций",
}
_ORG_ROLES = {"court": "суд", "authority": "орган"}

# Q1 — the entity with its role, its verdict and its first and latest publication.
_ENTITY = text(
    """
    SELECT g.id, g.key, g.name, g.variants, g.regions, g.mention_count, g.article_count,
           g.last_published_at, g.name_source, g.gender, g.event_types,
           ro.role, ro.kind, ro.method AS role_method, ro.reason AS role_reason,
           ro.quote AS role_quote,
           po.verdict, po.method AS verdict_method, po.reason AS verdict_reason,
           po.quote AS verdict_quote,
           ne.kind AS news_kind, ne.reason AS news_reason,
           (SELECT min(a.published_at) FROM entity_group_mentions gm
              JOIN entity_mentions m ON m.id = gm.mention_id
              JOIN article_extraction_runs r ON r.id = m.extraction_run_id
              JOIN parsed_articles a ON a.id = r.article_id
            WHERE gm.group_id = g.id) AS first_published_at
    FROM entity_groups g
    LEFT JOIN entity_group_roles ro ON ro.group_id = g.id
    LEFT JOIN entity_group_politics po ON po.group_id = g.id
    LEFT JOIN entity_group_news ne ON ne.group_id = g.id
    WHERE g.key = :key
    """
)
# Q2 — the Rosfinmonitoring matches, and the list snapshot the check could last read.
_RF = text(
    """
    SELECT m.level, e.full_name, e.birth_date, e.birth_place, s.snapshot_date
    FROM entity_group_rf_matches m
    JOIN rosfinmonitoring_entries e ON e.id = m.entry_id
    JOIN rosfinmonitoring_snapshots s ON s.id = e.snapshot_id
    WHERE m.group_id = :group
    UNION ALL
    SELECT NULL, NULL, NULL, NULL, max(snapshot_date) FROM rosfinmonitoring_snapshots
    ORDER BY 1 NULLS LAST, 2
    """
)
# Q3 — the events whose target this person is, with an excerpt around the trigger.
_EVENTS = text(
    """
    SELECT DISTINCT e.id, e.event_type, e.event_date, e.confidence, e.extractor_name,
           e.start_offset, e.end_offset,
           a.id AS article_id, a.title, a.published_at, d.canonical_url, s.name AS source,
           substr(a.text, greatest(e.start_offset - :context, 0) + 1,
                  e.end_offset - greatest(e.start_offset - :context, 0) + :context) AS quote,
           greatest(e.start_offset - :context, 0) AS quote_start
    FROM entity_group_mentions gm
    JOIN event_entity_mentions em ON em.mention_id = gm.mention_id AND em.role = 'target'
    JOIN extracted_events e ON e.id = em.event_id
    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE gm.group_id = :group
    ORDER BY a.published_at NULLS LAST, e.id
    LIMIT :limit
    """
)
# Q4 — the Criminal Code articles of this person's events (step 3's charges).
_CHARGES = text(
    """
    SELECT event_id, publication_id, article, part, other_targets
    FROM entity_group_charges WHERE group_id = :group
    """
)
# Q5 — the courts and bodies those events name, as the text wrote them.
_ORGS = text(
    """
    SELECT em.event_id, em.role, m.surface_text
    FROM event_entity_mentions em JOIN entity_mentions m ON m.id = em.mention_id
    WHERE em.event_id = ANY(:events) AND em.role IN ('court', 'authority')
    """
)
# Q6 — the publications naming this person, the latest first, an excerpt around the
# first mention in each.
_PUBLICATIONS = text(
    """
    SELECT * FROM (
        SELECT DISTINCT ON (a.id) a.id, a.title, a.published_at, d.canonical_url,
               s.name AS source, m.start_offset, m.end_offset,
               substr(a.text, greatest(m.start_offset - :context, 0) + 1,
                      m.end_offset - greatest(m.start_offset - :context, 0) + :context) AS quote,
               greatest(m.start_offset - :context, 0) AS quote_start
        FROM entity_group_mentions gm
        JOIN entity_mentions m ON m.id = gm.mention_id
        JOIN article_extraction_runs r ON r.id = m.extraction_run_id
        JOIN parsed_articles a ON a.id = r.article_id
        JOIN source_documents d ON d.id = a.document_id
        JOIN sources s ON s.id = d.source_id
        WHERE gm.group_id = :group
        ORDER BY a.id, m.start_offset
    ) found
    ORDER BY published_at DESC NULLS LAST, id DESC
    LIMIT :limit
    """
)
# Q7 — the other people of these publications.
_OTHERS = text(
    """
    SELECT DISTINCT r.article_id, g.key, g.name
    FROM entity_group_mentions gm
    JOIN entity_groups g ON g.id = gm.group_id
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    WHERE r.article_id = ANY(:articles) AND gm.group_id <> :group
    """
)
# Q8 — the people most often in the same publications.
_RELATED = text(
    """
    WITH mine AS (
        SELECT DISTINCT r.article_id FROM entity_group_mentions gm
        JOIN entity_mentions m ON m.id = gm.mention_id
        JOIN article_extraction_runs r ON r.id = m.extraction_run_id
        WHERE gm.group_id = :group
    )
    SELECT g.key, g.name, count(DISTINCT r.article_id) AS shared
    FROM entity_group_mentions gm
    JOIN entity_groups g ON g.id = gm.group_id
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    WHERE r.article_id IN (SELECT article_id FROM mine) AND gm.group_id <> :group
    GROUP BY g.key, g.name ORDER BY shared DESC, g.name LIMIT :limit
    """
)
# Q9 — the publication a verdict's quote comes from (its text, spaces folded).
_QUOTE_SOURCE = text(
    """
    SELECT a.id FROM entity_group_mentions gm
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    WHERE gm.group_id = :group
      AND position(:needle IN regexp_replace(a.text, '\\s+', ' ', 'g')) > 0
    ORDER BY a.published_at DESC NULLS LAST LIMIT 1
    """
)


@dataclass
class Source:
    article_id: int
    title: str
    source: str
    url: str
    published_at: datetime | None
    quote: str
    # The marked span: inside the quote, and in the whole text (for the link).
    start: int
    end: int
    text_start: int
    text_end: int


@dataclass
class TimelineItem:
    """One event of the case: the events of one type on one publication day, merged —
    several publications telling the same event are one item with several sources."""

    event_type: str
    # The day it is dated by: its publication's (None: the text names another year).
    day: datetime | None
    dated: bool
    confidence: float | None
    extractor: str
    sources: list[Source] = field(default_factory=list)
    articles: set[str] = field(default_factory=set)
    orgs: set[tuple[str, str]] = field(default_factory=set)


@dataclass
class Publication:
    article_id: int
    title: str
    source: str
    url: str
    published_at: datetime | None
    quote: str
    start: int
    end: int
    text_start: int
    text_end: int
    events: set[str] = field(default_factory=set)
    articles: set[str] = field(default_factory=set)
    others: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Node:
    kind: str
    label: str
    href: str
    relation: str


@dataclass
class Dossier:
    entity: Any
    rf: list[Any]
    snapshot_date: datetime | None
    timeline: list[TimelineItem]
    events_capped: bool
    charges: dict[str, dict[str, Any]]
    orgs: Counter[tuple[str, str]]
    publications: list[Publication]
    related: list[tuple[str, str, int]]
    verdict_source: int | None
    disputes: list[str]


def _marked(quote_text: str, start: int, end: int) -> str:
    """The excerpt with the mention (or the event's trigger) marked."""
    return (
        f"…{escape(quote_text[:start])}<mark>{escape(quote_text[start:end])}</mark>"
        f"{escape(quote_text[end:])}…"
    )


def load(db: Session, key: str) -> Dossier | None:
    entity = db.execute(_ENTITY, {"key": key}).first()
    if entity is None:
        return None
    rf_rows = db.execute(_RF, {"group": entity.id}).all()
    matches = [row for row in rf_rows if row.level is not None]
    snapshot_date = next((row.snapshot_date for row in rf_rows if row.level is None), None)

    event_rows = db.execute(
        _EVENTS, {"group": entity.id, "context": QUOTE_CONTEXT, "limit": TIMELINE_LIMIT + 1}
    ).all()
    events_capped = len(event_rows) > TIMELINE_LIMIT
    event_rows = event_rows[:TIMELINE_LIMIT]
    charges: dict[str, dict[str, Any]] = {}
    articles_by_event: dict[int, set[str]] = defaultdict(set)
    articles_by_publication: dict[int, set[str]] = defaultdict(set)
    for event_id, publication_id, article, part, others in db.execute(
        _CHARGES, {"group": entity.id}
    ).all():
        charge = charges.setdefault(article, {"parts": set(), "sole": False, "publications": set()})
        if part:
            charge["parts"].add(part)
        charge["sole"] = charge["sole"] or others == 0
        charge["publications"].add(publication_id)
        articles_by_event[event_id].add(article)
        articles_by_publication[publication_id].add(article)
    orgs_by_event: dict[int, set[tuple[str, str]]] = defaultdict(set)
    if event_rows:
        for event_id, role, name in db.execute(
            _ORGS, {"events": [row.id for row in event_rows]}
        ).all():
            if name:
                orgs_by_event[event_id].add((role, " ".join(str(name).split())))

    items: dict[tuple[str, Any], TimelineItem] = {}
    for row in event_rows:
        moment = row.event_date or row.published_at
        # The day as the page shows it: the local one.
        day = moment.astimezone().date() if moment else None
        item = items.setdefault(
            (row.event_type, day),
            TimelineItem(
                event_type=row.event_type,
                day=row.event_date or row.published_at,
                dated=row.event_date is not None,
                confidence=row.confidence,
                extractor=row.extractor_name,
            ),
        )
        item.dated = item.dated and row.event_date is not None
        item.articles |= articles_by_event.get(row.id, set())
        item.orgs |= orgs_by_event.get(row.id, set())
        if all(source.article_id != row.article_id for source in item.sources):
            item.sources.append(
                Source(
                    row.article_id,
                    row.title,
                    row.source,
                    row.canonical_url,
                    row.published_at,
                    row.quote or "",
                    row.start_offset - row.quote_start,
                    row.end_offset - row.quote_start,
                    row.start_offset,
                    row.end_offset,
                )
            )
    orgs: Counter[tuple[str, str]] = Counter(
        org for orgs_of in orgs_by_event.values() for org in orgs_of
    )

    publications = [
        Publication(
            row.id,
            row.title,
            row.source,
            row.canonical_url,
            row.published_at,
            row.quote or "",
            row.start_offset - row.quote_start,
            row.end_offset - row.quote_start,
            row.start_offset,
            row.end_offset,
            articles=set(articles_by_publication.get(row.id, set())),
        )
        for row in db.execute(
            _PUBLICATIONS,
            {"group": entity.id, "context": QUOTE_CONTEXT, "limit": PUBLICATION_LIMIT},
        ).all()
    ]
    events_by_publication: dict[int, set[str]] = defaultdict(set)
    for row in event_rows:
        events_by_publication[row.article_id].add(row.event_type)
    by_id = {publication.article_id: publication for publication in publications}
    for publication in publications:
        publication.events = events_by_publication.get(publication.article_id, set())
    if by_id:
        for article_id, other_key, other_name in db.execute(
            _OTHERS, {"articles": list(by_id), "group": entity.id}
        ).all():
            by_id[article_id].others.append((other_key, other_name))
    related = [
        (row.key, row.name, row.shared)
        for row in db.execute(_RELATED, {"group": entity.id, "limit": RELATED_LIMIT}).all()
    ]

    verdict_source = None
    needle = " ".join((entity.verdict_quote or "").split())[:80]
    if needle:
        verdict_source = db.scalar(_QUOTE_SOURCE, {"group": entity.id, "needle": needle})
    disputes = [
        pair.right.key if pair.left.key == key else pair.left.key
        for pair in dispute_pairs(db)
        if key in pair.keys
    ]
    return Dossier(
        entity=entity,
        rf=matches,
        snapshot_date=snapshot_date,
        timeline=sorted(
            items.values(),
            key=lambda item: (
                item.day is None,
                item.day or datetime.min.replace(tzinfo=UTC),
                item.event_type,
            ),
        ),
        events_capped=events_capped,
        charges=charges,
        orgs=orgs,
        publications=publications,
        related=related,
        verdict_source=verdict_source,
        disputes=disputes,
    )


def _day(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m.%Y") if moment else "—"


def rf_status(matches: list[Any]) -> tuple[str, str]:
    """(label, badge): on the list for certain, maybe a namesake, or not found."""
    if any(match.level == FULL for match in matches):
        return "в перечне Росфинмониторинга", ""
    if matches:
        return "возможно в перечне (тёзка без отчества)", "pending"
    return "не найден в перечне", ""


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
        _badge(
            _role_label(entity.role, entity.kind), "succeeded" if entity.role == FIGURANT else ""
        )
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
        f'<a class="badge pending" href="/ui/queue?{urlencode({"key": entity.key})}">'
        f"нерешённых спорных пар: {len(dossier.disputes)}</a>"
        if dossier.disputes
        else _badge("спорных пар нет")
    )
    variants = ", ".join(f"{escape(str(form))} ({count})" for form, count in entity.variants)
    regions = _regions(entity.regions) if entity.regions else "не указан"
    official = entity.kind in OFFICIAL_KINDS
    return f"""<section class="band dossier-head" aria-labelledby="dossier-name">
  <div class="dossier-title">
    <h2 id="dossier-name">{escape(display_name(entity.name))}</h2>
    <p class="badges">{role} {verdict} {_badge(rf_label, rf_badge)} {disputes}</p>
  </div>
  <dl class="facts">
    <dt>Как писали</dt><dd>{variants}</dd>
    <dt>Регион</dt><dd>{regions}</dd>
    <dt>Публикаций</dt><dd>{entity.article_count} · упоминаний: {entity.mention_count}</dd>
    <dt>Первая публикация</dt><dd>{_day(entity.first_published_at)}</dd>
    <dt>Последняя публикация</dt><dd>{_day(entity.last_published_at)}</dd>
    <dt>События дела</dt><dd>{_events(entity.event_types) or "—"}</dd>
    <dt>Имя</dt><dd>{_NAME_SOURCES.get(entity.name_source, "по правилам склейки")}{
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
    в <a href="/ui/queue">«Очереди»</a>.</p>
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
      <span class="muted">— {escape(_VERDICT_METHODS.get(entity.verdict_method, entity.verdict_method))}</span></p>
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
        role = f"""<p>{_badge(_role_label(entity.role, entity.kind))}
      <span class="muted">— {escape(_ROLE_METHODS.get(entity.role_method, entity.role_method))}</span></p>
    <p><b>Причина:</b> {escape(entity.role_reason)}</p>
    {f"<blockquote>{escape(entity.role_quote)}</blockquote>" if entity.role_quote else ""}"""
    else:
        role = '<p class="muted">Шаг 5 ещё не определял роль.</p>'
    rf_label, rf_badge = rf_status(dossier.rf)
    rf_items = "".join(
        f"<li>{_badge('ФИО с отчеством' if row.level == FULL else 'имя и фамилия', '' if row.level == FULL else 'pending')} "
        f"{escape(row.full_name)}{f', {row.birth_date:%d.%m.%Y} г.р.' if row.birth_date else ''}"
        f"{f', {escape(row.birth_place)}' if row.birth_place else ''}</li>"
        for row in dossier.rf
    )
    warnings = ["Даты событий — даты публикаций: точной даты события в базе нет."]
    if any(not charge["sole"] for charge in dossier.charges.values()):
        warnings.append("Часть статей УК «общая»: в событии обвиняемыми названы и другие люди.")
    if dossier.rf and not any(row.level == FULL for row in dossier.rf):
        warnings.append("Совпадение с перечнем только по имени и фамилии: может быть тёзка.")
    if entity.verdict == "unclear" or entity.role == "unclear":
        warnings.append("Система не смогла решить — случай в «Очереди».")
    if dossier.disputes:
        warnings.append("Есть нерешённые спорные пары: возможно, это тот же человек.")
    if entity.name_source == "rules":
        warnings.append("Имя собрано правилами, не проверено моделью или человеком.")
    return f"""<section class="band decision" aria-labelledby="decision-title">
  <h2 id="decision-title">Решение системы</h2>
  <div class="decision-grid">
    <div><h3>Политичность дела</h3>{verdict}</div>
    <div><h3>Роль в деле</h3>{role}{_news(entity)}</div>
    <div><h3>Росфинмониторинг</h3>
      <p>{_badge(rf_label, rf_badge)}</p>
      {f"<ul>{rf_items}</ul>" if rf_items else ""}
      <p class="muted">Перечень подтверждает личность: дата рождения и место. Последний снимок
      перечня: {_day(dossier.snapshot_date)}. Даты рождения в новостях нет — тёзку отличает
      только отчество.</p>
    </div>
  </div>
  <h3>Ограничения</h3>
  <ul class="warnings">{"".join(f"<li>{escape(item)}</li>" for item in warnings)}</ul>
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
    for article in sorted(dossier.charges, key=_article_order):
        charge = dossier.charges[article]
        parts = ", ".join(f"ч. {part}" for part in sorted(charge["parts"], key=_article_order))
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
            f'<time>{_day(item.day)}</time><span class="muted">по дате публикации</span>'
            if item.dated
            else '<time>дата не установлена</time><span class="muted">в тексте другой год; '
            f"публикация {_day(item.day)}</span>"
        )
        articles = ", ".join(
            _article_link(article) for article in sorted(item.articles, key=_article_order)
        )
        orgs = ", ".join(
            f'{escape(name)} <span class="muted">({_ORG_ROLES.get(role, role)})</span>'
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
    <p><b>{escape(_EVENT_LABELS.get(item.event_type, item.event_type))}</b>
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


# Nodes of each kind at most: one kind must not crowd the others out of the graph.
_GRAPH_SHARES = {"article": 5, "person": 6, "org": 4, "publication": 3, "event": 3}


def graph_nodes(dossier: Dossier) -> tuple[list[Node], int]:
    """The most telling connections, at most `GRAPH_LIMIT`, a share of each kind: the
    articles, the people, the courts and bodies, the publications, the events. Only
    what the data states — shared publications, events and their charges; no
    similarity. Also how many there are in all: the rest is in the page's sections."""
    articles = [
        Node(
            "article",
            f"ст. {article}",
            f"/ui/entities?{urlencode({'article': article, 'figurants': 'all', 'rf': 'all'})}",
            "обвинение по статье",
        )
        for article in sorted(dossier.charges, key=_article_order)
    ]
    people = [
        Node(
            "person",
            display_name(name),
            f"/ui/investigations/{quote(key)}",
            f"общих публикаций: {shared}",
        )
        for key, name, shared in dossier.related
    ]
    orgs = [
        Node(
            "org",
            name,
            f"/ui/publications?{urlencode({'q': name})}",
            f"{_ORG_ROLES.get(role, role)} в событиях: {count}",
        )
        for (role, name), count in dossier.orgs.most_common()
    ]
    publications = [
        Node(
            "publication",
            publication.title,
            f"/ui/articles/{publication.article_id}",
            "упомянут в публикации",
        )
        for publication in dossier.publications
    ]
    events = [
        Node(
            "event",
            _EVENT_LABELS.get(event_type, event_type),
            "#timeline",
            f"участник события: {count}",
        )
        for event_type, count in Counter(item.event_type for item in dossier.timeline).most_common()
    ]
    kinds = {
        "article": articles,
        "person": people,
        "org": orgs,
        "publication": publications,
        "event": events,
    }
    shown = [node for kind, nodes in kinds.items() for node in nodes[: _GRAPH_SHARES[kind]]]
    return shown[:GRAPH_LIMIT], sum(len(nodes) for nodes in kinds.values())


_NODE_KINDS = {
    "person": "человек",
    "article": "статья УК",
    "org": "суд или орган",
    "publication": "публикация",
    "event": "событие",
}


def _short(label: str, size: int = 22) -> str:
    return label if len(label) <= size else label[: size - 1] + "…"


def _graph(dossier: Dossier) -> str:
    nodes, total = graph_nodes(dossier)
    name = display_name(dossier.entity.name)
    # An ellipse: the labels at the sides have room, the top and bottom ones less.
    width, height, rx, ry = 820, 440, 235, 170
    cx, cy = width / 2, height / 2
    shapes = []
    for index, node in enumerate(nodes):
        angle = 2 * math.pi * index / max(len(nodes), 1) - math.pi / 2
        x, y = cx + rx * math.cos(angle), cy + ry * math.sin(angle)
        anchor = "start" if math.cos(angle) > 0.2 else "end" if math.cos(angle) < -0.2 else "middle"
        dx = 12 if anchor == "start" else -12 if anchor == "end" else 0
        dy = 4 if anchor != "middle" else (-12 if math.sin(angle) < 0 else 20)
        shapes.append(
            f'<line x1="{cx:.0f}" y1="{cy:.0f}" x2="{x:.0f}" y2="{y:.0f}" class="edge edge-{node.kind}">'
            f"<title>{escape(node.relation)}</title></line>"
        )
        shapes.append(
            f'<a href="{escape(node.href, quote=True)}" aria-label="{escape(_NODE_KINDS[node.kind])}: '
            f'{escape(node.label, quote=True)} — {escape(node.relation, quote=True)}">'
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="8" class="node node-{node.kind}"/>'
            f'<text x="{x + dx:.0f}" y="{y + dy:.0f}" text-anchor="{anchor}">{escape(_short(node.label))}</text>'
            f"<title>{escape(node.label)} — {escape(node.relation)}</title></a>"
        )
    svg = (
        f'<svg class="graph" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Граф связей: {escape(name, quote=True)} и {len(nodes)} связанных узлов">'
        f"{''.join(shapes)}"
        f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="14" class="node node-center"/>'
        f'<text x="{cx:.0f}" y="{cy + 32:.0f}" text-anchor="middle" class="center-label">'
        f"{escape(_short(name, 28))}</text></svg>"
    )
    legend = "".join(
        f'<span class="legend-item"><i class="swatch node-{kind}"></i>{label}</span>'
        for kind, label in _NODE_KINDS.items()
    )
    rows = "".join(
        f"<tr><td>{escape(_NODE_KINDS[node.kind])}</td>"
        f'<td><a href="{escape(node.href, quote=True)}">{escape(node.label)}</a></td>'
        f"<td>{escape(node.relation)}</td></tr>"
        for node in nodes
    )
    people = "".join(
        f'<tr><td><a href="/ui/investigations/{quote(key)}">{escape(display_name(name))}</a></td>'
        f'<td class="num">{shared}</td></tr>'
        for key, name, shared in dossier.related
    )
    more = (
        f"""<details><summary>Показать все связанные люди ({len(dossier.related)})</summary>
  <table><caption>Люди из тех же публикаций</caption>
  <thead><tr><th scope="col">Человек</th><th scope="col">Общих публикаций</th></tr></thead>
  <tbody>{people}</tbody></table></details>"""
        if dossier.related
        else ""
    )
    if not nodes:
        return """<section class="band" id="links" aria-labelledby="links-title">
  <h2 id="links-title">Связи</h2>
  <p class="empty">Связей нет: ни статей УК, ни других людей в тех же публикациях.</p>
</section>"""
    return f"""<section class="band" id="links" aria-labelledby="links-title">
  <h2 id="links-title">Связи</h2>
  <p class="muted">Только подтверждённые данными связи: общие публикации, события и их статьи УК.
  Показано {len(nodes)} из {total}.</p>
  <div class="graph-wrap">{svg}<p class="legend">{legend}</p></div>
  <table class="links-table"><caption>Связи списком</caption>
  <thead><tr><th scope="col">Тип</th><th scope="col">Узел</th><th scope="col">Связь</th></tr></thead>
  <tbody>{rows}</tbody></table>
  {more}
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
            escape(_EVENT_LABELS.get(event, event)) for event in sorted(publication.events)
        )
        articles = ", ".join(
            _article_link(article) for article in sorted(publication.articles, key=_article_order)
        )
        url = external_url(publication.url)
        external = (
            f' · <a href="{escape(url, quote=True)}" rel="noopener noreferrer" '
            'target="_blank">источник</a>'
            if url
            else ""
        )
        cards.append(
            f"""<article class="evidence">
  <h3><a href="/ui/articles/{publication.article_id}?start={publication.text_start}&amp;end={publication.text_end}">{escape(publication.title)}</a></h3>
  <p class="muted">{escape(publication.source)} · {_day(publication.published_at)}{external}</p>
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
    body = f"""<p><a href="/ui/investigations">← Расследование</a></p>
<div class="page-toc" role="navigation" aria-label="Разделы досье">
  <a href="#decision-title">Решение</a> <a href="#charges">Статьи УК</a>
  <a href="#timeline">Хронология</a>
  <a href="#links">Связи</a> <a href="#evidence">Доказательства</a>
</div>
{_header(dossier)}
{_decision(dossier)}
{_charges(dossier)}
<div class="dossier-columns">
{_timeline(dossier)}
{_graph(dossier)}
</div>
{_evidence(dossier)}"""
    return _page(
        display_name(dossier.entity.name),
        body,
        active="investigations",
        instruction="Досье: кто это, что и когда произошло, на каких публикациях основан вывод.",
        next_action="Проверьте решение системы по цитатам; спорное — исправьте или отправьте в «Очередь».",
        db=db,
    )


@router.get("/ui/investigations", response_class=HTMLResponse)
def ui_investigations(
    q: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    query = select(EntityGroupRecord).outerjoin(
        EntityGroupPoliticsRecord, EntityGroupPoliticsRecord.group_id == EntityGroupRecord.id
    )
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                EntityGroupRecord.name.ilike(pattern),
                cast(EntityGroupRecord.variants, Text).ilike(pattern),
            )
        ).order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
        heading = f"Найдено по «{escape(q.strip())}»"
    else:
        query = query.where(EntityGroupPoliticsRecord.verdict == POLITICAL).order_by(
            EntityGroupRecord.last_published_at.desc().nulls_last(), EntityGroupRecord.key
        )
        heading = "Свежие политические дела"
    found = db.scalars(query.limit(50)).all()
    roles = {
        group_id: (role, kind)
        for group_id, role, kind in db.execute(
            select(
                EntityGroupRoleRecord.group_id,
                EntityGroupRoleRecord.role,
                EntityGroupRoleRecord.kind,
            ).where(EntityGroupRoleRecord.group_id.in_([entity.id for entity in found]))
        ).all()
    }
    rows = "".join(
        f'<tr><td><a href="/ui/investigations/{quote(entity.key)}">'
        f"{escape(display_name(entity.name))}</a></td>"
        f"<td>{escape(_role_label(*roles[entity.id])) if entity.id in roles else '—'}</td>"
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
        "Расследование",
        body,
        active="investigations",
        instruction="Найдите человека и откройте его досье.",
        next_action="Досье покажет решение системы, хронологию, связи и доказательства.",
        db=db,
    )


@router.get("/ui/entities/{key}", response_model=None)
def ui_entity_moved(key: str) -> RedirectResponse:
    """The entity card became the dossier; its old address still leads there."""
    return RedirectResponse(f"/ui/investigations/{quote(key)}", status_code=307)
