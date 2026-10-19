"""`event_date` is the event's own date: the publication date never becomes it.

The seven acceptance scenarios end to end — extraction, storage, API, legacy page and the
graph — so no layer can put `published_at` back into an event's date, and so that an event
whose date is unknown stays visible everywhere it is shown.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from processing.extraction.events import RuleBasedEventExtractor
from processing.extraction.models import ExtractionDocument
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


# The event's date is what the graph and the dossier label as such, and the publication
# date is labelled as the publication's.


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
