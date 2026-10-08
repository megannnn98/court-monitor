"""The rows of «Результат»: who is on the list under the filters, and what a row tells.

Two kinds of people stand on the list — the entities with a political verdict, and the
figurants a publication does not name whose case goes by a political article
(`entities.unnamed_cases`). Each becomes a `ListRow` here, and from then on nothing asks
which kind it is: the filters, the page and the Excel file read the row.

Everyone of the result is read first, in two steps, and the filters are applied to the
rows — one period, one rule of «обработано», one count of the latest news for both kinds.
What only a shown row needs (the list's entry, the articles, the publications) is read
for the rows of a page, by `with_details`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import quote

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupNewsRecord, EntityGroupPoliticsRecord, EntityGroupRecord
from entities.done_marks import Marks, done_marks, is_done
from entities.evidence import person_evidence_cte
from entities.known_base import KnownBase, KnownMatch
from entities.known_nameless import NamelessBase
from entities.news import NEW_CASE, ONGOING, OTHER, SENTENCE
from entities.politics import MEMORIAL_CATEGORIES, POLITICAL
from entities.rf_articles import AWAITED, Listing, listing
from entities.rf_check import FULL
from entities.rf_entry import strongest_entries
from entities.unnamed_cases import KEY_PREFIX, Case, political_cases
from web.ui.entities import _articles_by_group, display_name, regions_text
from web.ui.political_filters import Filters

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
_OLDEST = datetime.min.replace(tzinfo=UTC)

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

# (title, url, published, source) of a publication.
Link = tuple[str, str, datetime | None, str]


@dataclass(frozen=True)
class Basis:
    """Why a person is on the list: who decided, the reason, and the words of the text."""

    method: str
    reason: str
    quote: str


@dataclass
class ListRow:
    key: str
    # As the data holds it: given name first for a person, as told for an unnamed figurant.
    name: str
    # Where the name leads: the dossier, or the figurant's sentences on «Безымянные».
    href: str
    last_published: datetime | None
    regions: str
    basis: Basis
    # The entity behind a person with a name; None for a figurant the text does not name.
    group_id: int | None = None
    # The operator's «обработано», still standing (no news since).
    done: bool = False
    # What the latest news is, and why the model said so.
    news_kind: str | None = None
    news_reason: str = ""
    articles: list[tuple[str, bool]] = field(default_factory=list)
    # The list's entry: «full» (the name with the patronymic) or «name», and who it is.
    rf_level: str | None = None
    rf_entry: str = ""
    # When this entry of the перечень appeared, if the ОВД-Инфо copy says. It describes
    # the entry, not the person; see `entities.rf_entry.entry_included_text`.
    rf_inclusion_date: datetime | None = None
    rf_inclusion_source: str | None = None
    # What the operator's base says of this person; None: nobody by this name there.
    known: KnownMatch | None = None
    # An unnamed person: the age and the sex the text tells, to look for a record of the
    # base that names nobody either (the place is `regions`).
    age: int | None = None
    gender: str = ""
    # The day the age was told.
    age_day: date | None = None
    memorial: str | None = None
    first_published: datetime | None = None
    # The latest publications.
    links: list[Link] = field(default_factory=list)

    @property
    def unnamed(self) -> bool:
        return self.group_id is None

    @property
    def listing(self) -> Listing | None:
        """What the articles say of the перечень. Only of a person with a name: one the
        text does not name cannot be on a list of names, and is looked for on «Безымянные»."""
        if self.unnamed:
            return None
        return listing((article for article, _ in self.articles), self.rf_level)

    @property
    def maybe_listed(self) -> bool:
        """On the list by the name alone (no patronymic on one side): maybe a namesake."""
        return self.rf_level is not None and self.rf_level != FULL

    @property
    def shown_name(self) -> str:
        """Surname first for a person, as told for an unnamed figurant."""
        return self.name if self.unnamed else display_name(self.name)


def named_row(
    entity: EntityGroupRecord,
    politics: EntityGroupPoliticsRecord,
    news: EntityGroupNewsRecord | None = None,
    *,
    done: bool = False,
) -> ListRow:
    """A person with a name; the details of a shown row come later (`with_details`)."""
    return ListRow(
        key=entity.key,
        name=entity.name,
        href=f"/ui/investigations/{quote(entity.key)}",
        last_published=entity.last_published_at,
        regions=regions_text(entity.regions),
        basis=Basis(politics.method, politics.reason, politics.quote),
        group_id=entity.id,
        done=done,
        news_kind=news.kind if news else None,
        news_reason=news.reason if news else "",
    )


def unnamed_row(case: Case, *, done: bool = False) -> ListRow:
    """An unnamed figurant: what the sentences tell, in the columns the named have."""
    latest = case.latest
    links: dict[int, Link] = {}
    for sentence in sorted(
        case.sentences, key=lambda sentence: sentence.published_at or _OLDEST, reverse=True
    ):
        links.setdefault(
            sentence.article_id,
            (sentence.title, sentence.url, sentence.published_at, sentence.source),
        )
    return ListRow(
        key=case.key,
        name=case.name,
        # No dossier: the sentences stand on «Безымянные», where the person is identified.
        href=f"/ui/unnamed?status=all#u-{quote(latest.key)}",
        last_published=case.last_published_at,
        regions=case.place,
        # On the list by the political article the text names.
        basis=Basis("article", latest.explanation, latest.quote),
        done=done,
        news_kind=_EVENT_NEWS.get(latest.event_type, OTHER),
        news_reason=latest.explanation,
        articles=[(article, True) for article in case.articles],
        first_published=case.first_published_at,
        links=list(links.values())[:LINKS],
        age=case.age,
        gender=case.gender,
        age_day=told.date() if (told := case.age_told_at) else None,
    )


def _everyone(db: Session, marks: Marks) -> list[ListRow]:
    """Everybody of the result before the filters: the named, latest news first, then the
    unnamed."""
    named = db.execute(
        select(EntityGroupRecord, EntityGroupPoliticsRecord, EntityGroupNewsRecord)
        .join(EntityGroupPoliticsRecord, EntityGroupPoliticsRecord.group_id == EntityGroupRecord.id)
        .outerjoin(EntityGroupNewsRecord, EntityGroupNewsRecord.group_id == EntityGroupRecord.id)
        .where(EntityGroupPoliticsRecord.verdict == POLITICAL)
        .order_by(EntityGroupRecord.last_published_at.desc().nulls_last(), EntityGroupRecord.key)
    ).all()
    return [
        *(
            named_row(
                entity,
                politics,
                news,
                done=is_done(marks, entity.key, entity.last_published_at),
            )
            for entity, politics, news in named
        ),
        *(
            unnamed_row(case, done=is_done(marks, case.key, case.last_published_at))
            for case in political_cases(db)
        ),
    ]


def known_answer(match: KnownMatch | None) -> str:
    return match.level if match else "none"


@dataclass(frozen=True)
class Result:
    """The list under the filters, latest news first, and what the filters' labels count."""

    rows: list[ListRow]
    # The people of each latest news in the period, before the choice of one.
    news_counts: dict[str, int]
    # The people per answer of the operator's base («none» for nobody by the name),
    # before the choice of one.
    known_counts: dict[str, int]
    # How many people the base holds; none loaded, the answer «not there» means nothing.
    base_size: int
    # Everybody marked «обработано», whatever the filters.
    done_total: int
    # The people charged under an article of the перечень and not on it, before the choice.
    awaited: int = 0


def result_rows(db: Session, chosen: Filters, now: datetime | None = None) -> Result:
    everyone = _everyone(db, done_marks(db))
    start, end = chosen.window(now or datetime.now(UTC))
    in_period = [
        row
        for row in everyone
        if chosen.who in ("all", "unnamed" if row.unnamed else "named")
        and (chosen.done == "show" or row.done == (chosen.done == "only"))
        and _within(row.last_published, start, end)
    ]
    news_counts = Counter(row.news_kind for row in in_period if row.news_kind)
    # Stable: the named keep their order, and an unnamed row of the same moment follows.
    found = sorted(
        (row for row in in_period if chosen.news in ("all", row.news_kind)),
        key=lambda row: row.last_published or _OLDEST,
        reverse=True,
    )
    base = KnownBase.from_session(db)
    # A person without a name is looked for among the records that name nobody — by the
    # age, the sex and the place, on the day of their latest news.
    nameless = NamelessBase.from_session(db)
    for row in found:
        row.known = (
            nameless.match(row.age, row.gender, row.regions, row.age_day)
            if row.unnamed
            else base.match(row.name)
        )
    known_counts = Counter(known_answer(row.known) for row in found)
    # With the base not loaded (a sync has never run) nobody is «not in the base» — the
    # base is not there — so the choice is ignored and the page says so.
    # Loaded is either kind of record: a base of nameless records alone is a base.
    base_size = len(base) + len(nameless)
    if chosen.known != "all" and base_size:
        found = [row for row in found if known_answer(row.known) == chosen.known]
    # What the articles say of the list is asked of everybody, not of a page: the choice
    # and its count are of the whole list.
    named = {row.group_id: row for row in found if row.group_id is not None}
    for group_id, articles in _articles_by_group(db, list(named)).items():
        named[group_id].articles = articles
    for group_id, entry in strongest_entries(db, list(named)).items():
        named[group_id].rf_level = entry.level
    awaited = sum(1 for row in found if row.listing and row.listing.kind == AWAITED)
    if chosen.rfm == "awaited":
        found = [row for row in found if row.listing and row.listing.kind == AWAITED]
    return Result(
        awaited=awaited,
        rows=found,
        news_counts=dict(news_counts),
        known_counts=dict(known_counts),
        base_size=base_size,
        done_total=sum(row.done for row in everyone),
    )


def queue_counts(db: Session, chosen: Filters, now: datetime | None = None) -> dict[str, int]:
    """How many people each queue holds in the period of `chosen`, whatever queue is open:
    one reading of the period, with everybody, «обработано» included."""
    everyone = result_rows(
        db,
        Filters(
            months=chosen.months, date_from=chosen.date_from, date_to=chosen.date_to, done="show"
        ),
        now,
    )
    open_rows = [row for row in everyone.rows if not row.done]
    counts = {
        "all": len(open_rows),
        "new_case": sum(row.news_kind == NEW_CASE for row in open_rows),
        "sentence": sum(row.news_kind == SENTENCE for row in open_rows),
        "unnamed": sum(row.unnamed for row in open_rows),
        "awaited": sum(bool(row.listing and row.listing.kind == AWAITED) for row in open_rows),
        "done": len(everyone.rows) - len(open_rows),
    }
    if everyone.base_size:
        counts["not_in_base"] = sum(known_answer(row.known) == "none" for row in open_rows)
    return counts


def _within(moment: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    """In the period; a person with no dated news is in no period but «all the time»."""
    if start is None and end is None:
        return True
    return (
        moment is not None and (start is None or moment >= start) and (end is None or moment < end)
    )


def with_details(db: Session, rows: list[ListRow]) -> list[ListRow]:
    """The rows with what only a shown row needs. The unnamed are whole already: what is
    known of them is in their sentences."""
    named = {row.group_id: row for row in rows if row.group_id is not None}
    ids = list(named)
    for group_id, entry in strongest_entries(db, ids).items():
        row = named[group_id]
        row.rf_level, row.rf_entry, row.rf_inclusion_date, row.rf_inclusion_source = (
            entry.level,
            entry.text,
            entry.inclusion_date,
            entry.inclusion_source,
        )
    for group_id, articles in _articles_by_group(db, ids).items():
        named[group_id].articles = articles
    for group_id, category in db.execute(MEMORIAL_CATEGORIES, {"groups": ids}).all():
        named[group_id].memorial = category
    # `relevant` is None for an article the model has not classified yet — falls back to
    # the title keywords (they only catch the digest case, not «иноагент»/pickets, but
    # that is still no worse than showing everything unfiltered).
    publications: dict[int, list[tuple[Link, bool | None]]] = {}
    for group_id, _id, title, published_at, url, source, relevant in db.execute(
        _PUBLICATIONS, {"groups": ids, "context": 0}
    ).all():
        publications.setdefault(group_id, []).append(((title, url, published_at, source), relevant))
    for group_id, found in publications.items():
        dates = [link[2] for link, _ in found if link[2] is not None]
        named[group_id].first_published = min(dates) if dates else None
        substantive = [
            link
            for link, relevant in found
            if (relevant if relevant is not None else not _is_digest(link[0]))
        ]
        named[group_id].links = sorted(
            substantive or [link for link, _ in found],
            key=lambda link: link[2] or _OLDEST,
            reverse=True,
        )[:LINKS]
    return rows


def _is_digest(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in _DIGEST_MARKERS)


def latest_news(db: Session, key: str) -> datetime | None:
    """The latest news of the person a mark is made on; LookupError for nobody's key."""
    if key.startswith(KEY_PREFIX):
        case = next((case for case in political_cases(db) if case.key == key), None)
        if case is None:
            raise LookupError(key)
        return case.last_published_at
    entity = db.scalar(select(EntityGroupRecord).where(EntityGroupRecord.key == key))
    if entity is None:
        raise LookupError(key)
    return entity.last_published_at
