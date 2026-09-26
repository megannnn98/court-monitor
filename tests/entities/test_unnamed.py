"""Unnamed figurants: the sentences that describe a person by age, a model's reading of
them, and who on the Rosfinmonitoring list they may be — on PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import (
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    UnnamedFigurantRecord,
)
from entities.unnamed import (
    DIFFERENT,
    NONE,
    SAME,
    UnnamedAnswer,
    UnnamedFinder,
    UnnamedItem,
    UnnamedReaderError,
    candidates,
    common_crime_only,
    decide,
    described_sentences,
)

TYUMEN = (
    "В Тюмени задержан 17-летний житель города по делу о теракте на железной дороге. "
    "Его обвиняют по ст. 205 УК РФ."
)
VICTIM = "На 40-летнюю жительницу Кушвы напал сосед. Возбуждено уголовное дело об убийстве."
NAMED = "Суд арестовал 35-летнего жителя Москвы Ивана Петрова по ст. 280.3 УК РФ."


def test_the_sentences_that_describe_a_person_by_age_are_picked() -> None:
    text_ = f"Заголовок. {TYUMEN} Прочее без возраста. Подросток из Омска арестован."

    found = described_sentences(1, text_, None)

    assert [sentence.text for sentence in found] == [
        "В Тюмени задержан 17-летний житель города по делу о теракте на железной дороге.",
        "Подросток из Омска арестован.",
    ]
    # The offsets are the sentence's in the whole text; the context is around it.
    first = found[0]
    assert text_[first.start : first.end] == first.text
    assert "Его обвиняют по ст. 205" in first.context


class FakeReader:
    model = "fake-model"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.asked: list[UnnamedItem] = []

    def classify(self, items: Sequence[UnnamedItem]) -> dict[int, UnnamedAnswer]:
        self.asked += items
        if self.fail:
            raise UnnamedReaderError("provider down")
        answers = {}
        for item in items:
            victim = "Кушвы" in item.sentence
            named = "Петрова" in item.context
            theft = "кражу" in item.sentence
            answers[item.id] = UnnamedAnswer(
                id=item.id,
                is_case=not victim,
                named=named,
                age=40 if victim else 35 if named else 17,
                gender="male",
                place="Тюмень",
                initial="",
                articles=["150", "158"] if theft else ["205"],
                event="detention",
                explanation="так в тексте",
            )
        return answers


def _seed(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for external_id, text_ in (("tyumen", TYUMEN), ("victim", VICTIM), ("named", NAMED)):
            _, run = seed.article(
                source,
                external_id=external_id,
                title=external_id,
                text=text_,
                published_at=datetime(2024, 11, 25, tzinfo=UTC),
            )
            seed.event(run, text_[:10], event_type="detention", event_date=None, links=[])
        # No criminal case: never read.
        _, run = seed.article(
            source,
            external_id="fine",
            title="fine",
            text="18-летнего жителя Омска оштрафовали за пост.",
        )
        seed.event(run, "оштрафовали", event_type="fine", event_date=None, links=[])
        session.commit()


def test_only_a_russian_case_against_an_unnamed_person_is_kept(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    reader = FakeReader()

    result = UnnamedFinder(session_factory, reader=reader).run()
    again = FakeReader()
    cached = UnnamedFinder(session_factory, reader=again).run()

    # The fined one is no criminal case: not read.
    assert sorted(item.sentence[:12] for item in reader.asked) == [
        "В Тюмени зад",
        "На 40-летнюю",
        "Суд арестова",
    ]
    assert (result.sentences, result.unnamed, result.named, result.not_cases) == (3, 1, 1, 1)
    with session_factory() as session:
        [figurant] = session.scalars(select(UnnamedFigurantRecord)).all()
    assert (figurant.age, figurant.gender, figurant.place) == (17, "male", "Тюмень")
    assert figurant.articles == ["205"] and figurant.event_type == "detention"
    assert figurant.quote.startswith("В Тюмени задержан 17-летний житель")
    # Asked once: the same sentences again are the cache's.
    assert again.asked == [] and cached.cached == 3 and cached.unnamed == 1


def test_a_failed_model_keeps_nobody_and_asks_again(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    failed = UnnamedFinder(session_factory, reader=FakeReader(fail=True)).run()
    retry = FakeReader()
    UnnamedFinder(session_factory, reader=retry).run()

    assert (failed.failures, failed.unnamed) == (3, 0)
    assert len(retry.asked) == 3


def _list(session_factory: sessionmaker[Session]) -> None:
    """The operator's example: Пуртов, 17.02.2007, Тюмень — and others who are not him."""
    with session_factory.begin() as session:
        first = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime(2024, 11, 1, tzinfo=UTC),
            source_url="https://fedsfm.test",
            content_hash="a",
            entry_count=1,
            fetched_at=datetime(2024, 11, 1, tzinfo=UTC),
        )
        latest = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime(2024, 12, 1, tzinfo=UTC),
            source_url="https://fedsfm.test",
            content_hash="b",
            entry_count=4,
            fetched_at=datetime(2024, 12, 1, tzinfo=UTC),
        )
        session.add_all([first, latest])
        session.flush()
        people = (
            # Born in Tyumen, 17 on the day of the news: the one.
            (
                "ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ",
                datetime(2007, 2, 17, tzinfo=UTC),
                "Г. ТЮМЕНЬ ТЮМЕНСКОЙ ОБЛАСТИ",
            ),
            # 17 too, born elsewhere.
            ("СИДОРОВ ПЁТР ИВАНОВИЧ", datetime(2007, 5, 1, tzinfo=UTC), "Г. ОМСК"),
            # A girl of 17 from Tyumen: not «житель».
            ("ИВАНОВА АННА ПЕТРОВНА", datetime(2007, 3, 3, tzinfo=UTC), "Г. ТЮМЕНЬ"),
            # From Tyumen, but 30.
            ("КОЗЛОВ ИВАН ИВАНОВИЧ", datetime(1994, 1, 1, tzinfo=UTC), "Г. ТЮМЕНЬ"),
        )
        for snapshot, entries in ((latest, people), (first, people[1:2])):
            for full_name, born, place in entries:
                session.add(
                    RosfinmonitoringEntryRecord(
                        snapshot_id=snapshot.id,
                        full_name=full_name,
                        normalized_name=full_name.lower(),
                        matching_key=full_name.lower().replace(" ", ""),
                        birth_date=born,
                        birth_place=place,
                    )
                )


def test_the_list_gives_the_candidates_of_that_age_sex_and_place(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    _list(session_factory)
    UnnamedFinder(session_factory, reader=FakeReader()).run()

    with session_factory() as session:
        figurant = session.scalars(select(UnnamedFigurantRecord)).one()
        found = candidates(session, figurant)

    # Of age and sex there are two; the one born in Tyumen is shown, and why.
    assert found.total == 2
    [purtov] = found.shown
    assert purtov.full_name == "ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ"
    assert purtov.place_match and purtov.age == 17
    assert "родился: Г. ТЮМЕНЬ ТЮМЕНСКОЙ ОБЛАСТИ" in purtov.reasons
    # He first appeared in the latest snapshot: the earliest we know of him.
    assert purtov.first_seen == datetime(2024, 12, 1, tzinfo=UTC)


def test_an_initial_narrows_the_list_without_a_place(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    _list(session_factory)
    UnnamedFinder(session_factory, reader=FakeReader()).run()
    with session_factory.begin() as session:
        session.execute(text("UPDATE unnamed_figurants SET place = '', initial = 'С'"))

    with session_factory() as session:
        figurant = session.scalars(select(UnnamedFigurantRecord)).one()
        found = candidates(session, figurant)

    assert [item.full_name for item in found.shown] == ["СИДОРОВ ПЁТР ИВАНОВИЧ"]
    assert found.shown[0].first_seen == datetime(2024, 11, 1, tzinfo=UTC)


def test_a_person_s_word_is_kept_and_orders_the_candidates(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    _list(session_factory)
    UnnamedFinder(session_factory, reader=FakeReader()).run()
    with session_factory() as session:
        figurant = session.scalars(select(UnnamedFigurantRecord)).one()
        purtov = candidates(session, figurant).shown[0]

    with session_factory.begin() as session:
        decide(session, figurant.key, purtov.key, DIFFERENT)
        decide(session, figurant.key, purtov.key, SAME)
        decide(session, figurant.key, "whatever", NONE)
    # A new search keeps them: they hold to the keys.
    UnnamedFinder(session_factory, reader=FakeReader()).run()

    with session_factory() as session:
        figurant = session.scalars(select(UnnamedFigurantRecord)).one()
        found = candidates(session, figurant)
        decisions = session.execute(
            text("SELECT candidate, decision FROM unnamed_decisions ORDER BY candidate")
        ).all()

    assert found.shown[0].decision == SAME
    assert decisions == [("", NONE), (purtov.key, SAME)]


def test_a_case_of_common_crime_only_is_no_unnamed_figurant(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for external_id, text_ in (
            ("tyumen", TYUMEN),
            ("theft", "Задержан 19-летний житель Тулы, втянувший подростка в кражу."),
        ):
            _, run = seed.article(
                source,
                external_id=external_id,
                title=external_id,
                text=text_,
                published_at=datetime(2024, 11, 25, tzinfo=UTC),
            )
            seed.event(run, text_[:10], event_type="detention", event_date=None, links=[])
        session.commit()

    result = UnnamedFinder(session_factory, reader=FakeReader()).run()

    with session_factory() as session:
        quotes = session.scalars(select(UnnamedFigurantRecord.quote)).all()
    assert (result.unnamed, result.common_crime) == (1, 1)
    assert [quote[:17] for quote in quotes] == ["В Тюмени задержан"]


def test_common_crime_only_is_every_article_common_none_political() -> None:
    assert common_crime_only(["150", "158"])
    # An attempt says nothing of the crime.
    assert common_crime_only(["105", "30"])
    # One political or terrorist article, or one not known as common: kept.
    assert not common_crime_only(["158", "205"])
    assert not common_crime_only(["167"])
    assert not common_crime_only(["30"])
    # Nothing told: kept.
    assert not common_crime_only([])
