"""People from the news of a period, against PostgreSQL: dedup, boundaries, sources, order."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from support.query_counter import count_queries

from db.models.extraction import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
)
from db.models.persons import PersonEventLinkRecord, PersonRecord
from db.models.sources import ParsedArticleRecord, Source, SourceDocument
from persons.models import PersonStatus
from telegram_bot.models import PeopleFromNewsResult
from telegram_bot.people_repository import PeopleFromNewsRepository

NEWS_URL = "https://news.example"
OTHER_NEWS_URL = "https://other-news.example"
REGISTRY_URL = "https://registry.example"
NEWS_URLS = [NEWS_URL, OTHER_NEWS_URL]

PERIOD_START = datetime(2026, 9, 1, tzinfo=UTC)
PERIOD_END = datetime(2026, 9, 20, tzinfo=UTC)


class Builder:
    """The smallest rows the query walks through, written straight to the tables."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._sources: dict[str, Source] = {}
        self._external = 0

    def source(self, base_url: str, name: str) -> Source:
        if base_url not in self._sources:
            source = Source(name=name, base_url=base_url)
            self._session.add(source)
            self._session.flush()
            self._sources[base_url] = source
        return self._sources[base_url]

    def person(self, name: str, *, status: PersonStatus = PersonStatus.ACTIVE) -> PersonRecord:
        person = PersonRecord(
            canonical_name=name,
            normalized_name=name.lower(),
            matching_key=name.lower().replace(" ", "-"),
            status=status.value,
        )
        self._session.add(person)
        self._session.flush()
        return person

    def article(
        self,
        title: str,
        published_at: datetime | None,
        *,
        base_url: str = NEWS_URL,
        source_name: str = "ОВД-Инфо",
    ) -> ArticleExtractionRunRecord:
        """An article with its extraction run; mentions and events hang off the run."""
        source = self.source(base_url, source_name)
        self._external += 1
        document = SourceDocument(
            source_id=source.id,
            external_id=f"ext-{self._external}",
            canonical_url=f"{base_url}/{self._external}",
            fetched_at=datetime(2026, 9, 20, tzinfo=UTC),
            content_type="text/html",
            raw_content=b"<html></html>",
        )
        self._session.add(document)
        self._session.flush()
        article = ParsedArticleRecord(
            document_id=document.id,
            title=title,
            published_at=published_at,
            text=f"{title} body",
        )
        self._session.add(article)
        self._session.flush()
        run = ArticleExtractionRunRecord(
            article_id=article.id,
            article_content_hash=f"hash-{self._external}",
            extractor_name="rule-based",
            extractor_version="1",
            normalizer_version="1",
            status="succeeded",
            started_at=datetime(2026, 9, 20, tzinfo=UTC),
        )
        self._session.add(run)
        self._session.flush()
        return run

    def mention(
        self,
        run: ArticleExtractionRunRecord,
        person: PersonRecord | None,
        *,
        offset: int = 0,
        surface: str = "Иванов",
    ) -> EntityMentionRecord:
        mention = EntityMentionRecord(
            extraction_run_id=run.id,
            entity_type="person",
            surface_text=surface,
            normalized_text=surface.lower(),
            start_offset=offset,
            end_offset=offset + len(surface),
            confidence=0.9,
            normalized_data={},
            extractor_name="rule-based",
            extractor_version="1",
            normalizer_version="1",
            person_id=None if person is None else person.id,
        )
        self._session.add(mention)
        self._session.flush()
        return mention

    def event_for(
        self, run: ArticleExtractionRunRecord, person: PersonRecord, *, offset: int = 0
    ) -> None:
        event = ExtractedEventRecord(
            extraction_run_id=run.id,
            event_type="detention",
            event_date=None,
            start_offset=offset,
            end_offset=offset + 10,
            confidence=0.8,
            attributes={},
            extractor_name="rule-based",
            extractor_version="1",
        )
        self._session.add(event)
        self._session.flush()
        self._session.add(
            PersonEventLinkRecord(
                person_id=person.id, event_id=event.id, role="subject", confidence=0.8
            )
        )
        self._session.flush()


def repository(session_factory: sessionmaker[Session]) -> PeopleFromNewsRepository:
    return PeopleFromNewsRepository(session_factory, news_base_urls=NEWS_URLS)


def people_in_period(
    session_factory: sessionmaker[Session],
    *,
    start: datetime = PERIOD_START,
    end: datetime = PERIOD_END,
    limit: int = 50,
) -> PeopleFromNewsResult:
    return repository(session_factory).people_in_period(
        date_from=start.date(),
        date_to=(end - timedelta(days=1)).date(),
        start=start,
        end=end,
        limit=limit,
    )


def test_a_person_with_one_article(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        run = build.article("Задержание", datetime(2026, 9, 10, tzinfo=UTC))
        build.mention(run, person)

    result = people_in_period(session_factory)

    assert result.total == 1
    assert [p.canonical_name for p in result.people] == ["Иванов Иван"]
    assert result.people[0].article_count == 1
    assert result.people[0].sources == ["ОВД-Инфо"]
    assert [a.title for a in result.people[0].articles] == ["Задержание"]


def test_several_mentions_of_one_article_count_once(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        run = build.article("Задержание", datetime(2026, 9, 10, tzinfo=UTC))
        build.mention(run, person, offset=0)
        build.mention(run, person, offset=50, surface="Иванова")

    result = people_in_period(session_factory)

    assert result.people[0].article_count == 1
    assert len(result.people[0].articles) == 1


def test_a_mention_and_an_event_of_one_article_count_once(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        run = build.article("Задержание", datetime(2026, 9, 10, tzinfo=UTC))
        build.mention(run, person)
        build.event_for(run, person)

    result = people_in_period(session_factory)

    assert result.total == 1
    assert result.people[0].article_count == 1


def test_a_person_reached_only_through_an_event_is_included(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        run = build.article("Приговор", datetime(2026, 9, 10, tzinfo=UTC))
        build.event_for(run, person)

    result = people_in_period(session_factory)

    assert [p.canonical_name for p in result.people] == ["Иванов Иван"]


def test_a_person_with_several_articles_counts_them_all(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        for day in (5, 10, 15):
            run = build.article(f"Статья {day}", datetime(2026, 9, day, tzinfo=UTC))
            build.mention(run, person)

    result = people_in_period(session_factory)

    assert result.people[0].article_count == 3
    assert [a.title for a in result.people[0].articles] == ["Статья 15", "Статья 10", "Статья 5"]


def test_at_most_three_articles_per_person(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        for day in range(1, 8):
            run = build.article(f"Статья {day}", datetime(2026, 9, day, tzinfo=UTC))
            build.mention(run, person)

    result = people_in_period(session_factory)

    assert result.people[0].article_count == 7
    assert [a.title for a in result.people[0].articles] == ["Статья 7", "Статья 6", "Статья 5"]


def test_a_merged_person_is_excluded(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        merged = build.person("Иванов Иван", status=PersonStatus.MERGED)
        run = build.article("Задержание", datetime(2026, 9, 10, tzinfo=UTC))
        build.mention(run, merged)

    assert people_in_period(session_factory).people == []


def test_an_unresolved_mention_is_excluded(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        build.person("Иванов Иван")
        run = build.article("Задержание", datetime(2026, 9, 10, tzinfo=UTC))
        build.mention(run, None)

    assert people_in_period(session_factory).people == []


def test_articles_outside_the_period_are_excluded(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        before = build.article("До", datetime(2026, 8, 31, 23, 59, tzinfo=UTC))
        after = build.article("После", datetime(2026, 9, 20, 0, 0, tzinfo=UTC))
        build.mention(before, person)
        build.mention(after, person)

    assert people_in_period(session_factory).people == []


def test_both_period_boundaries_are_included(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        first = build.article("Первый день", datetime(2026, 9, 1, 0, 0, tzinfo=UTC))
        last = build.article("Последний день", datetime(2026, 9, 19, 23, 59, tzinfo=UTC))
        build.mention(first, person)
        build.mention(last, person)

    result = people_in_period(session_factory)

    assert result.people[0].article_count == 2


def test_an_article_without_a_publication_date_is_excluded(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        build.mention(build.article("Без даты", None), person)

    assert people_in_period(session_factory).people == []


def test_a_registry_source_is_excluded(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        card = build.article(
            "Карточка фигуранта",
            datetime(2026, 9, 10, tzinfo=UTC),
            base_url=REGISTRY_URL,
            source_name="Мемориал: реестр",
        )
        build.mention(card, person)

    assert people_in_period(session_factory).people == []


def test_sources_of_a_person_are_distinct_and_sorted(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        person = build.person("Иванов Иван")
        for day, base_url, name in (
            (5, NEWS_URL, "ОВД-Инфо"),
            (10, OTHER_NEWS_URL, "SOTA"),
            (15, NEWS_URL, "ОВД-Инфо"),
        ):
            run = build.article(
                f"Статья {day}",
                datetime(2026, 9, day, tzinfo=UTC),
                base_url=base_url,
                source_name=name,
            )
            build.mention(run, person)

    assert people_in_period(session_factory).people[0].sources == ["SOTA", "ОВД-Инфо"]


def test_people_are_sorted_by_latest_article_then_by_name(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        newest = build.person("Сидоров Сидор")
        same_day_b = build.person("Петров Пётр")
        same_day_a = build.person("Абрамов Абрам")
        build.mention(build.article("Новее", datetime(2026, 9, 18, tzinfo=UTC)), newest)
        shared_day = datetime(2026, 9, 10, tzinfo=UTC)
        build.mention(build.article("Тот же день Б", shared_day), same_day_b)
        build.mention(build.article("Тот же день А", shared_day), same_day_a)

    names = [p.canonical_name for p in people_in_period(session_factory).people]

    assert names == ["Сидоров Сидор", "Абрамов Абрам", "Петров Пётр"]


def test_total_counts_people_before_the_limit(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        for index in range(5):
            person = build.person(f"Человек {index}")
            build.mention(
                build.article(f"Статья {index}", datetime(2026, 9, 10 + index, tzinfo=UTC)), person
            )

    result = people_in_period(session_factory, limit=2)

    assert result.total == 5
    assert len(result.people) == 2
    assert [p.canonical_name for p in result.people] == ["Человек 4", "Человек 3"]


def test_an_empty_period_returns_nothing(session_factory: sessionmaker[Session]) -> None:
    result = people_in_period(session_factory)

    assert result.total == 0 and result.people == []


def test_the_whole_answer_takes_one_query(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    with session_factory.begin() as session:
        build = Builder(session)
        for index in range(10):
            person = build.person(f"Человек {index}")
            for day in (5, 10, 15):
                run = build.article(f"Статья {index}-{day}", datetime(2026, 9, day, tzinfo=UTC))
                build.mention(run, person)
                build.event_for(run, person)

    with count_queries(test_engine) as statements:
        result = people_in_period(session_factory)

    assert len(result.people) == 10
    # The query is one CTE chain: it starts with WITH, not SELECT.
    reads = [sql for sql in statements if sql.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert len(reads) == 1


def test_the_period_is_reported_back(session_factory: sessionmaker[Session]) -> None:
    result = repository(session_factory).people_in_period(
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 19),
        start=PERIOD_START,
        end=PERIOD_END,
        limit=50,
    )

    assert (result.date_from, result.date_to, result.limit) == (
        date(2026, 9, 1),
        date(2026, 9, 19),
        50,
    )
