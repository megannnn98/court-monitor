"""The candidates as the customer's table shows them: period, order, news, names surname first."""

from datetime import date, datetime, timedelta
from typing import NamedTuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from candidates.models import PoliticalPersecutionCandidate, RosfinmonitoringStatus
from candidates.service import DEFAULT_INCLUDED_RF_STATUSES, CandidateQueryService
from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersonEventLinkRecord,
    SourceDocument,
)


class _CandidateNews(NamedTuple):
    url: str
    published_at: datetime | None
    event_type: str | None
    event_date: datetime | None = None


class _CandidateRow(NamedTuple):
    candidate: PoliticalPersecutionCandidate
    news: _CandidateNews | None


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


def _is_administrative_only(reasons: list[str]) -> bool:
    """Every political article of the person is from КоАП: an administrative case."""
    charges = [reason for reason in reasons if reason.startswith(_POLITICAL_CHARGE_REASON)]
    return bool(charges) and not any("УК" in charge for charge in charges)


def _is_criminal_only(reasons: list[str]) -> bool:
    """At least one political charge references УК (criminal code), none reference КоАП."""
    charges = [reason for reason in reasons if reason.startswith(_POLITICAL_CHARGE_REASON)]
    if not charges:
        return False
    has_criminal = any("УК" in charge for charge in charges)
    has_administrative = any("КоАП" in charge for charge in charges)
    return has_criminal and not has_administrative


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
    """The candidates of the page and its Excel export, filtered and in the table order.

    The candidate definition stays the service's; the period, the administrative cases
    and the order are the customer's view of it. The channel queue widens the
    Rosfinmonitoring statuses: the channel publishes people on the list too.

    When *event_date_filter* is True (default), candidates whose latest event is before
    *period_start* are excluded — old cases mentioned in fresh articles are filtered out.
    When *criminal_only* is True, only candidates with at least one criminal (УК) charge
    and no administrative (КоАП) charges are included.
    """
    try:
        result = CandidateQueryService(db).get_candidates(
            snapshot_id=snapshot_id,
            min_persecution_confidence=min_confidence,
            limit=None,
            include_rf_statuses=include_rf_statuses,
            session=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    news = _candidate_news(db, [candidate.person_id for candidate in result.candidates])
    rows: list[_CandidateRow] = []
    for candidate in result.candidates:
        if not include_administrative and _is_administrative_only(candidate.persecution_reasons):
            continue
        if criminal_only and not _is_criminal_only(candidate.persecution_reasons):
            continue
        item = news.get(candidate.person_id)
        published_at = item.published_at if item is not None else None
        event_date = item.event_date if item is not None else None

        # Filter by article date (publication date).
        if period_start is not None and (
            published_at is None or _news_day(published_at) < period_start
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
    if words[-1].lower().endswith(_PATRONYMIC_ENDINGS):
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
