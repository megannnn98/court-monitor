"""Who a criminal case is opened against among the entities, on PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import EntityGroupRecord, EntityGroupRoleRecord, EntityMentionRecord
from entities.collector import EntityCollector
from entities.rf_check import EntityRfCheck
from entities.roles import (
    FigurantFinder,
    RoleAnswer,
    RoleClassifierError,
    RoleItem,
    matched_answers,
    role_of,
)

RF_PAGE = """<!doctype html><html>
<div class="panel-heading"><h4>Физические лица</h4></div>
<div class="panel-body"><ol><li>1. ОРЛОВ ОЛЕГ ИВАНОВИЧ*, 01.01.1980 г.р. , Г. ТВЕРЬ;</li></ol></div>
</html>""".encode()


def _person(session: Session, seed: ResearchSeeder, run: int, surface: str, name: str) -> int:
    first, last = name.split()
    mention_id = seed.mention(run, surface, person_id=None)
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": None,
    }
    return mention_id


def _seed(session_factory: sessionmaker[Session]) -> None:
    """Петров: charged by an article (the rules). Беда and Сирош: a sentence the rules
    cannot read. Орлов: on the Rosfinmonitoring list."""
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        text_ = "Суд арестовал Ивана Петрова по ч. 2 ст. 205.2 УК РФ."
        _, run = seed.article(source, external_id="petrov", title="Арест", text=text_)
        petrov = _person(session, seed, run, "Ивана Петрова", "Иван Петров")
        law = seed.mention(
            run, "ч. 2 ст. 205.2 УК РФ", person_id=None, entity_type="legal_reference"
        )
        session.get_one(EntityMentionRecord, law).normalized_data = {
            "code": "УК РФ",
            "article": "205.2",
            "part": "2",
            "clause": None,
        }
        seed.event(
            run,
            text_,
            event_type="arrest",
            event_date=None,
            links=[],
            entity_links=[(petrov, "target"), (law, "legal_basis")],
        )
        text_ = "Суд признал Александра Беду виновным. Адвокат Фёдор Сирош обжалует приговор."
        _, run = seed.article(source, external_id="beda", title="Приговор", text=text_)
        _person(session, seed, run, "Александра Беду", "Александр Беда")
        _person(session, seed, run, "Фёдор Сирош", "Фёдор Сирош")
        seed.event(run, "приговор", event_type="sentence", event_date=None, links=[])
        text_ = "Суд арестовал Олега Орлова."
        _, run = seed.article(source, external_id="orlov", title="Арест", text=text_)
        _person(session, seed, run, "Олега Орлова", "Олег Орлов")
        seed.event(run, "арестовал", event_type="arrest", event_date=None, links=[])
        session.commit()
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE entity_groups SET name = 'Олег Иванович Орлов' WHERE name = 'Олег Орлов'")
        )
    EntityRfCheck(session_factory, download=lambda: RF_PAGE).run()


class FakeClassifier:
    model = "fake-model"

    def __init__(self, kinds: dict[str, str], *, fail: bool = False) -> None:
        self.kinds = kinds
        self.fail = fail
        self.asked: list[RoleItem] = []

    def classify(self, items: Sequence[RoleItem]) -> dict[int, RoleAnswer]:
        self.asked += items
        if self.fail:
            raise RoleClassifierError("provider down")
        return {
            item.id: RoleAnswer(
                id=item.id,
                source=item.name,
                kind=self.kinds[item.name],  # type: ignore[arg-type]
                explanation=f"так сказано про {item.name}",
            )
            for item in items
        }


KINDS = {"Иван Петров": "accused", "Александр Беда": "accused", "Фёдор Сирош": "lawyer"}


def _roles(session_factory: sessionmaker[Session]) -> dict[str, tuple[str, str | None, str]]:
    with session_factory() as session:
        rows = session.execute(
            select(
                EntityGroupRecord.name,
                EntityGroupRoleRecord.role,
                EntityGroupRoleRecord.kind,
                EntityGroupRoleRecord.method,
            ).join(EntityGroupRecord, EntityGroupRecord.id == EntityGroupRoleRecord.group_id)
        ).all()
    return {name: (role, kind, method) for name, role, kind, method in rows}


def test_the_rules_settle_the_charged_and_a_model_reads_the_rest(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    classifier = FakeClassifier(KINDS)

    result = FigurantFinder(session_factory, classifier=classifier).run()

    # Орлов is on the list: not asked about. Петров's charge is asked about too, its
    # sentence first: the extractor makes anyone a sentence names a target.
    assert sorted(item.name for item in classifier.asked) == [
        "Александр Беда",
        "Иван Петров",
        "Фёдор Сирош",
    ]
    petrov = next(item for item in classifier.asked if item.name == "Иван Петров")
    assert petrov.quotes[0] == "Суд арестовал Ивана Петрова по ч. 2 ст. 205.2 УК РФ."
    beda = next(item for item in classifier.asked if item.name == "Александр Беда")
    assert beda.quotes and "признал Александра Беду виновным" in beda.quotes[0]
    assert _roles(session_factory) == {
        "Иван Петров": ("figurant", "accused", "model"),
        "Александр Беда": ("figurant", "accused", "model"),
        "Фёдор Сирош": ("mentioned", "lawyer", "model"),
    }
    assert (result.entities, result.figurant_rules, result.figurant_model) == (3, 0, 2)
    assert (result.mentioned, result.unclear, result.asked_now, result.cached) == (1, 0, 3, 0)


def test_the_same_quotes_are_answered_from_the_cache(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    FigurantFinder(session_factory, classifier=FakeClassifier(KINDS)).run()
    again = FakeClassifier(KINDS)

    result = FigurantFinder(session_factory, classifier=again).run()

    assert again.asked == []
    assert (result.asked_now, result.cached, result.figurant_model) == (0, 3, 2)
    assert _roles(session_factory)["Александр Беда"] == ("figurant", "accused", "model")


def test_a_failed_model_leaves_unclear_never_figurant_and_asks_again(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    failed = FigurantFinder(session_factory, classifier=FakeClassifier(KINDS, fail=True)).run()
    unclear = _roles(session_factory)["Александр Беда"]
    petrov = _roles(session_factory)["Иван Петров"]
    retry = FakeClassifier(KINDS)
    FigurantFinder(session_factory, classifier=retry).run()

    # Unanswered, the rules' charge still makes Петров a figurant; nothing else does.
    assert (failed.failures, failed.unclear, failed.figurant_model) == (3, 2, 0)
    assert failed.figurant_rules == 1
    assert petrov == ("figurant", None, "article")
    assert unclear == ("unclear", None, "model")
    assert len(retry.asked) == 3


def test_without_a_model_only_the_rules_decide(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    result = FigurantFinder(session_factory).run()

    assert (result.figurant_rules, result.unclear) == (1, 2)
    with session_factory() as session:
        reasons = set(session.scalars(select(EntityGroupRoleRecord.reason)).all())
    assert "модель не настроена" in reasons


def test_an_answer_that_names_someone_else_is_dropped() -> None:
    items = [RoleItem(1, "Александр Беда", ()), RoleItem(2, "Фёдор Сирош", ())]
    shifted = [
        RoleAnswer(id=1, source="Александр Беда", kind="accused", explanation="—"),
        # Lost count: the answer about Беда's neighbour carries the wrong id.
        RoleAnswer(id=2, source="Иван Петров", kind="accused", explanation="—"),
    ]

    assert list(matched_answers(items, shifted)) == [1]


@pytest.mark.parametrize(
    ("kind", "role"),
    [
        ("accused", "figurant"),
        ("detained", "possible"),
        ("administrative", "possible"),
        ("lawyer", "mentioned"),
        ("victim", "mentioned"),
        ("unknown", "unclear"),
    ],
)
def test_a_kind_maps_to_a_role(kind: str, role: str) -> None:
    assert role_of(kind) == role


def _seed_officials(session_factory: sessionmaker[Session], *, titles: bool = True) -> None:
    """Бастрыкин: named by his title, and the only target of a hate-speech case against a
    man who insulted him. Минакова: a judge by title. Иванов: charged, no title."""
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        title = "главу СК " if titles else ""
        text_ = (
            "СК возбудил дело по ст. 282 УК РФ против мужчины, который оскорблял "
            f"{title}Александра Бастрыкина."
        )
        _, run = seed.article(source, external_id="insult", title="Дело", text=text_)
        target = _person(session, seed, run, "Александра Бастрыкина", "Александр Бастрыкин")
        law = seed.mention(run, "ст. 282 УК РФ", person_id=None, entity_type="legal_reference")
        session.get_one(EntityMentionRecord, law).normalized_data = {
            "code": "УК РФ",
            "article": "282",
            "part": None,
            "clause": None,
        }
        seed.event(
            run,
            text_,
            event_type="case_opened",
            event_date=None,
            links=[],
            entity_links=[(target, "target"), (law, "legal_basis")],
        )
        for external_id, text_, surface, name in (
            (
                "sk",
                "Глава СК Александр Бастрыкин поручил доложить."
                if titles
                else "Александр Бастрыкин поручил доложить.",
                "Александр Бастрыкин",
                "Александр Бастрыкин",
            ),
            (
                "judge",
                "Судья Ольга Минакова арестовала Ивана Иванова.",
                "Ольга Минакова",
                "Ольга Минакова",
            ),
            ("judge2", "Судья Ольга Минакова продлила арест.", "Ольга Минакова", "Ольга Минакова"),
            ("ivanov", "Суд арестовал Ивана Иванова.", "Ивана Иванова", "Иван Иванов"),
        ):
            _, run = seed.article(source, external_id=external_id, title=external_id, text=text_)
            _person(session, seed, run, surface, name)
            seed.event(run, text_[:10], event_type="arrest", event_date=None, links=[])
        session.commit()
    EntityCollector(session_factory).run()


def test_officials_are_named_in_cases_never_their_figurants(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_officials(session_factory)
    classifier = FakeClassifier({"Иван Иванов": "accused"})

    result = FigurantFinder(session_factory, classifier=classifier).run()

    # A title before the name in the texts: no need to ask.
    assert [item.name for item in classifier.asked] == ["Иван Иванов"]
    assert _roles(session_factory) == {
        "Александр Бастрыкин": ("mentioned", "police", "official"),
        "Ольга Минакова": ("mentioned", "judge", "official"),
        "Иван Иванов": ("figurant", "accused", "model"),
    }
    assert result.officials == 2


def test_the_model_overrules_the_rules_charge_that_names_the_wrong_person(
    session_factory: sessionmaker[Session],
) -> None:
    """Without the title in the texts, the model reads the charge's sentence."""
    _seed_officials(session_factory, titles=False)
    kinds = {"Александр Бастрыкин": "official", "Иван Иванов": "accused", "Ольга Минакова": "judge"}

    classifier = FakeClassifier(kinds)

    FigurantFinder(session_factory, classifier=classifier).run()

    assert _roles(session_factory)["Александр Бастрыкин"] == ("mentioned", "official", "model")
    # The model reads the charge's sentence first, then his own news.
    bastrykin = next(item for item in classifier.asked if item.name == "Александр Бастрыкин")
    assert bastrykin.quotes[0].startswith("СК возбудил дело по ст. 282 УК РФ")


def test_a_person_s_mark_wins_either_way_and_survives_a_rebuild(
    session_factory: sessionmaker[Session],
) -> None:
    from entities.officials import mark_official

    _seed_officials(session_factory)
    classifier = FakeClassifier({"Иван Иванов": "accused", "Ольга Минакова": "judge"})
    FigurantFinder(session_factory, classifier=classifier).run()
    with session_factory.begin() as session:
        entities = {entity.name: entity for entity in session.scalars(select(EntityGroupRecord))}
        mark_official(session, entities["Иван Иванов"], True)
        mark_official(session, entities["Ольга Минакова"], False)
    marked = _roles(session_factory)

    EntityCollector(session_factory).run()
    FigurantFinder(session_factory, classifier=classifier).run()

    assert marked["Иван Иванов"] == ("mentioned", "official", "official")
    after = _roles(session_factory)
    assert after["Иван Иванов"] == ("mentioned", "official", "official")
    # Unmarked, the title no longer counts; the model says judge, the mark says no.
    assert after["Ольга Минакова"] == ("unclear", "judge", "model")
