"""What a political case's latest news is — on PostgreSQL, with a stand-in model."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import (
    EntityGroupNewsRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityMentionRecord,
)
from entities.collector import EntityCollector
from entities.news import NewsAnswer, NewsFinder, NewsItem, NewsReaderError


def _person(
    session: Session, seed: ResearchSeeder, run: int, surface: str, first: str, last: str
) -> None:
    mention = seed.mention(run, surface, person_id=None)
    session.get_one(EntityMentionRecord, mention).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": None,
    }


def _seed(session_factory: sessionmaker[Session]) -> None:
    """Моор: arrested in 2023, his arrest extended now. Петров: sentenced now. Сидоров:
    a common criminal — no verdict «political», never asked."""
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for external_id, day, text_, surface, first, last in (
            (
                "moor-old",
                datetime(2026, 9, 20, tzinfo=UTC),
                "Александра Моора арестовали по делу о постах.",
                "Александра Моора",
                "Александр",
                "Моор",
            ),
            (
                "moor-new",
                datetime(2026, 9, 24, tzinfo=UTC),
                "Суд продлил арест Александру Моору, задержанному в 2023 году.",
                "Александру Моору",
                "Александр",
                "Моор",
            ),
            (
                "petrov",
                datetime(2026, 9, 23, tzinfo=UTC),
                "Ивана Петрова приговорили к шести годам колонии.",
                "Ивана Петрова",
                "Иван",
                "Петров",
            ),
            (
                "sidorov",
                datetime(2026, 9, 22, tzinfo=UTC),
                "Петра Сидорова арестовали за кражу.",
                "Петра Сидорова",
                "Петр",
                "Сидоров",
            ),
        ):
            _, run = seed.article(
                source, external_id=external_id, title=external_id, text=text_, published_at=day
            )
            _person(session, seed, run, surface, first, last)
            seed.event(run, text_[:10], event_type="arrest", event_date=None, links=[])
        session.commit()
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        for group_id, name in session.execute(select(EntityGroupRecord.id, EntityGroupRecord.name)):
            session.add(
                EntityGroupPoliticsRecord(
                    group_id=group_id,
                    verdict="criminal" if "Сидоров" in name else "political",
                    method="model",
                    reason="",
                    quote="",
                )
            )


KINDS = {"Александр Моор": "ongoing", "Иван Петров": "sentence"}


class FakeReader:
    model = "fake-model"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.asked: list[NewsItem] = []

    def classify(self, items: Sequence[NewsItem]) -> dict[int, NewsAnswer]:
        self.asked += items
        if self.fail:
            raise NewsReaderError("provider down")
        return {
            item.id: NewsAnswer(
                id=item.id, source=item.name, kind=KINDS[item.name], explanation="так в тексте"
            )  # type: ignore[arg-type]
            for item in items
        }


def _kinds(session_factory: sessionmaker[Session]) -> dict[str, str]:
    with session_factory() as session:
        return dict(
            session.execute(
                select(EntityGroupRecord.name, EntityGroupNewsRecord.kind).join(
                    EntityGroupNewsRecord, EntityGroupNewsRecord.group_id == EntityGroupRecord.id
                )
            ).all()
        )


def test_the_political_cases_latest_news_is_named_by_the_latest_quotes(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    reader = FakeReader()

    result = NewsFinder(session_factory, reader=reader).run()

    # Only the political: Сидоров's theft is not asked about.
    assert sorted(item.name for item in reader.asked) == ["Александр Моор", "Иван Петров"]
    moor = next(item for item in reader.asked if item.name == "Александр Моор")
    # The latest first, each with its date: the text tells the old case.
    assert [quote.published_at for quote in moor.quotes] == [
        datetime(2026, 9, 24, tzinfo=UTC),
        datetime(2026, 9, 20, tzinfo=UTC),
    ]
    assert "задержанному в 2023 году" in moor.quotes[0].text
    assert _kinds(session_factory) == {"Александр Моор": "ongoing", "Иван Петров": "sentence"}
    assert (result.cases, result.sentence, result.ongoing, result.asked_now) == (2, 1, 1, 2)
    with session_factory() as session:
        quote, published = session.execute(
            text(
                "SELECT n.quote, n.published_at FROM entity_group_news n "
                "JOIN entity_groups g ON g.id = n.group_id WHERE g.name = 'Александр Моор'"
            )
        ).one()
    assert "продлил арест" in quote and published == datetime(2026, 9, 24, tzinfo=UTC)


def test_the_same_quotes_are_answered_from_the_cache(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    NewsFinder(session_factory, reader=FakeReader()).run()
    again = FakeReader()

    result = NewsFinder(session_factory, reader=again).run()

    assert again.asked == [] and result.cached == 2
    assert _kinds(session_factory)["Иван Петров"] == "sentence"


def test_without_an_answer_the_news_is_unknown_and_asked_again(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    failed = NewsFinder(session_factory, reader=FakeReader(fail=True)).run()
    unknown = _kinds(session_factory)
    retry = FakeReader()
    NewsFinder(session_factory, reader=retry).run()

    assert (failed.unknown, failed.failures) == (2, 2)
    assert set(unknown.values()) == {"unknown"}
    assert len(retry.asked) == 2
