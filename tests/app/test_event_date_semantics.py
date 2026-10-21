"""`event_date` is the event's own date: the publication date never becomes it.

The seven acceptance scenarios end to end — extraction, storage, API, legacy page and the
graph — so no layer can put `published_at` back into an event's date, and so that an event
whose date is unknown stays visible everywhere it is shown.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import FIXED_TIME, DatabaseSeeder

from persons import read as person_read
from processing import read as identity
from processing.extraction.events import RuleBasedEventExtractor
from processing.extraction.models import ExtractionDocument
from processing.storage.models import ArticleExtractionRunRecord, ExtractedEventRecord
from web.app import app
from web.dependencies import get_db

# The publication date of every fixture below. An event dated by it is the defect.
PUBLISHED = datetime(2026, 10, 19, 7, 42, 13, tzinfo=UTC)
EVENT_DAY = datetime(2025, 10, 12, tzinfo=UTC)

ARRESTED_ON = "12 октября 2025 года"
ARRESTED = f"Иванову арестовали {ARRESTED_ON}."
ARRESTED_UNDATED = "Иванову арестовали."
# The seeder places a mention by finding its text in the article, so every fixture article
# names the person in full.
ARRESTED_NAMED = f"Иванова Иванна: {ARRESTED}"
ARRESTED_UNDATED_NAMED = f"Иванова Иванна: {ARRESTED_UNDATED}"
PETROV = "Петров Пётр"


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _dates(text: str, published_at: datetime = PUBLISHED) -> list[datetime | None]:
    document = ExtractionDocument(
        article_id=1,
        title="",
        source_name="",
        source_url="",
        content_hash="",
        text=text,
        published_at=published_at,
    )
    return [event.event_date for event in RuleBasedEventExtractor().extract(document, [])]


def _person_event(
    session: Session,
    seed: DatabaseSeeder,
    *,
    external_id: str,
    text: str,
    span: str,
    event_date: datetime | None,
    published_at: datetime = PUBLISHED,
) -> tuple[int, int]:
    """One article, one person and one event of theirs; the person id and article id."""
    source = seed.source("ОВД-Инфо", "https://ovd.info")
    article_id, run = seed.article(
        source,
        external_id=external_id,
        title="Дело Ивановой",
        text=text,
        published_at=published_at,
    )
    person_id = seed.person("Иванова Иванна")
    mention = seed.mention(run, "Иванова Иванна", person_id=person_id)
    seed.event(
        run,
        span,
        event_type="arrest",
        event_date=event_date,
        links=[(person_id, "target")],
        entity_links=[(mention, "target")],
    )
    return person_id, article_id


# 1. «Иванова задержали 12 октября» — the event's date, with the year written unambiguously.


def test_a_written_date_is_the_event_date() -> None:
    assert _dates(ARRESTED) == [EVENT_DAY]


# 2. «Иванова задержали» — no date in the text, so no date at all.


def test_an_event_whose_text_names_no_date_has_none() -> None:
    assert _dates(ARRESTED_UNDATED) == [None]


# 3. An article published on the 19th about an event of the 12th keeps the 12th.


def test_the_event_date_is_not_the_publication_date(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        _person_event(
            session,
            DatabaseSeeder(session),
            external_id="dated",
            text=ARRESTED_NAMED,
            span=ARRESTED,
            event_date=EVENT_DAY,
        )

    with session_factory() as session:
        event_date, published_at = session.execute(
            text(
                "SELECT e.event_date, a.published_at FROM extracted_events e "
                "JOIN article_extraction_runs r ON r.id = e.extraction_run_id "
                "JOIN parsed_articles a ON a.id = r.article_id"
            )
        ).one()
    assert event_date == EVENT_DAY
    assert event_date != published_at


# 4. Two events, two dates: each keeps its own.


def test_two_events_of_one_article_keep_their_own_dates(
    session_factory: sessionmaker[Session],
) -> None:
    second = f"{PETROV}: Петрову задержали 3 ноября 2025 года."
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("ОВД-Инфо", "https://ovd.info")
        _, run = seed.article(
            source,
            external_id="two-events",
            title="Два события",
            text=f"Иванова Иванна: {ARRESTED} {second}",
            published_at=PUBLISHED,
        )
        ivanova = seed.person("Иванова Иванна")
        petrov = seed.person("Петров Пётр")
        seed.event(
            run,
            ARRESTED,
            event_type="arrest",
            event_date=EVENT_DAY,
            links=[(ivanova, "target")],
            entity_links=[(seed.mention(run, "Иванова Иванна", person_id=ivanova), "target")],
        )
        seed.event(
            run,
            second,
            event_type="detention",
            event_date=datetime(2025, 11, 3, tzinfo=UTC),
            links=[(petrov, "target")],
            entity_links=[(seed.mention(run, "Петров Пётр", person_id=petrov), "target")],
        )

    with session_factory() as session:
        dates: dict[str, datetime | None] = {
            str(event_type): event_date
            for event_type, event_date in session.execute(
                text("SELECT event_type, event_date FROM extracted_events ORDER BY event_type")
            ).all()
        }
    assert dates == {"arrest": EVENT_DAY, "detention": datetime(2025, 11, 3, tzinfo=UTC)}


# 5. Several people and events in one article: a date stays with the event that wrote it.


def test_a_date_is_not_carried_to_the_other_events_of_one_article() -> None:
    assert _dates(f"{ARRESTED} Иванову задержали.") == [EVENT_DAY, None]
    assert _dates(f"Иванову задержали. {ARRESTED}") == [None, EVENT_DAY]


# 6. An unknown date does not make the event disappear from the API or the page.


def test_an_undated_event_is_still_returned(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        person_id, _ = _person_event(
            session,
            DatabaseSeeder(session),
            external_id="undated",
            text=ARRESTED_UNDATED_NAMED,
            span=ARRESTED_UNDATED,
            event_date=None,
        )

    with _client(session_factory) as client:
        events = client.get(f"/api/v1/persons/{person_id}/events").json()
        detail = client.get(f"/persons/{person_id}/detail").json()
        page = client.get(f"/ui/persons/{person_id}")

    assert events != [] and detail["events"] != []
    (event,) = events
    assert event["event_type"] == "arrest"
    # The unknown date is stated as unknown, not invented and not dropped.
    assert event["event_date"] is None
    assert detail["events"][0]["event_date"] is None
    assert page.status_code == 200 and ARRESTED_UNDATED in page.text


# 7. Two sources publishing one event on different days: the publication date must not
#    turn one event into two facts. Both reports write the same event date, so they are
#    clustered by it rather than by when each article came out.


def test_two_publications_of_one_event_are_one_fact(
    session_factory: sessionmaker[Session],
) -> None:
    days = [datetime(2026, 10, 13, tzinfo=UTC), datetime(2026, 10, 19, tzinfo=UTC)]
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("ОВД-Инфо", "https://ovd.info")
        for index, published in enumerate(days):
            _, run = seed.article(
                source,
                external_id=f"report-{index}",
                title="Арест Ивановой",
                text=ARRESTED_NAMED,
                published_at=published,
            )
            person_id = seed.person("Иванова Иванна")
            seed.event(
                run,
                ARRESTED,
                event_type="arrest",
                event_date=EVENT_DAY,
                links=[(person_id, "target")],
                entity_links=[(seed.mention(run, "Иванова Иванна", person_id=person_id), "target")],
            )

    with session_factory() as session:
        publication_days = session.execute(
            text("SELECT count(DISTINCT published_at::date) FROM parsed_articles")
        ).scalar()
        event_dates = session.execute(
            text("SELECT count(DISTINCT event_date) FROM extracted_events")
        ).scalar()
        events = session.execute(text("SELECT count(*) FROM extracted_events")).scalar()

    # Two publication days, two reports of one event, one event date to cluster them by.
    assert publication_days == 2
    assert events == 2 and event_dates == 1


# The two defects the review of PR #71 found after it was merged.


def test_the_latest_news_is_the_newest_publication_not_the_oldest_dated_event(
    session_factory: sessionmaker[Session],
) -> None:
    """A dated event of last year must not outrank today's news whose event the text left
    undated. Ranking on «has a date beats no date» made the customer's latest news a year
    old, and the period filter could then drop a person who had fresh news."""
    old_published = datetime(2025, 1, 2, tzinfo=UTC)
    fresh_published = datetime(2026, 10, 19, tzinfo=UTC)
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("ОВД-Инфо", "https://ovd.info")
        person_id = seed.person("Иванова Иванна")

        _, old_run = seed.article(
            source,
            external_id="old",
            title="Арест 2025 года",
            text="Иванова Иванна: Иванову арестовали.",
            published_at=old_published,
        )
        seed.event(
            old_run,
            "Иванову арестовали.",
            event_type="arrest",
            event_date=datetime(2025, 1, 1, tzinfo=UTC),
            links=[(person_id, "target")],
            entity_links=[(seed.mention(old_run, "Иванова Иванна", person_id=person_id), "target")],
        )

        _, fresh_run = seed.article(
            source,
            external_id="fresh",
            title="Свежая новость",
            text="Иванова Иванна: Иванову задержали.",
            published_at=fresh_published,
        )
        seed.event(
            fresh_run,
            "Иванову задержали.",
            event_type="detention",
            event_date=None,
            links=[(person_id, "target")],
            entity_links=[
                (
                    seed.mention(fresh_run, "Иванова Иванна", person_id=person_id),
                    "target",
                )
            ],
        )

    with session_factory() as session:
        latest = person_read.latest_events(session, [person_id])

    event = latest[person_id]
    assert event.event_type == "detention"
    # Placed by its publication, dated by nothing: the two are not merged.
    assert event.event_date is None


def test_event_counts_reads_only_the_latest_run_of_an_article(
    session_factory: sessionmaker[Session],
) -> None:
    """A re-extraction opens a new run and keeps the old one. Counting every run counted one
    arrest twice in «Публикации» and kept counting events a later extractor no longer
    finds. On the working database this overstated the count by 7829."""
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("ОВД-Инфо", "https://ovd.info")
        article_id, first = seed.article(
            source,
            external_id="run-one",
            title="Первый прогон",
            text="Иванову арестовали.",
            published_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        seed.event(first, "Иванову арестовали.", event_type="arrest", event_date=None, links=[])
        # A second successful run of the same article, as an extractor bump leaves it.
        second = ArticleExtractionRunRecord(
            article_id=article_id,
            article_content_hash="hash-run-two",
            extractor_name="rule-based",
            extractor_version="1.12.0",
            normalizer_version="1.0.0",
            status="succeeded",
            started_at=FIXED_TIME,
            finished_at=FIXED_TIME,
        )
        session.add(second)
        session.flush()
        session.add(
            ExtractedEventRecord(
                extraction_run_id=second.id,
                event_type="arrest",
                event_date=None,
                start_offset=0,
                end_offset=5,
                confidence=0.9,
                attributes={},
                extractor_name="rule-based",
                extractor_version="1.12.0",
            )
        )

    with session_factory() as session:
        counts = identity.event_counts(session, [article_id])

    assert counts == [(article_id, "arrest", 1)]


def _article_with_two_runs(
    session: Session,
    seed: DatabaseSeeder,
    *,
    source_id: int,
    external_id: str,
    old_event_type: str,
    old_event_date: datetime | None,
    new_event_type: str,
    new_event_date: datetime | None,
) -> tuple[int, int, int]:
    """One article, two successful runs of it, each with an event of its own.

    A re-extraction opens a new run and keeps the old one, so both events stay in the
    table. The article id and the ids of the two events are returned, the old event's
    first.
    """
    article_id, first = seed.article(
        source_id,
        external_id=external_id,
        title="Первый прогон",
        text="Иванову арестовали.",
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    old_event_id = seed.event(
        first,
        "Иванову арестовали.",
        event_type=old_event_type,
        event_date=old_event_date,
        links=[],
    )
    second = ArticleExtractionRunRecord(
        article_id=article_id,
        article_content_hash="hash-second-run",
        extractor_name="rule-based",
        extractor_version="1.12.0",
        normalizer_version="1.0.0",
        status="succeeded",
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
    )
    session.add(second)
    session.flush()
    new_event = ExtractedEventRecord(
        extraction_run_id=second.id,
        event_type=new_event_type,
        event_date=new_event_date,
        start_offset=0,
        end_offset=5,
        confidence=0.9,
        attributes={},
        extractor_name="rule-based",
        extractor_version="1.12.0",
    )
    session.add(new_event)
    session.flush()
    return article_id, old_event_id, new_event.id


def test_articles_with_events_reads_only_the_latest_run_of_an_article(
    session_factory: sessionmaker[Session],
) -> None:
    """An article whose latest extraction finds no arrest is not an arrest article.

    Reading every successful run left the article of a case the newer extractor no longer
    finds, and the unnamed-figurants stage then asked the model about it. The article
    whose latest run does find an arrest is still there: the read is narrowed, not
    emptied.
    """
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("ОВД-Инфо", "https://ovd.info")
        stale_article, _, _ = _article_with_two_runs(
            session,
            seed,
            source_id=source,
            external_id="articles-with-events-stale",
            old_event_type="arrest",
            old_event_date=None,
            new_event_type="detention",
            new_event_date=None,
        )
        fresh_article, _, _ = _article_with_two_runs(
            session,
            seed,
            source_id=source,
            external_id="articles-with-events-fresh",
            old_event_type="arrest",
            old_event_date=None,
            new_event_type="arrest",
            new_event_date=None,
        )

    with session_factory() as session:
        articles = identity.articles_with_events(session, {"arrest"})

    # The article whose latest run finds no arrest is out; the one that does is in.
    assert stale_article not in articles
    assert fresh_article in articles


def test_events_undated_reads_only_the_latest_run_of_an_article(
    session_factory: sessionmaker[Session],
) -> None:
    """The graph's reports of one event come from the latest run of every article.

    Reading every run showed one arrest twice, once as the old run's report and once as
    the new one's.
    """
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("ОВД-Инфо", "https://ovd.info")
        article_id, old_event_id, new_event_id = _article_with_two_runs(
            session,
            seed,
            source_id=source,
            external_id="events-undated",
            old_event_type="arrest",
            old_event_date=None,
            new_event_type="arrest",
            new_event_date=None,
        )

    with session_factory() as session:
        events = identity.events_undated(session, [article_id], {"arrest"})

    assert events == [new_event_id]
    assert old_event_id not in events


def test_events_dated_reads_only_the_latest_run_of_an_article(
    session_factory: sessionmaker[Session],
) -> None:
    """The other reports of one event are the latest runs' too.

    The old run's arrest wrote the same day as the new one, so the graph matched it as
    another report of that event.
    """
    day = date(2025, 1, 1)
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("ОВД-Инфо", "https://ovd.info")
        _, old_event_id, new_event_id = _article_with_two_runs(
            session,
            seed,
            source_id=source,
            external_id="events-dated",
            old_event_type="arrest",
            old_event_date=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
            new_event_type="arrest",
            new_event_date=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        )

    with session_factory() as session:
        events = identity.events_dated(session, {("arrest", day)})

    assert events == [new_event_id]
    assert old_event_id not in events


def test_the_storage_of_the_two_pins() -> None:
    """Both remaining findings of the review, kept together with the ones above."""
    assert RuleBasedEventExtractor.extractor_version == "1.12.0"


def test_the_timeline_says_which_day_it_shows(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        _person_event(
            session,
            DatabaseSeeder(session),
            external_id="labelled",
            text=ARRESTED_NAMED,
            span=ARRESTED,
            event_date=EVENT_DAY,
        )

    with session_factory() as session:
        dated = session.execute(
            text(
                "SELECT e.event_date IS NOT NULL, e.event_date = a.published_at "
                "FROM extracted_events e "
                "JOIN article_extraction_runs r ON r.id = e.extraction_run_id "
                "JOIN parsed_articles a ON a.id = r.article_id"
            )
        ).one()
    # The event has a date, and it is not the publication's — so the page shows it as the
    # event's own rather than as «по дате публикации».
    assert dated == (True, False)
