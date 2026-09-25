"""Rebuilding the person entities from the articles with a criminal case, on PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import EntityGroupMentionRecord, EntityGroupRecord, EntityMentionRecord
from entities.collector import EntityCollector
from entities.normalizer import NameItem, NameNormalizerError, NormalizedName


def _person(
    session: Session, seed: ResearchSeeder, run: int, surface: str, first: str | None, last: str
) -> int:
    mention_id = seed.mention(run, surface, person_id=None)
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": None,
    }
    return mention_id


def _seed(session_factory: sessionmaker[Session]) -> dict[str, int]:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        _, arrest = seed.article(
            source,
            external_id="arrest",
            title="Арест",
            text="Суд арестовал Александра Моора.",
            published_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        arrested = _person(session, seed, arrest, "Александра Моора", "Александр", "Моора")
        seed.event(
            arrest,
            "Суд арестовал",
            event_type="arrest",
            event_date=None,
            links=[],
            entity_links=[(arrested, "subject")],
        )
        _, sentence = seed.article(
            source,
            external_id="sentence",
            title="Приговор",
            text="Александру Моору вынесли приговор. Моор не признал вину.",
            published_at=datetime(2026, 9, 10, tzinfo=UTC),
        )
        _person(session, seed, sentence, "Александру Моору", "Александр", "Моору")
        bare = _person(session, seed, sentence, "Моор", None, "Моор")
        seed.event(sentence, "вынесли приговор", event_type="sentence", event_date=None, links=[])
        _, junk = seed.article(
            source,
            external_id="junk",
            title="Выставка",
            text="Александр Моор открыл выставку.",
            published_at=datetime(2026, 9, 20, tzinfo=UTC),
        )
        junk_mention = _person(session, seed, junk, "Александр Моор", "Александр", "Моор")
        # A fine is an event, but no criminal case: the article stays out.
        seed.event(junk, "открыл выставку", event_type="fine", event_date=None, links=[])
        session.commit()
        return {"arrested": arrested, "bare": bare, "junk": junk_mention}


def test_entities_are_rebuilt_from_the_articles_with_a_criminal_case(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    stages: list[str] = []

    result = EntityCollector(session_factory, on_stage=stages.append).run()
    again = EntityCollector(session_factory).run()

    assert (result.mentions, result.entities, result.grouped) == (3, 1, 3)
    assert stages == ["reading", "grouping", "writing", "charges"]
    assert again == result  # a rebuild, not an append
    with session_factory() as session:
        [entity] = session.scalars(select(EntityGroupRecord)).all()
        assert (entity.key, entity.name) == ("александр моор", "Александр Моор")
        assert (entity.mention_count, entity.article_count) == (3, 2)
        assert entity.event_types == {"arrest": 1}
        assert entity.last_published_at == datetime(2026, 9, 10, tzinfo=UTC)
        linked = set(session.scalars(select(EntityGroupMentionRecord.mention_id)).all())
        assert ids["bare"] in linked and ids["arrested"] in linked
        # A mention in an article without a criminal case is no evidence.
        assert ids["junk"] not in linked


class FakeNormalizer:
    """Names every entity by a fixed table; counts the calls, fails on demand."""

    model = "fake-model"

    def __init__(self, table: dict[str, str], *, fail: bool = False) -> None:
        self.table = table
        self.fail = fail
        self.asked: list[tuple[str, ...]] = []

    def normalize(self, items: Sequence[NameItem]) -> dict[int, NormalizedName]:
        self.asked += [item.forms for item in items]
        if self.fail:
            raise NameNormalizerError("provider down")
        return {
            item.id: NormalizedName(
                id=item.id,
                source=item.forms[0],
                nominative=self.table[item.forms[0]],
                gender="male",
                is_person=True,
            )
            for item in items
        }


def _seed_declined_and_nominative(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for external_id, text_, surface, first, last in (
            ("a", "Суд арестовал Александра Моора.", "Александра Моора", "Александра", "Моора"),
            # Surname first, no patronymic: which is which is the model's to say.
            ("b", "Моор Александр получил срок.", "Моор Александр", "Александр", "Моор"),
        ):
            _, run = seed.article(source, external_id=external_id, title=external_id, text=text_)
            _person(session, seed, run, surface, first, last)
            seed.event(run, text_[:5], event_type="arrest", event_date=None, links=[])
        session.commit()


def test_model_names_merge_entities_and_are_asked_once(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_declined_and_nominative(session_factory)
    table = {"Александра Моора": "Александр Моор", "Моор Александр": "Александр Моор"}
    first_model = FakeNormalizer(table)
    second_model = FakeNormalizer(table)

    first = EntityCollector(session_factory, normalizer=first_model).run()
    again = EntityCollector(session_factory, normalizer=second_model).run()

    # The rules kept «Александра Моора» apart; the model's name merges it.
    assert (first.entities, first.normalized_now, first.normalized_cached) == (1, 2, 0)
    assert len(first_model.asked) == 2
    assert (again.normalized_now, again.normalized_cached, second_model.asked) == (0, 2, [])
    with session_factory() as session:
        [entity] = session.scalars(select(EntityGroupRecord)).all()
        assert (entity.name, entity.gender, entity.name_source) == (
            "Александр Моор",
            "male",
            "model",
        )
        assert entity.mention_count == 2


def test_a_failed_batch_keeps_the_rule_names_and_asks_again_next_time(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_declined_and_nominative(session_factory)
    down = FakeNormalizer({}, fail=True)

    result = EntityCollector(session_factory, normalizer=down).run()

    assert (result.entities, result.normalize_failures, result.normalized_now) == (2, 2, 0)
    with session_factory() as session:
        sources = set(session.scalars(select(EntityGroupRecord.name_source)).all())
        assert sources == {"rules"}


def test_many_batches_are_asked_in_parallel_and_all_kept(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    import entities.collector

    monkeypatch.setattr(entities.collector, "BATCH_SIZE", 1)
    _seed_declined_and_nominative(session_factory)
    model = FakeNormalizer(
        {"Александра Моора": "Александр Моор", "Моор Александр": "Александр Моор"}
    )
    stages: list[str] = []

    result = EntityCollector(session_factory, normalizer=model, on_stage=stages.append).run()

    assert (result.normalized_now, result.normalize_failures) == (2, 0)
    assert len(model.asked) == 2  # one entity per batch
    assert [stage for stage in stages if stage.startswith("normalizing")] == [
        "normalizing 1/2",
        "normalizing 2/2",
    ]


def test_a_registry_card_gives_its_region_and_keeps_its_namesake_apart(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("memopzk", "https://memopzk.example.test")
        card = (
            "Бондаренко Николай Викторович.\nРегион: Луганская область.\n"
            "Бондаренко Николай Викторович осужден по статьям: ст. 299 УК РФ."
        )
        _, run = seed.article(source, external_id="card", title="Бондаренко", text=card)
        mention_id = seed.mention(run, "Бондаренко Николай Викторович осужден", person_id=None)
        session.get_one(EntityMentionRecord, mention_id).normalized_data = {
            "first_name": "Николай",
            "last_name": "Бондаренко",
            "patronymic": "Викторович",
        }
        seed.event(run, "осужден", event_type="sentence", event_date=None, links=[])
        news = "В Саратове задержали депутата Николая Бондаренко."
        _, run = seed.article(source, external_id="news", title="Задержание", text=news)
        _person(session, seed, run, "Николая Бондаренко", "Николай", "Бондаренко")
        seed.event(run, "задержали", event_type="arrest", event_date=None, links=[])
        session.commit()

    EntityCollector(session_factory).run()

    with session_factory() as session:
        entities = {
            entity.name: entity.regions for entity in session.scalars(select(EntityGroupRecord))
        }
    assert entities == {
        "Николай Викторович Бондаренко": [["Луганская область", 1]],
        "Николай Бондаренко": [],
    }


def test_a_name_is_kept_by_its_forms_and_an_older_answer_by_key_still_counts(
    session_factory: sessionmaker[Session],
) -> None:
    from sqlalchemy import text

    from db.orm_models import EntityNameNormalizationRecord
    from entities.normalizer import PROMPT_VERSION

    _seed_declined_and_nominative(session_factory)
    table = {"Александра Моора": "Александр Моор", "Моор Александр": "Александр Моор"}
    EntityCollector(session_factory, normalizer=FakeNormalizer(table)).run()
    with session_factory() as session:
        keys = set(session.scalars(select(EntityNameNormalizationRecord.key)))
    # Kept by the forms it named: a rebuild that changes the entity keys asks nothing.
    assert keys and all(key.startswith("forms:") for key in keys)

    # An older answer kept by the rule key of the entity is still an answer.
    with session_factory.begin() as session:
        session.execute(text("DELETE FROM entity_name_normalizations"))
        session.add(
            EntityNameNormalizationRecord(
                key="александр моор",
                prompt_version=PROMPT_VERSION,
                model="old",
                nominative="Александр Моор",
                gender="male",
                is_person=True,
            )
        )
    again = FakeNormalizer(table)

    result = EntityCollector(session_factory, normalizer=again).run()

    assert again.asked == [("Александра Моора",)]
    assert result.normalized_cached == 1


def test_a_single_nominative_form_is_named_without_the_model(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        text_ = "Суд арестовал. Анна Олеговна Смирнова не признала вину."
        _, run = seed.article(source, external_id="a", title="a", text=text_)
        mention_id = seed.mention(run, "Анна Олеговна Смирнова", person_id=None)
        session.get_one(EntityMentionRecord, mention_id).normalized_data = {
            "first_name": "Анна",
            "last_name": "Смирнова",
            "patronymic": "Олеговна",
        }
        seed.event(run, "арестовал", event_type="arrest", event_date=None, links=[])
        session.commit()
    model = FakeNormalizer({})

    result = EntityCollector(session_factory, normalizer=model).run()

    assert model.asked == [] and result.normalized_now == 0
    with session_factory() as session:
        entity = session.scalars(select(EntityGroupRecord)).one()
        assert (entity.name, entity.gender, entity.name_source) == (
            "Анна Олеговна Смирнова",
            "female",
            "rules",
        )


def test_a_relative_is_no_entity_and_a_swapped_name_is_one(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        _, run = seed.article(
            source,
            external_id="digest",
            title="Приговор",
            text=(
                "Владимира Гульчака приговорили. Жене Владимира отказали. "
                "Алексис Дрион и Дрион Алексис."
            ),
            published_at=datetime(2026, 9, 21, tzinfo=UTC),
        )
        _person(session, seed, run, "Владимира Гульчака", "Владимир", "Гульчак")
        _person(session, seed, run, "Жене Владимира", "Женя", "Владимир")
        _person(session, seed, run, "Алексис Дрион", "Алексис", "Дрион")
        _person(session, seed, run, "Дрион Алексис", "Дрион", "Алексис")
        seed.event(run, "приговорили", event_type="sentence", event_date=None, links=[])
        session.commit()

    EntityCollector(session_factory).run()

    with session_factory() as session:
        names = sorted(session.scalars(select(EntityGroupRecord.name)).all())
    assert names == ["Алексис Дрион", "Владимир Гульчак"]
