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
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path
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
from entities.evidence import person_evidence_cte
from entities.known_base import KnownBase, KnownMatch
from entities.news import KIND_LABELS as NEWS_LABELS
from entities.news import NEW_CASE, SENTENCE
from entities.officials import OFFICIAL_KINDS
from entities.politics import CRIMINAL, POLITICAL, VERDICT_LABELS
from entities.rf_check import FULL
from entities.roles import FIGURANT
from rosfinmonitoring.inclusion_dates import ATTRIBUTION as INCLUSION_ATTRIBUTION
from web.dependencies import get_db
from web.ui.entities import (
    _EVENT_LABELS,
    _NAME_SOURCES,
    _ROLE_METHODS,
    _events,
    _political,
    _regions,
    article_order,
    display_name,
    role_label,
)
from web.ui.layout import _page, copy_button, external_url
from web.ui.workload import dispute_pairs

router = APIRouter()

_STATIC = Path(__file__).resolve().parents[2] / "static"
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

TIMELINE_LIMIT = 200
PUBLICATION_LIMIT = 30
RELATED_LIMIT = 30
QUOTE_CONTEXT = 160

_VERDICT_BADGES = {POLITICAL: "succeeded", CRIMINAL: "", "unclear": "pending"}
_VERDICT_METHODS = {
    "article": "правило: статья УК из списка политических (или только обычной уголовщины)",
    "memorial": "правило: категория реестра «Мемориала»",
    "model": "модель по цитатам из публикаций",
    "manual": "решено оператором вручную",
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
           (WITH """
    + person_evidence_cte()
    + """
            SELECT min(published_at) FROM person_evidence WHERE group_id = g.id) AS first_published_at
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
    SELECT m.level, e.full_name, e.birth_date, e.birth_place, s.snapshot_date, e.inclusion_date
    FROM entity_group_rf_matches m
    JOIN rosfinmonitoring_entries e ON e.id = m.entry_id
    JOIN rosfinmonitoring_snapshots s ON s.id = e.snapshot_id
    WHERE m.group_id = :group
    UNION ALL
    SELECT NULL, NULL, NULL, NULL, max(snapshot_date), NULL FROM rosfinmonitoring_snapshots
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
    f"""
    WITH {person_evidence_cte()}
    SELECT * FROM (
        SELECT DISTINCT ON (article_id) article_id AS id, title, published_at, canonical_url,
               source, start_offset, end_offset, quote, quote_start, kind,
               resolution, rf_name, rf_birth_date, decided_at
        FROM person_evidence
        WHERE group_id = :group
        ORDER BY article_id, kind, start_offset
    ) found
    ORDER BY published_at DESC NULLS LAST, id DESC
    LIMIT :limit
    """
)
# Q7 — the other people of these publications.
_OTHERS = text(
    f"""
    WITH {person_evidence_cte()}
    SELECT DISTINCT e.article_id, g.key, g.name
    FROM person_evidence e
    JOIN entity_groups g ON g.id = e.group_id
    WHERE e.article_id = ANY(:articles) AND e.group_id <> :group
    """
)
# Q8 — the people most often in the same publications.
_RELATED = text(
    f"""
    WITH {person_evidence_cte()},
    mine AS (
        SELECT DISTINCT article_id FROM person_evidence
        WHERE group_id = :group
    )
    SELECT g.key, g.name, count(DISTINCT r.article_id) AS shared
    FROM person_evidence r
    JOIN entity_groups g ON g.id = r.group_id
    WHERE r.article_id IN (SELECT article_id FROM mine) AND r.group_id <> :group
    GROUP BY g.key, g.name ORDER BY shared DESC, g.name LIMIT :limit
    """
)
# Q9 — the publication a verdict's quote comes from (its text, spaces folded).
_QUOTE_SOURCE = text(
    f"""
    WITH {person_evidence_cte()}
    SELECT e.article_id
    FROM person_evidence e
    JOIN parsed_articles a ON a.id = e.article_id
    WHERE e.group_id = :group
      AND (
          (e.kind = 'mention' AND position(:needle IN regexp_replace(a.text, '\\s+', ' ', 'g')) > 0)
          OR (e.kind <> 'mention' AND position(:needle IN regexp_replace(e.quote, '\\s+', ' ', 'g')) > 0)
      )
    ORDER BY e.published_at DESC NULLS LAST LIMIT 1
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
    evidence_kind: str = "mention"
    resolution: str | None = None
    rf_name: str | None = None
    rf_birth_date: date | None = None
    decided_at: datetime | None = None
    events: set[str] = field(default_factory=set)
    articles: set[str] = field(default_factory=set)
    others: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Dossier:
    entity: Any
    rf: list[Any]
    snapshot_date: datetime | None
    timeline: list[TimelineItem]
    events_capped: bool
    charges: dict[str, dict[str, Any]]
    publications: list[Publication]
    related: list[tuple[str, str, int]]
    verdict_source: int | None
    disputes: list[str]
    # What the operator's base says of the person; `known_loaded` False: no base to ask.
    known: KnownMatch | None = None
    known_loaded: bool = False


def _marked(quote_text: str, start: int, end: int) -> str:
    """The excerpt with the mention (or the event's trigger) marked."""
    return (
        f"…{escape(quote_text[:start])}<mark>{escape(quote_text[start:end])}</mark>"
        f"{escape(quote_text[end:])}…"
    )


def load(db: Session, key: str) -> Dossier | None:
    entity = db.execute(_ENTITY, {"key": key, "context": QUOTE_CONTEXT}).first()
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
            row.kind,
            row.resolution,
            row.rf_name,
            row.rf_birth_date,
            row.decided_at,
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
            _OTHERS, {"articles": list(by_id), "group": entity.id, "context": QUOTE_CONTEXT}
        ).all():
            by_id[article_id].others.append((other_key, other_name))
    related = [
        (row.key, row.name, row.shared)
        for row in db.execute(
            _RELATED, {"group": entity.id, "limit": RELATED_LIMIT, "context": QUOTE_CONTEXT}
        ).all()
    ]

    verdict_source = None
    needle = " ".join((entity.verdict_quote or "").split())[:80]
    if needle:
        verdict_source = db.scalar(
            _QUOTE_SOURCE, {"group": entity.id, "needle": needle, "context": QUOTE_CONTEXT}
        )
    disputes = [
        pair.right.key if pair.left.key == key else pair.left.key
        for pair in dispute_pairs(db)
        if key in pair.keys
    ]
    base = KnownBase.from_session(db)
    return Dossier(
        known=base.match(entity.name),
        known_loaded=bool(len(base)),
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
        role = f"""<p>{_badge(role_label(entity.role, entity.kind))}
      <span class="muted">— {escape(_ROLE_METHODS.get(entity.role_method, entity.role_method))}</span></p>
    <p><b>Причина:</b> {escape(entity.role_reason)}</p>
    {f"<blockquote>{escape(entity.role_quote)}</blockquote>" if entity.role_quote else ""}"""
    else:
        role = '<p class="muted">Шаг 5 ещё не определял роль.</p>'
    rf_label, rf_badge = rf_status(dossier.rf)
    # The day of inclusion is said about the entry, and only where the entry was matched
    # with the patronymic: on a namesake the day belongs to somebody else.
    rf_items = "".join(
        f"<li>{_badge('ФИО с отчеством' if row.level == FULL else 'имя и фамилия', '' if row.level == FULL else 'pending')} "
        f"{escape(row.full_name)}{f', {row.birth_date:%d.%m.%Y} г.р.' if row.birth_date else ''}"
        f"{f', {escape(row.birth_place)}' if row.birth_place else ''}"
        f"{f' — запись перечня включена {row.inclusion_date:%d.%m.%Y}' if row.level == FULL and row.inclusion_date else ''}"
        "</li>"
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
    <div><h3>Роль в деле</h3>{role}{_news(entity)}{_known(dossier)}</div>
    <div><h3>Росфинмониторинг</h3>
      <p>{_badge(rf_label, rf_badge)}</p>
      {f"<ul>{rf_items}</ul>" if rf_items else ""}
      <p class="muted">Перечень подтверждает личность: дата рождения и место. Последний снимок
      перечня: {_day(dossier.snapshot_date)}. Даты рождения в новостях нет — тёзку отличает
      только отчество. Дата включения — свойство записи перечня, а не человека: она не
      делает совпадение подтверждением личности. {escape(INCLUSION_ATTRIBUTION)}</p>
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
            f'<time>{_day(item.day)}</time><span class="muted">по дате публикации</span>'
            if item.dated
            else '<time>дата не установлена</time><span class="muted">в тексте другой год; '
            f"публикация {_day(item.day)}</span>"
        )
        articles = ", ".join(
            _article_link(article) for article in sorted(item.articles, key=article_order)
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
  <p class="muted">Человек связан с событием, в котором он назван; событие — со своей
  публикацией, судом, органом и статьёй. Двое связаны только через событие, где названы оба.
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
            escape(_EVENT_LABELS.get(event, event)) for event in sorted(publication.events)
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
        identification = ""
        if publication.evidence_kind == "unnamed_resolution":
            source = (
                f"запись РФМ {publication.rf_name}, {publication.rf_birth_date:%d.%m.%Y}"
                if publication.resolution == "rf_entry"
                and publication.rf_name
                and publication.rf_birth_date
                else "существующий человек"
                if publication.resolution == "existing_person"
                else "имя указано вручную"
                if publication.resolution == "supplied_name"
                else "ручное решение"
            )
            identification = (
                f'<p class="muted">Опознан оператором: {escape(source)}'
                f"{f', {_day(publication.decided_at)}' if publication.decided_at else ''}. "
                "Цитата оставлена как в публикации.</p>"
            )
        cards.append(
            f"""<article class="evidence">
  <h3><a href="/ui/articles/{publication.article_id}?start={publication.text_start}&amp;end={publication.text_end}">{escape(publication.title)}</a></h3>
  <p class="muted">{escape(publication.source)} · {_day(publication.published_at)}{external}</p>
  {identification}
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
        "Расследование",
        body,
        active="investigations",
        instruction="Найдите человека и откройте его досье.",
        db=db,
    )


@router.get("/ui/entities/{key}", response_model=None)
def ui_entity_moved(key: str) -> RedirectResponse:
    """The entity card became the dossier; its old address still leads there."""
    return RedirectResponse(f"/ui/investigations/{quote(key)}", status_code=307)
