"""The candidates as the customer's table shows them: period, order, news, names surname first."""

from datetime import date, datetime, timedelta
from typing import NamedTuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from candidates.models import (
    DEFAULT_MIN_PERSECUTION_CONFIDENCE,
    PoliticalPersecutionCandidate,
    RosfinmonitoringStatus,
)
from candidates.service import DEFAULT_INCLUDED_RF_STATUSES, CandidateQueryService
from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersonEventLinkRecord,
    RosfinmonitoringSnapshotRecord,
    SourceDocument,
)
from extraction.name_frequency import lookup_gender


class _CandidateNews(NamedTuple):
    url: str
    published_at: datetime | None
    event_type: str | None
    event_date: datetime | None = None


class _CandidateRow(NamedTuple):
    candidate: PoliticalPersecutionCandidate
    news: _CandidateNews | None


# The public name of a row for the other consumers of the selection (the Telegram bot).
CandidateRow = _CandidateRow


# The categories of the customer's table, by the event the row links to.
_CANDIDATE_CATEGORIES = {
    "case_opened": "Возбуждено дело",
    "charge": "Обвинение",
    "arrest": "Арест",
    "sentence": "Приговор",
    "detention": "Задержание",
    "search": "Обыск",
    "fine": "Штраф",
    "release": "Освобождение",
    "other": "Другое",
}


# New cases and sentences first (customer priority), then detentions and searches.
_CATEGORY_PRIORITY = {
    "case_opened": 0,
    "charge": 0,
    "arrest": 0,
    "sentence": 0,
    "detention": 1,
    "search": 1,
}


_OTHER_CATEGORY_PRIORITY = 2


# The customer reviews the last month and a half.
_DEFAULT_NEWS_PERIOD = timedelta(days=45)


# The sources publish in Moscow time; a news day is a Moscow day.
_NEWS_TIMEZONE = ZoneInfo("Europe/Moscow")


_POLITICAL_CHARGE_REASON = "Политическая статья:"


_PATRONYMIC_ENDINGS = ("вич", "вна", "ична")


def _candidate_news(db: Session, person_ids: list[int]) -> dict[int, _CandidateNews]:
    """The source article of each person's latest event, as the person card orders them.

    A person without events gets the article of their first mention.
    """
    if not person_ids:
        return {}
    from_events = db.execute(
        select(
            PersonEventLinkRecord.person_id,
            SourceDocument.canonical_url,
            ParsedArticleRecord.published_at,
            ExtractedEventRecord.event_type,
            ExtractedEventRecord.event_date,
        )
        .join(ExtractedEventRecord, ExtractedEventRecord.id == PersonEventLinkRecord.event_id)
        .join(
            ArticleExtractionRunRecord,
            ArticleExtractionRunRecord.id == ExtractedEventRecord.extraction_run_id,
        )
        .join(ParsedArticleRecord, ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id)
        .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
        .where(PersonEventLinkRecord.person_id.in_(person_ids))
        .distinct(PersonEventLinkRecord.person_id)
        .order_by(
            PersonEventLinkRecord.person_id,
            ExtractedEventRecord.event_date.desc().nullslast(),
            ExtractedEventRecord.id.desc(),
        )
    ).tuples()
    news = {
        person_id: _CandidateNews(url, published_at, event_type, event_date)
        for person_id, url, published_at, event_type, event_date in from_events.all()
    }
    without_events = [person_id for person_id in person_ids if person_id not in news]
    if without_events:
        from_mentions = db.execute(
            select(
                EntityMentionRecord.person_id,
                SourceDocument.canonical_url,
                ParsedArticleRecord.published_at,
            )
            .join(
                ArticleExtractionRunRecord,
                ArticleExtractionRunRecord.id == EntityMentionRecord.extraction_run_id,
            )
            .join(
                ParsedArticleRecord,
                ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id,
            )
            .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
            .where(EntityMentionRecord.person_id.in_(without_events))
            .distinct(EntityMentionRecord.person_id)
            .order_by(
                EntityMentionRecord.person_id,
                ParsedArticleRecord.published_at.asc().nullslast(),
                EntityMentionRecord.id,
            )
        ).tuples()
        news.update(
            (person_id, _CandidateNews(url, published_at, None))
            for person_id, url, published_at in from_mentions.all()
            if person_id is not None
        )
    return news


def _news_day(moment: datetime) -> date:
    return moment.astimezone(_NEWS_TIMEZONE).date()


def _period_start(date_from: str | None) -> date | None:
    """The first news day to show: the default period when not given, none when empty."""
    if date_from is None:
        return datetime.now(_NEWS_TIMEZONE).date() - _DEFAULT_NEWS_PERIOD
    if not date_from.strip():
        return None
    try:
        return date.fromisoformat(date_from)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date_from: {date_from!r}") from exc


def _is_administrative_only(
    reasons: list[str],
    event_type: str | None = None,
) -> bool:
    """The candidate's persecution is purely administrative.

    True when either:
    - Every political charge references КоАП (not УК), OR
    - The most recent event is a ``fine`` and there is no УК charge anywhere.

    The second condition catches cases where the classifier marked the person
    political based on article text keywords but all their actual events are
    administrative fines.
    """
    charges = [reason for reason in reasons if reason.startswith(_POLITICAL_CHARGE_REASON)]
    has_criminal_charge = any("УК" in charge for charge in charges)
    # Explicit КоАП-only charges.
    if charges and not has_criminal_charge:
        return True
    # No explicit charge references, but the most recent event is a fine.
    return event_type == "fine" and not has_criminal_charge


def _has_criminal_events(
    event_type: str | None,
    reasons: list[str],
) -> bool:
    """Whether the candidate's persecution is shown to be criminal, not administrative.

    - A criminal-code (УК) charge is criminal whatever the latest news is about.
    - КоАП charges only: administrative, whatever the event type — «арестован на 15
      суток» is an administrative arrest, not a criminal case.
    - No charge reference: the latest event is the only evidence; a ``fine`` is
      administrative, and a person with no linked event proves nothing criminal.
    """
    charges = [reason for reason in reasons if reason.startswith(_POLITICAL_CHARGE_REASON)]
    if any("УК" in charge for charge in charges):
        return True
    if charges:
        return False
    return event_type is not None and event_type != "fine"


def latest_snapshot_id(db: Session) -> int | None:
    """The Rosfinmonitoring snapshot the candidates page opens on: the newest one.

    The Telegram bot uses the same choice, so a person is a candidate in both or in
    neither.
    """
    return db.scalar(
        select(RosfinmonitoringSnapshotRecord.id)
        .order_by(
            RosfinmonitoringSnapshotRecord.snapshot_date.desc(),
            RosfinmonitoringSnapshotRecord.id.desc(),
        )
        .limit(1)
    )


def _candidate_rows(
    db: Session,
    *,
    snapshot_id: int,
    min_confidence: float,
    period_start: date | None,
    include_administrative: bool,
    criminal_only: bool = False,
    event_date_filter: bool = True,
    include_rf_statuses: frozenset[RosfinmonitoringStatus] = DEFAULT_INCLUDED_RF_STATUSES,
) -> list[_CandidateRow]:
    """`select_candidate_rows` for the web pages: an unknown snapshot is a 404."""
    try:
        return select_candidate_rows(
            db,
            snapshot_id=snapshot_id,
            min_confidence=min_confidence,
            period_start=period_start,
            include_administrative=include_administrative,
            criminal_only=criminal_only,
            event_date_filter=event_date_filter,
            include_rf_statuses=include_rf_statuses,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def select_candidate_rows(
    db: Session,
    *,
    snapshot_id: int,
    min_confidence: float = DEFAULT_MIN_PERSECUTION_CONFIDENCE,
    period_start: date | None,
    period_end: date | None = None,
    include_administrative: bool = False,
    criminal_only: bool = False,
    event_date_filter: bool = True,
    include_rf_statuses: frozenset[RosfinmonitoringStatus] = DEFAULT_INCLUDED_RF_STATUSES,
) -> list[_CandidateRow]:
    """The candidates of the page and its Excel export, filtered and in the table order.

    The candidate definition stays the service's; the period, the administrative cases
    and the order are the customer's view of it. The channel queue widens the
    Rosfinmonitoring statuses: the channel publishes people on the list too.

    When *event_date_filter* is True (default), candidates whose latest event is before
    *period_start* are excluded — old cases mentioned in fresh articles are filtered out.
    *period_end* (inclusive news day, the bot's «по») excludes later news; the web page
    has no end and passes none.
    Raises ``ValueError`` for an unknown snapshot.
    When *criminal_only* is True, only candidates whose most recent event is not a
    ``fine`` (or who have a criminal-code charge) are included — administrative
    fines for already-known political prisoners are filtered out.
    """
    result = CandidateQueryService(db).get_candidates(
        snapshot_id=snapshot_id,
        min_persecution_confidence=min_confidence,
        limit=None,
        include_rf_statuses=include_rf_statuses,
        session=db,
    )
    news = _candidate_news(db, [candidate.person_id for candidate in result.candidates])
    rows: list[_CandidateRow] = []
    for candidate in result.candidates:
        item = news.get(candidate.person_id)
        event_type = item.event_type if item is not None else None
        if not include_administrative and _is_administrative_only(
            candidate.persecution_reasons, event_type
        ):
            continue
        if criminal_only and not _has_criminal_events(event_type, candidate.persecution_reasons):
            continue
        published_at = item.published_at if item is not None else None
        event_date = item.event_date if item is not None else None

        # Filter by article date (publication date).
        if period_start is not None and (
            published_at is None or _news_day(published_at) < period_start
        ):
            continue
        if period_end is not None and (
            published_at is None or _news_day(published_at) > period_end
        ):
            continue

        # Filter by event date: exclude old events mentioned in fresh articles.
        if (
            event_date_filter
            and period_start is not None
            and event_date is not None
            and _news_day(event_date) < period_start
        ):
            continue

        rows.append(_CandidateRow(candidate, item))

    def order(row: _CandidateRow) -> tuple[int, int, float, int]:
        event_type = row.news.event_type if row.news is not None else None
        published_at = row.news.published_at if row.news is not None else None
        return (
            _CATEGORY_PRIORITY.get(event_type or "", _OTHER_CATEGORY_PRIORITY),
            0 if published_at is not None else 1,
            -published_at.timestamp() if published_at is not None else 0.0,
            row.candidate.person_id,
        )

    return sorted(rows, key=order)


def _surname_first(name: str) -> str:
    """«Иван Иванов» → «Иванов Иван»; a name ending in a patronymic already starts with it."""
    words = name.split()
    if len(words) < 2:
        return name
    if "." in words[0]:
        initials = [word for word in words if "." in word]
        return " ".join([*(word for word in words if "." not in word), *initials])
    # «Михаил Антонович»: a given name, then a surname in «-ович», not a patronymic.
    two_given_first = len(words) == 2 and lookup_gender(words[0]) is not None
    if words[-1].lower().endswith(_PATRONYMIC_ENDINGS) and not two_given_first:
        return name
    if len(words) == 3 and words[1].lower().endswith(_PATRONYMIC_ENDINGS):
        return " ".join([words[2], words[0], words[1]])
    return " ".join([words[-1], *words[:-1]])


def _candidate_filters(
    snapshot_id: int,
    min_confidence: float,
    period_start: date | None,
    include_administrative: bool,
    criminal_only: bool = False,
    event_date_filter: bool = True,
) -> str:
    params: dict[str, str | int | float] = {
        "snapshot_id": snapshot_id,
        "min_confidence": min_confidence,
        "date_from": period_start.isoformat() if period_start is not None else "",
    }
    if include_administrative:
        params["include_administrative"] = "1"
    if criminal_only:
        params["criminal_only"] = "1"
    if not event_date_filter:
        params["event_date_filter"] = "0"
    return urlencode(params)


_ALL_RF_STATUSES = frozenset(RosfinmonitoringStatus)
