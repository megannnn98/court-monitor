"""A name for a persecution reported without one.

News often leaves the defendant unnamed («Жителя Крыма приговорили к 21 году колонии»)
while another outlet names him the same days. An unnamed event is matched to a named
event of the same type within a few days by what both sentences state: the term, the
age, the article, the place. The match is a suggestion for a person to confirm, never a
link: measured on the working corpus, about seven in ten suggestions were right.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

CASE_EVENT_TYPES = ("sentence", "arrest", "charge", "case_opened", "detention")
MATCH_WINDOW = timedelta(days=3)
# More case events than this in one article is a digest: its neighbouring stories must
# not lend their facts to the sentence being matched.
_DIGEST_EVENTS = 5
_DIGEST_TITLE = re.compile(r"дайджест|судный день|сводк", re.IGNORECASE)
_CONTEXT_BEFORE = 200
_CONTEXT_AFTER = 100

_TERM = re.compile(
    r"(\d+(?:[,.]\d+)?)\s+(?:год|лет)\w*(?:\s+и\s+\S+\s+месяц\w*)?\s+"
    r"(?:колони|лишени|заключени|тюрьм|строгого|общего|особого)",
    re.IGNORECASE,
)
_AGE = re.compile(r"(\d+)-летн", re.IGNORECASE)
_ARTICLE = re.compile(r"(?:ст\.|стать\w+)\s*(\d{3}(?:\.\d+)?)", re.IGNORECASE)
_PLACE = re.compile(
    r"(?<![а-яё])(?:Крым|Севастопол|Донецк|Луганск|Запорож|Херсон|Мелитопол|Мариупол|"
    r"Москв|Петербург|Башкир|Татарстан|Дагестан|Чечн|Ингуш|Якут|Кузбасс|Кемеров|Иркутск|"
    r"Новосибирск|Екатеринбург|Свердлов|Краснодар|Ростов|Самар|Саратов|Приморь|Хабаровск|"
    r"Калининград|Бурят|Уф[аеы]|Пенз|Твер|Курск|Белгород|Брянск|Воронеж|Перм|Тюмен|Омск|"
    r"Томск|Челябинск|Нижн|Казан|Волгоград|Ставропол)\w*"
)
# An unnamed case is worth a name when it is about what the customer tracks.
_POLITICAL = re.compile(
    r"госизмен|государственн\w+\s+измен|политзаключ|шпионаж|(?<![\d.])(?:275|276|280|"
    r"205\.2|207\.3|280\.3|284\.1|354\.1)(?![\d])|Мемориал",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Facts:
    terms: frozenset[str]
    ages: frozenset[str]
    articles: frozenset[str]
    places: frozenset[str]


@dataclass(frozen=True)
class CaseEvent:
    event_id: int
    event_type: str
    article_id: int
    published_at: datetime
    source: str
    url: str
    title: str
    facts: Facts
    political: bool
    target: str | None
    target_person_id: int | None


@dataclass(frozen=True)
class NameSuggestion:
    unnamed: CaseEvent
    named: CaseEvent
    score: int


def facts_of(sentence: str) -> Facts:
    return Facts(
        terms=frozenset(term.replace(",", ".") for term in _TERM.findall(sentence)),
        ages=frozenset(_AGE.findall(sentence)),
        articles=frozenset(_ARTICLE.findall(sentence)),
        places=frozenset(place[:6].lower() for place in _PLACE.findall(sentence)),
    )


def match_score(unnamed: CaseEvent, named: CaseEvent) -> int | None:
    """How well two events agree, or None when they cannot be the same case.

    A verdict needs the same term and one more fact; any other event, the same age and
    place: those are what the unnamed reports keep.
    """
    if unnamed.event_type != named.event_type:
        return None
    if abs(unnamed.published_at - named.published_at) > MATCH_WINDOW:
        return None
    left, right = unnamed.facts, named.facts
    term = bool(left.terms & right.terms)
    age = bool(left.ages & right.ages)
    place = bool(left.places & right.places)
    article = bool(left.articles & right.articles)
    same_case = (unnamed.event_type == "sentence" and term and (place or age or article)) or (
        age and place
    )
    if not same_case:
        return None
    return 2 * term + 2 * age + place + article


def suggest_names(events: Sequence[CaseEvent]) -> list[NameSuggestion]:
    """The best named match of every political unnamed event, one per article."""
    named = [event for event in events if event.target is not None]
    suggestions: list[NameSuggestion] = []
    seen: set[int] = set()
    for event in events:
        if event.target is not None or not event.political or event.article_id in seen:
            continue
        if not (event.facts.terms or event.facts.ages):
            continue
        best: NameSuggestion | None = None
        for candidate in named:
            if candidate.article_id == event.article_id:
                continue
            score = match_score(event, candidate)
            if score is not None and (best is None or score > best.score):
                best = NameSuggestion(event, candidate, score)
        if best is not None:
            seen.add(event.article_id)
            suggestions.append(best)
    return suggestions


_EVENTS_SQL = text(
    """
    SELECT ev.id, ev.event_type, ev.start_offset, ev.end_offset,
           a.id AS article_id, a.published_at, a.title, a.text,
           s.name AS source, d.canonical_url AS url,
           count(*) FILTER (WHERE l.role = 'target' AND m.entity_type = 'person') AS targets,
           max(m.normalized_text) FILTER (WHERE l.role = 'target' AND m.entity_type = 'person')
               AS target,
           max(m.person_id) FILTER (WHERE l.role = 'target' AND m.entity_type = 'person')
               AS target_person_id
    FROM extracted_events ev
    JOIN article_extraction_runs r ON r.id = ev.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    LEFT JOIN event_entity_mentions l ON l.event_id = ev.id
    LEFT JOIN entity_mentions m ON m.id = l.mention_id
    WHERE ev.event_type IN :types AND a.published_at >= :since
    GROUP BY ev.id, a.id, s.name, d.canonical_url
    """
).bindparams(bindparam("types", expanding=True))


def load_case_events(db: Session, since: datetime) -> list[CaseEvent]:
    """Case events published since `since`; only those with at most one target."""
    rows = db.execute(_EVENTS_SQL, {"types": list(CASE_EVENT_TYPES), "since": since}).all()
    per_article = Counter(row.article_id for row in rows)
    events: list[CaseEvent] = []
    for row in rows:
        if row.targets > 1 or row.published_at is None:
            continue
        digest = per_article[row.article_id] > _DIGEST_EVENTS or bool(
            _DIGEST_TITLE.search(row.title)
        )
        sentence = row.text[row.start_offset : row.end_offset]
        if not digest:
            context = row.text[
                max(0, row.start_offset - _CONTEXT_BEFORE) : row.end_offset + _CONTEXT_AFTER
            ]
            sentence = f"{row.title} {context}"
        events.append(
            CaseEvent(
                event_id=row.id,
                event_type=row.event_type,
                article_id=row.article_id,
                published_at=row.published_at,
                source=row.source,
                url=row.url,
                title=row.title,
                facts=facts_of(sentence),
                # A digest's unnamed events are its other stories, not one case.
                political=not digest and bool(_POLITICAL.search(row.text)),
                target=row.target if row.targets == 1 else None,
                target_person_id=row.target_person_id if row.targets == 1 else None,
            )
        )
    return events
