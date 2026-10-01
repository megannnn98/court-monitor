"""«Результат»: the politically persecuted — what the whole pipeline is for.

A figurant of a criminal case (step 4) whose case is political persecution (step 5), on
the Rosfinmonitoring list or not: the list does not make a case known, it confirms who a
person is (the operator's word) — its birth date and place are shown beside the name. A
name the list carries without a patronymic may be a namesake: marked, and can be hidden.
The period filter answers «new or old case» by the date of the latest publication; the
same rows go to Excel.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from html import escape
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from openpyxl import Workbook
from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session

from db.orm_models import (
    EntityDoneMarkRecord,
    EntityGroupNewsRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
)
from entities.done_marks import case_done, done_keys, is_done, unnamed_marks
from entities.evidence import person_evidence_cte
from entities.known_base import LEVEL_LABELS, KnownBase, KnownMatch
from entities.news import CLOSED, KIND_LABELS, NEW_CASE, ONGOING, OTHER, SENTENCE, UNKNOWN
from entities.politics import MEMORIAL_CATEGORIES, POLITICAL
from entities.rf_check import FULL
from entities.rf_entry import (
    INCLUSION_NOTES,
    entry_included_text,
    rf_word,
    strongest_entries,
)
from entities.unnamed_cases import KEY_PREFIX, Case, political_cases
from persecution.classifier import POLITICAL_ARTICLES
from rosfinmonitoring.inclusion_dates import ATTRIBUTION as INCLUSION_ATTRIBUTION
from web.dependencies import get_db
from web.exports import (
    XLSX,
    excel_day,
    is_web_link,
    workbook_bytes,
    write_notes,
    write_sheet,
)
from web.ui.entities import _articles_by_group, _date, display_name, regions_text
from web.ui.funnel import funnel, funnel_line
from web.ui.layout import _page, copy_button, pager

router = APIRouter()

PAGE_SIZE = 100
LINKS = 3
# Roundup posts («Главное за день», «Еженедельный дайджест») name many people in one
# breath and say nothing about any one of them: an operator scanning «Публикации» for
# what a person is accused of should see a substantive source first, not a digest.
_DIGEST_MARKERS = (
    "главное за",
    "главные новости",
    "дайджест",
    "итоги дня",
    "итоги недели",
)
PERIODS = {0: "За всё время", 1: "Месяц", 3: "3 месяца", 6: "Полгода", 12: "Год"}
# The last filters, so that a reload or the menu's link keeps the period.
FILTERS_COOKIE = "political_filters"
FILTER_NAMES = ("months", "date_from", "date_to", "news", "known", "done", "who")
# Named people, or the figurants a publication does not name (`entities.unnamed_cases`).
WHO_FILTERS = {"all": "Все", "named": "С именем", "unnamed": "Без имени"}
# An unnamed figurant has no «свежая новость» of the model's: the event of the latest
# sentence about them stands for it.
_EVENT_NEWS = {
    "case_opened": NEW_CASE,
    "detention": NEW_CASE,
    "charge": NEW_CASE,
    "search": NEW_CASE,
    "arrest": ONGOING,
    "sentence": SENTENCE,
    "other": OTHER,
}
# The people the operator marked «обработано»: hidden unless she asks to see them.
DONE_FILTERS = ("hide", "show")
# What the latest news is (`entities.news`): the operator's new cases and sentences first.
NEWS_FILTERS = {
    "all": "Любая свежая новость",
    NEW_CASE: "Новые дела",
    SENTENCE: "Приговоры",
    ONGOING: "Продолжение дела",
    CLOSED: "Дело завершено",
    OTHER: "Другое",
    UNKNOWN: "Не определено",
}

# Whether the operator's own base («Найденные люди») already holds the person, by name
# (`entities.known_base`): the new people are the ones it does not.
KNOWN_FILTERS = {
    "all": "Любые",
    "none": "Нет в базе",
    "in_base": LEVEL_LABELS["in_base"].capitalize(),
    "probably": LEVEL_LABELS["probably"].capitalize(),
    "namesakes": LEVEL_LABELS["namesakes"].capitalize(),
}

_PUBLICATIONS = text(
    f"""
    WITH {person_evidence_cte()}
    SELECT DISTINCT pe.group_id, pe.article_id AS id, pe.title, pe.published_at,
           pe.canonical_url, pe.source, d.relevant
    FROM person_evidence pe
    LEFT JOIN article_digest_answers d ON d.article_id = pe.article_id
    WHERE pe.group_id = ANY(:groups)
    """
)


@dataclass(frozen=True)
class UnnamedPerson:
    """An unnamed figurant in the place of an entity: what a row of the list reads."""

    id: int
    key: str
    name: str
    regions: list[Any]
    last_published_at: datetime | None
    case: Case


@dataclass(frozen=True)
class UnnamedBasis:
    """Why an unnamed figurant is on the list: the political article the text names."""

    reason: str
    quote: str
    method: str = "article"


Person = EntityGroupRecord | UnnamedPerson
Found = tuple[Person, EntityGroupPoliticsRecord | UnnamedBasis]


@dataclass
class ListRow:
    entity: Person
    politics: EntityGroupPoliticsRecord | UnnamedBasis
    # On the list by the name alone (no patronymic on one side): maybe a namesake.
    maybe_listed: bool
    articles: list[tuple[str, bool]] = field(default_factory=list)
    # The list's entry: «full» (the name with the patronymic) or «name», and who it is.
    rf_level: str | None = None
    rf_entry: str = ""
    # The operator's «обработано», still standing (no news since).
    done: bool = False
    # When this entry of the перечень appeared, if the ОВД-Инфо copy says. It describes
    # the entry, not the person; see `entry_included_text`.
    rf_inclusion_date: datetime | None = None
    # What the latest news is, and why the model said so.
    news_kind: str | None = None
    news_reason: str = ""
    # What the operator's base says of this person; None: nobody by this name there.
    known: KnownMatch | None = None
    memorial: str | None = None
    first_published: datetime | None = None
    # (title, url, published, source) of the latest publications.
    links: list[tuple[str, str, datetime | None, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Filters:
    """The list's filters: a period of the latest news — the last months, or dates — and
    what the latest news is."""

    months: int = 0
    date_from: date | None = None
    date_to: date | None = None
    news: str = "all"
    known: str = "all"
    done: str = "hide"
    who: str = "all"

    @property
    def custom(self) -> bool:
        return self.date_from is not None or self.date_to is not None

    def query(self) -> dict[str, str]:
        """As URL parameters, for the pager and the Excel link."""
        return {
            "months": str(self.months),
            "date_from": self.date_from.isoformat() if self.date_from else "",
            "date_to": self.date_to.isoformat() if self.date_to else "",
            "news": self.news,
            "known": self.known,
            "done": self.done,
            "who": self.who,
        }


def _parse_date(text: str) -> date | None:
    """«25/09/2026» as the form writes it (day/month/year), «25.09.2026», or the
    ISO «2026-09-25» of the links and the cookie."""
    value = text.strip()
    if not value:
        return None
    for layout in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, layout).date()  # noqa: DTZ007 - a date, no time
        except ValueError:
            continue
    return None


_CALENDAR_ICON = (
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>'
)


def _form_date(value: date | None) -> str:
    return f"{value:%d/%m/%Y}" if value else ""


def _date_field(name: str, label: str, value: date | None) -> str:
    """A day/month/year field, whatever the browser's language: a native date field
    shows the browser's own order (month first in an English one)."""
    return (
        f'<label class="dates">{label} <span class="date-pick"><input type="text" name="{name}" '
        f'value="{_form_date(value)}" placeholder="дд/мм/гггг" inputmode="numeric" '
        'pattern="\\d{1,2}/\\d{1,2}/\\d{4}" title="день/месяц/год" size="10" '
        "data-date>"
        # The browser's calendar, unnamed and unseen: it only fills the field above.
        '<button type="button" class="secondary date-open" title="Выбрать в календаре" '
        f'aria-label="Календарь">{_CALENDAR_ICON}</button>'
        '<input type="date" class="date-native" tabindex="-1" aria-hidden="true" '
        f'value="{value.isoformat() if value else ""}"></span></label>'
    )


def filters(
    months: int,
    date_from: str,
    date_to: str,
    news: str = "all",
    known: str = "all",
    done: str = "hide",
    who: str = "all",
) -> Filters:
    """Dates, when given, win over the months."""
    start, end = _parse_date(date_from), _parse_date(date_to)
    return Filters(
        months=0 if start or end or months not in PERIODS else months,
        date_from=start,
        date_to=end,
        news=news if news in NEWS_FILTERS else "all",
        known=known if known in KNOWN_FILTERS else "all",
        done=done if done in DONE_FILTERS else "hide",
        who=who if who in WHO_FILTERS else "all",
    )


def remembered(cookie: str) -> Filters | None:
    """The filters the cookie keeps; None when it keeps nothing usable."""
    # The page's script writes it encoded; the server, plain.
    values = {name: items[-1] for name, items in parse_qs(unquote(cookie)).items()}
    if not values:
        return None
    try:
        months = int(values.get("months") or 0)
    except ValueError:
        months = 0
    return filters(
        months,
        values.get("date_from", "")[:10],
        values.get("date_to", "")[:10],
        values.get("news", "all"),
        values.get("known", "all"),
        values.get("done", "hide"),
        values.get("who", "all"),
    )


def _answer(match: KnownMatch | None) -> str:
    return match.level if match else "none"


@dataclass(frozen=True)
class KnownCheck:
    """The operator's base against the people on the list."""

    matches: Mapping[int, KnownMatch | None]
    # The people per answer («none» for nobody by the name), before the choice of one.
    counts: Mapping[str, int]
    # How many people the base holds; none loaded, the answer «not there» means nothing.
    size: int


def _by_known_base(
    db: Session,
    found: list[Found],
    chosen: Filters,
) -> tuple[list[Found], KnownCheck]:
    """The people the base answers for, and who is left after the choice of an answer.

    With the base not loaded (a sync has never run) nobody is «not in the base» — the
    base is not there — so the choice is ignored and the page says so.
    """
    base = KnownBase.from_session(db)
    # A person without a name cannot be looked up by one: the base is not asked.
    matches = {
        entity.id: base.match(entity.name)
        for entity, _ in found
        if isinstance(entity, EntityGroupRecord)
    }
    counts = Counter(_answer(match) for match in matches.values())
    check = KnownCheck(matches, dict(counts), len(base))
    if chosen.known == "all" or not len(base):
        return found, check
    return [
        (entity, politics)
        for entity, politics in found
        if entity.id in matches and _answer(matches[entity.id]) == chosen.known
    ], check


def _unnamed_rows(db: Session, chosen: Filters) -> tuple[list[Found], dict[str, int]]:
    """The unnamed figurants under the same filters as the named, and how many of each
    latest news there are in the period (before the choice of the news)."""
    marks = unnamed_marks(db)
    since = datetime.now(UTC) - timedelta(days=30 * chosen.months) if chosen.months else None
    start = datetime.combine(chosen.date_from, time.min, UTC) if chosen.date_from else None
    end = (
        datetime.combine(chosen.date_to + timedelta(days=1), time.min, UTC)
        if chosen.date_to
        else None
    )
    found: list[Found] = []
    counts: Counter[str] = Counter()
    for number, case in enumerate(political_cases(db), start=1):
        latest = case.last_published_at
        if chosen.done == "hide" and case_done(case, marks):
            continue
        if (since or start or end) and latest is None:
            continue
        if latest is not None and (
            (since and latest < since) or (start and latest < start) or (end and latest >= end)
        ):
            continue
        kind = _EVENT_NEWS.get(case.latest.event_type, OTHER)
        counts[kind] += 1
        if chosen.news not in ("all", kind):
            continue
        person = UnnamedPerson(
            # Below every entity's id, so the two never meet in a lookup by id.
            id=-number,
            key=case.key,
            name=case.name,
            regions=[[case.place, len(case.sentences)]] if case.place else [],
            last_published_at=latest,
            case=case,
        )
        found.append((person, UnnamedBasis(case.latest.explanation, case.latest.quote)))
    return found, dict(counts)


def _all_rows(db: Session, chosen: Filters) -> tuple[list[Found], dict[str, int]]:
    """The named and the unnamed in one list, latest news first."""
    named, _, news_counts = _rows(db, chosen) if chosen.who != "unnamed" else ([], 0, {})
    unnamed, unnamed_counts = _unnamed_rows(db, chosen) if chosen.who != "named" else ([], {})
    counts = Counter(news_counts) + Counter(unnamed_counts)
    oldest = datetime.min.replace(tzinfo=UTC)
    # Stable: the named keep their order, and an unnamed row of the same moment follows.
    merged = sorted(
        [*named, *unnamed], key=lambda item: item[0].last_published_at or oldest, reverse=True
    )
    return merged, dict(counts)


def _rows(db: Session, chosen: Filters) -> tuple[list[Found], int, dict[str, int]]:
    """The list's entities, latest news first; how many there are, and how many of each
    latest news (in the period, before the choice of the news)."""
    query = (
        select(EntityGroupRecord, EntityGroupPoliticsRecord)
        .join(EntityGroupPoliticsRecord, EntityGroupPoliticsRecord.group_id == EntityGroupRecord.id)
        .where(EntityGroupPoliticsRecord.verdict == POLITICAL)
    )
    if chosen.done == "hide":
        query = query.where(~is_done())
    if chosen.months:
        since = datetime.now(UTC) - timedelta(days=30 * chosen.months)
        query = query.where(EntityGroupRecord.last_published_at >= since)
    if chosen.date_from is not None:
        start = datetime.combine(chosen.date_from, time.min, UTC)
        query = query.where(EntityGroupRecord.last_published_at >= start)
    if chosen.date_to is not None:
        # The whole last day.
        end = datetime.combine(chosen.date_to + timedelta(days=1), time.min, UTC)
        query = query.where(EntityGroupRecord.last_published_at < end)
    in_period = query.subquery()
    news_counts = {
        str(kind): count
        for kind, count in db.execute(
            select(EntityGroupNewsRecord.kind, func.count())
            .join(in_period, in_period.c.id == EntityGroupNewsRecord.group_id)
            .group_by(EntityGroupNewsRecord.kind)
        ).all()
    }
    if chosen.news != "all":
        query = query.where(
            exists().where(
                EntityGroupNewsRecord.group_id == EntityGroupRecord.id,
                EntityGroupNewsRecord.kind == chosen.news,
            )
        )
    rows = db.execute(
        query.order_by(
            EntityGroupRecord.last_published_at.desc().nulls_last(), EntityGroupRecord.key
        )
    ).all()
    return [(entity, politics) for entity, politics in rows], len(rows), news_counts


def _details(
    db: Session,
    found: list[Found],
    known: Mapping[int, KnownMatch | None] | None = None,
) -> list[ListRow]:
    ids = [entity.id for entity, _ in found if isinstance(entity, EntityGroupRecord)]
    done = done_keys(db, [entity.key for entity, _ in found])
    marks = unnamed_marks(db)
    rows = {
        entity.id: ListRow(
            entity=entity,
            politics=politics,
            maybe_listed=False,
            known=known.get(entity.id) if known else None,
            done=entity.key in done
            or (isinstance(entity, UnnamedPerson) and case_done(entity.case, marks)),
        )
        for entity, politics in found
    }
    for row in rows.values():
        if isinstance(row.entity, UnnamedPerson):
            _fill_unnamed(row, row.entity.case)
    for group_id, kind, reason in db.execute(
        select(
            EntityGroupNewsRecord.group_id, EntityGroupNewsRecord.kind, EntityGroupNewsRecord.reason
        ).where(EntityGroupNewsRecord.group_id.in_(ids))
    ).all():
        rows[group_id].news_kind, rows[group_id].news_reason = kind, reason
    for group_id, entry in strongest_entries(db, ids).items():
        row = rows[group_id]
        row.rf_level, row.rf_entry, row.rf_inclusion_date = (
            entry.level,
            entry.text,
            entry.inclusion_date,
        )
    for row in rows.values():
        row.maybe_listed = row.rf_level is not None and row.rf_level != FULL
    for group_id, articles in _articles_by_group(db, ids).items():
        rows[group_id].articles = articles
    for group_id, category in db.execute(MEMORIAL_CATEGORIES, {"groups": ids}).all():
        rows[group_id].memorial = category
    # (title, url, published, source, relevant): relevant is None for an article the
    # model has not classified yet — falls back to the title keywords below (they only
    # catch the digest case, not «иноагент»/pickets, but that is still no worse than
    # showing everything unfiltered).
    publications: dict[int, list[tuple[str, str, datetime | None, str, bool | None]]] = {}
    for group_id, _id, title, published_at, url, source, relevant in db.execute(
        _PUBLICATIONS, {"groups": ids, "context": 0}
    ).all():
        publications.setdefault(group_id, []).append((title, url, published_at, source, relevant))
    oldest = datetime.min.replace(tzinfo=UTC)
    for group_id, found_publications in publications.items():
        dates = [item[2] for item in found_publications if item[2] is not None]
        rows[group_id].first_published = min(dates) if dates else None
        links = [item[:4] for item in found_publications]
        substantive = [
            item[:4]
            for item in found_publications
            if (item[4] if item[4] is not None else not _is_digest(item[0]))
        ]
        rows[group_id].links = sorted(
            substantive or links, key=lambda item: item[2] or oldest, reverse=True
        )[:LINKS]
    return [rows[entity.id] for entity, _ in found]


def _fill_unnamed(row: ListRow, case: Case) -> None:
    """What the sentences tell, in the columns the named people have."""
    row.articles = [(article, True) for article in case.articles]
    row.news_kind = _EVENT_NEWS.get(case.latest.event_type, OTHER)
    row.news_reason = case.latest.explanation
    row.first_published = case.first_published_at
    oldest = datetime.min.replace(tzinfo=UTC)
    seen: set[int] = set()
    links = []
    for sentence in sorted(
        case.sentences, key=lambda sentence: sentence.published_at or oldest, reverse=True
    ):
        if sentence.article_id not in seen:
            seen.add(sentence.article_id)
            links.append((sentence.title, sentence.url, sentence.published_at, sentence.source))
    row.links = links[:LINKS]


def _shown_name(row: ListRow) -> str:
    """The name of a row: surname first for a person, as told for an unnamed figurant."""
    if isinstance(row.entity, UnnamedPerson):
        return row.entity.name
    return display_name(row.entity.name)


def _is_digest(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in _DIGEST_MARKERS)


def _article_text(articles: list[tuple[str, bool]]) -> str:
    return ", ".join(article for article, _ in articles)


# Who said so. A person who decides a verdict by hand is not the model, and telling the
# operator their own decision came from the model costs them the reason to trust it.
_BASIS_LABELS = {
    "article": "статья УК",
    "manual": "решено вручную",
    "memorial": "правило: реестр «Мемориала»",
    "model": "модель",
}


# What `decide_politics` writes as the reason. The label already says this, so printing
# it after a colon reads as «решено вручную: решено оператором вручную».
_MANUAL_REASON = "решено оператором вручную"


def _basis(row: ListRow) -> str:
    """Who decided the verdict, and why — the two never say the same thing twice."""
    label = _BASIS_LABELS.get(row.politics.method, "модель")
    reason = row.politics.reason
    if reason in ("", _MANUAL_REASON):
        return label
    return f"{label}: {reason}"


def _news_mark(row: ListRow) -> str:
    """The latest news as a mark; the new cases and the sentences stand out."""
    if row.news_kind is None:
        return '<span class="muted">—</span>'
    css = {NEW_CASE: "succeeded", SENTENCE: "succeeded", UNKNOWN: "pending"}.get(row.news_kind, "")
    return (
        f'<span class="badge {css}" title="{escape(row.news_reason, quote=True)}">'
        f"{escape(KIND_LABELS.get(row.news_kind, row.news_kind))}</span>"
    )


def _known_mark(row: ListRow, *, loaded: bool) -> str:
    """What the operator's base says of the person; the names it holds in the tooltip."""
    if not loaded:
        return '<span class="muted">—</span>'
    match = row.known
    if match is None:
        return '<span class="badge succeeded">нет в базе</span>'
    css = {"in_base": "", "probably": "", "namesakes": "pending"}[match.level]
    names = "; ".join(match.names)
    return (
        f'<span class="badge {css}" title="{escape(names, quote=True)}">'
        f"{escape(match.label)}</span>"
        f'<br><span class="muted">{escape(names if match.level != "namesakes" else "")}</span>'
    )


def _done_box(row: ListRow, back: str) -> str:
    """The tick at the start of a row: «обработано». Ticking sends the form at once."""
    return (
        '<form method="post" action="/ui/political/done" class="inline-form">'
        f'<input type="hidden" name="key" value="{escape(row.entity.key, quote=True)}">'
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        f'<input type="hidden" name="done" value="{0 if row.done else 1}">'
        '<input type="checkbox" title="Обработано" aria-label="Обработано" '
        # requestSubmit, not submit: it fires the event the scroll keeper listens for.
        f'onchange="this.form.requestSubmit()"{" checked" if row.done else ""}></form>'
    )


def _html_row(position: int, row: ListRow, *, known_loaded: bool = False, back: str = "") -> str:
    entity = row.entity
    articles = ", ".join(
        f"<b>{escape(article)}</b>" if article in POLITICAL_ARTICLES else escape(article)
        for article, _ in row.articles
    )
    links = "".join(
        f'<div class="pub-link"><span class="muted">{escape(source)}:</span> '
        f'<a href="{escape(url, quote=True)}">{escape(title[:80])}</a></div>'
        for title, url, _, source in row.links
        if is_web_link(url)
    )
    listed = (
        ' <span class="badge">в перечне РФМ</span>'
        if row.rf_level == FULL
        else ' <span class="badge pending">возможно в перечне</span>'
        if row.maybe_listed
        else ""
    )
    # Shown only for an entry matched with the patronymic: on a namesake the day belongs
    # to somebody else, so it is not shown at all.
    included = (
        f'<div class="muted">{escape(entry_included_text(row.rf_inclusion_date))}</div>'
        if row.rf_level == FULL and row.rf_inclusion_date
        else ""
    )
    if isinstance(entity, UnnamedPerson):
        # No dossier: the sentences stand on «Безымянные», where the person is identified.
        anchor = quote(entity.case.latest.key)
        name_cell = (
            f'<a href="/ui/unnamed?status=all#u-{anchor}">{escape(entity.name)}</a> '
            '<span class="badge pending" title="Публикация не называет имени">без имени</span> '
            f"{copy_button(entity.name)}"
        )
    else:
        name_cell = (
            f'<a href="/ui/investigations/{quote(entity.key)}">'
            f"{escape(display_name(entity.name))}</a>{listed} "
            f"{copy_button(display_name(entity.name))}{included}"
        )
    return (
        f"<tr{' class="done"' if row.done else ''}><td>{_done_box(row, back)}</td>"
        f"<td>{position}</td>"
        f"<td>{name_cell}</td>"
        f"<td>{_news_mark(row)}</td>"
        f"<td>{_known_mark(row, loaded=known_loaded)}</td>"
        f"<td>{escape(regions_text(entity.regions))}</td>"
        f'<td class="muted">{escape(row.rf_entry)}</td>'
        f"<td>{articles}</td>"
        f'<td>{escape(_basis(row))}<br><span class="muted">{escape(row.politics.quote[:200])}</span></td>'
        f"<td>{escape(row.memorial or '')}</td>"
        f"<td>{escape(_date(row.first_published))}</td>"
        f"<td>{escape(_date(entity.last_published_at))}</td>"
        f"<td>{links}</td></tr>"
    )


@router.get("/ui/political", response_class=HTMLResponse)
def ui_political(
    request: Request,
    months: int = Query(default=0),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    news: str = Query(default="all", max_length=16),
    known: str = Query(default="all", max_length=16),
    done: str = Query(default="hide", max_length=8),
    who: str = Query(default="all", max_length=8),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    chosen = filters(months, date_from, date_to, news, known, done, who)
    # No filters in the address: the last ones chosen, not «all the time».
    if not any(name in request.query_params for name in FILTER_NAMES):
        chosen = remembered(request.cookies.get(FILTERS_COOKIE, "")) or chosen
    found, news_counts = _all_rows(db, chosen)
    found, check = _by_known_base(db, found, chosen)
    total = len(found)
    on_page = _details(db, found[(page - 1) * PAGE_SIZE : page * PAGE_SIZE], check.matches)
    keep = chosen.query()
    back = urlencode({**keep, "page": page})
    rows = "".join(
        _html_row(position, row, known_loaded=bool(check.size), back=back)
        for position, row in enumerate(on_page, start=(page - 1) * PAGE_SIZE + 1)
    )
    done_total = (
        db.scalar(
            select(func.count())
            .select_from(EntityGroupRecord)
            .join(
                EntityGroupPoliticsRecord,
                EntityGroupPoliticsRecord.group_id == EntityGroupRecord.id,
            )
            .where(EntityGroupPoliticsRecord.verdict == POLITICAL, is_done())
        )
        or 0
    )
    marks = unnamed_marks(db)
    done_total += sum(case_done(case, marks) for case in political_cases(db))
    who_options = "".join(
        f'<option value="{key}"{" selected" if key == chosen.who else ""}>{label}</option>'
        for key, label in WHO_FILTERS.items()
    )
    # Beside the filters and always there, so the way back to a ticked person is in plain
    # sight: the first person to tick a row could not find them again.
    done_toggle = (
        # `check`, the tick-box label: `field` is the select's, and sizes the box as one.
        '<label class="check"><input type="checkbox" name="done" value="show" '
        f'onchange="this.form.submit()"{" checked" if chosen.done == "show" else ""}> '
        f"Показать обработанных ({done_total})</label>"
    )
    # A period button clears the dates: the months are the choice then.
    periods = " ".join(
        f'<button type="submit" name="months" value="{key}" '
        f'class="chip{" active" if key == chosen.months and not chosen.custom else ""}" '
        "onclick=\"this.form.date_from.value='';this.form.date_to.value=''\">"
        f"{label}</button>"
        for key, label in PERIODS.items()
    )
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    pages_html = pager("/ui/political", keep, page, pages)
    news_options = "".join(
        f'<option value="{key}"{" selected" if key == chosen.news else ""}>{escape(label)}'
        f"{f' ({news_counts.get(key, 0)})' if key != 'all' else ''}</option>"
        for key, label in NEWS_FILTERS.items()
        if key in ("all", NEW_CASE, SENTENCE, ONGOING) or news_counts.get(key)
    )
    known_options = "".join(
        f'<option value="{key}"{" selected" if key == chosen.known else ""}>{escape(label)}'
        f"{f' ({check.counts.get(key, 0)})' if key != 'all' else ''}</option>"
        for key, label in KNOWN_FILTERS.items()
    )
    known_select = (
        '<label class="field">В базе Airtable <select name="known" '
        f'onchange="this.form.submit()">{known_options}</select></label>'
        if check.size
        else ""
    )
    dates = (
        _date_field("date_from", "с", chosen.date_from)
        + _date_field("date_to", "по", chosen.date_to)
        + '<button type="submit">Показать</button>'
    )
    # The chosen months ride along hidden; a period button, sent later, wins over them.
    body = f"""<form method="get" action="/ui/political" class="toolbar" id="political-filters">
  <input type="hidden" name="months" value="{chosen.months}">
  <span class="chips">{periods}</span>
  <span class="chips">{dates}</span>
  <div class="filter-row">
    <!-- The form's own values, not the page's: dates picked but not shown yet count. -->
    <label class="field">Свежая новость <select name="news" onchange="this.form.submit()">{
        news_options
    }</select></label>
    {known_select}
    <label class="field">Кто <select name="who" onchange="this.form.submit()">{
        who_options
    }</select></label>
    {done_toggle}
    <button type="submit" class="secondary" formaction="/ui/political/export.xlsx">Скачать Excel</button>
  </div>
</form>
{funnel_line(funnel(db))}
<p class="muted">Найдено: {total}. Галочка в начале строки — «обработано»: человек
уходит из списка и вернётся, когда о нём появится новая новость; «Показать обработанных»
над таблицей возвращает их в список, чтобы снять галочку. «Без имени» — фигуранты, которых
публикация не называет: в списке только те, чьё дело идёт по политической статье; несколько
упоминаний одного человека сведены в одну строку по возрасту, полу и месту. Фигуранты уголовных дел, дело которых — политическое
преследование. Перечень Росфинмониторинга подтверждает личность: дата рождения и место из
него — рядом с именем; «возможно в перечне» — совпали только имя и фамилия, может быть тёзка.
Дата включения — свойство записи перечня, а не человека: под именем она читается как
«запись перечня включена …» и не делает совпадение подтверждением личности.
{escape(INCLUSION_ATTRIBUTION)}.
Жирная статья — из списка политических; «Последняя новость» показывает, свежий ли случай.
«В базе Airtable» — есть ли человек в вашей таблице «Найденные люди»; сверка только по имени
(даты рождения и региона там нет), поэтому «вероятно» и «тёзки» — не уверенность.</p>
<table><thead><tr><th title="Обработано">✓</th><th>№</th><th>Фамилия Имя</th><th>Свежая новость</th><th>В базе Airtable</th><th>Регион</th><th>Перечень РФМ</th><th>Статьи УК</th>
<th>Почему политическое</th><th>Мемориал</th><th>Первая новость</th><th>Последняя новость</th>
<th>Публикации</th></tr></thead><tbody>{rows}</tbody></table>
{pages_html}
<script>
// The calendar: opened by its button, the day it gives written day/month/year.
document.querySelectorAll("#political-filters .date-pick").forEach((pick) => {{
  const field = pick.querySelector("[data-date]");
  const native = pick.querySelector(".date-native");
  pick.querySelector(".date-open").addEventListener("click", () => {{
    const [d, m, y] = field.value.split("/");
    native.value = y && m && d ? `${{y}}-${{m.padStart(2, "0")}}-${{d.padStart(2, "0")}}` : "";
    try {{ native.showPicker(); }} catch {{ native.focus(); }}
  }});
  native.addEventListener("change", () => {{
    if (!native.value) return;
    const [y, m, d] = native.value.split("-");
    field.value = `${{d}}/${{m}}/${{y}}`;
    field.dispatchEvent(new Event("change", {{ bubbles: true }}));
  }});
}});
// Dates picked but not shown yet are kept too: a reload shows them.
document.getElementById("political-filters").addEventListener("change", (event) => {{
  if (!("date" in event.target.dataset)) return;
  const form = new URLSearchParams(new FormData(event.target.form));
  document.cookie = "{FILTERS_COOKIE}=" + encodeURIComponent(form.toString()) +
    "; path=/ui/political; max-age=31536000; samesite=lax";
}});
</script>"""
    response = _page(
        "Результат",
        body,
        active="political",
        instruction=(
            "Люди, против которых заведены политические уголовные дела; перечень "
            "Росфинмониторинга подтверждает их личность."
        ),
        db=db,
    )
    response.set_cookie(
        FILTERS_COOKIE,
        urlencode(chosen.query()),
        max_age=365 * 24 * 3600,
        path="/ui/political",
        samesite="lax",
    )
    return response


def known_answer_text(match: KnownMatch | None, *, loaded: bool) -> str | None:
    """The answer of the operator's base as a cell: what, and which records."""
    if not loaded:
        return None
    if match is None:
        return "нет в базе"
    names = "; ".join(match.names)
    return f"{match.label} ({names})" if match.level == "namesakes" else f"{match.label}: {names}"


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
                _shown_name(row),
                regions_text(row.entity.regions) or None,
                _article_text(row.articles) or None,
                _basis(row),
                row.memorial,
                excel_day(row.first_published),
                excel_day(row.entity.last_published_at),
                rf_word(row.rf_level, row.rf_entry, row.rf_inclusion_date) or None,
                *(f"{source}: {title[:80]}" for title, _, source in found),
                *(None for _ in range(LINKS - len(found))),
                KIND_LABELS.get(row.news_kind, row.news_kind) if row.news_kind else None,
                _known_text(row, loaded=known_loaded),
            ]
            for position, (row, found) in enumerate(zip(rows, sources, strict=True), start=1)
        ],
    )
    for line, found in enumerate(sources, start=2):
        for column, (_, url, _) in enumerate(found, start=first_source):
            sheet.cell(row=line, column=column).hyperlink = url
    write_notes(workbook, "Источник дат", INCLUSION_NOTES)
    return workbook_bytes(workbook)


def export_name(chosen: Filters, found: list[Found], today: date) -> str:
    """`result_<from>_<to>.xlsx`: the dates chosen, else the period's start, else the
    earliest latest news of the rows; up to the date chosen, else today."""
    start = chosen.date_from
    if start is None and chosen.months:
        start = today - timedelta(days=30 * chosen.months)
    if start is None:
        dates = [row.last_published_at for row, _ in found if row.last_published_at is not None]
        start = min(dates).date() if dates else today
    end = chosen.date_to or today
    return f"result_{start.isoformat()}_{end.isoformat()}.xlsx"


@router.get("/ui/political/export.xlsx")
def ui_political_export(
    months: int = Query(default=0),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    news: str = Query(default="all", max_length=16),
    known: str = Query(default="all", max_length=16),
    done: str = Query(default="hide", max_length=8),
    who: str = Query(default="all", max_length=8),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """Every row of the page's filters, not only one page."""
    chosen = filters(months, date_from, date_to, news, known, done, who)
    found, _ = _all_rows(db, chosen)
    found, check = _by_known_base(db, found, chosen)
    name = export_name(chosen, found, datetime.now(UTC).date())
    return Response(
        political_xlsx(_details(db, found, check.matches), known_loaded=bool(check.size)),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/ui/political/done")
async def ui_political_done(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Tick or untick «обработано» on a person, and come back to the same list."""
    form = {
        name: values[0]
        for name, values in parse_qs((await request.body()).decode("utf-8", "replace")).items()
    }
    key = form.get("key", "")
    entity: EntityGroupRecord | Case | None
    if key.startswith(KEY_PREFIX):
        entity = next((case for case in political_cases(db) if case.key == key), None)
    else:
        entity = db.scalar(select(EntityGroupRecord).where(EntityGroupRecord.key == key))
    if entity is None:
        raise HTTPException(status_code=404, detail="Человек не найден")
    mark = db.get(EntityDoneMarkRecord, key)
    if form.get("done") == "1":
        if mark is None:
            mark = EntityDoneMarkRecord(key=key)
            db.add(mark)
        # The news the operator has seen: a later one brings the person back.
        mark.news_at = entity.last_published_at
    elif mark is not None:
        db.delete(mark)
    db.commit()
    # Only the list's own address: the form's `back` is its query, never a place to go.
    return RedirectResponse(
        f"/ui/political?{urlencode(parse_qs(form.get('back', '')), doseq=True)}", 303
    )
