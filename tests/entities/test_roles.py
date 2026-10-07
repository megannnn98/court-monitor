"""Who a criminal case is opened against among the entities, on PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import (
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityMentionRecord,
    EntityOfficialMarkRecord,
    ExcludedPersonRecord,
)
from entities.collector import EntityCollector
from entities.rf_check import EntityRfCheck
from entities.roles import (
    FIGURANT,
    FigurantFinder,
    RoleAnswer,
    RoleClassifierError,
    RoleItem,
    decide_role,
    matched_answers,
    role_of,
)

RF_PAGE = """<!doctype html><html>
<div class="panel-heading"><h4>Физические лица</h4></div>
<div class="panel-body"><ol><li>1. ОРЛОВ ОЛЕГ ИВАНОВИЧ*, 01.01.1980 г.р. , Г. ТВЕРЬ;</li></ol></div>
</html>""".encode()


def _person(session: Session, seed: DatabaseSeeder, run: int, surface: str, name: str) -> int:
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
    cannot read. Орлов: on the Rosfinmonitoring list, looked at all the same."""
    with session_factory() as session:
        seed = DatabaseSeeder(session)
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


KINDS = {
    "Иван Петров": "accused",
    "Александр Беда": "accused",
    "Фёдор Сирош": "lawyer",
    # On the list, and looked at all the same: the list confirms who, not which case.
    "Олег Иванович Орлов": "accused",
}


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

    # Орлов is on the list and asked about too: the list confirms who a person is, not
    # which case. Петров's charge is asked about too, its sentence first: the extractor
    # makes anyone a sentence names a target.
    assert sorted(item.name for item in classifier.asked) == [
        "Александр Беда",
        "Иван Петров",
        "Олег Иванович Орлов",
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
        "Олег Иванович Орлов": ("figurant", "accused", "model"),
    }
    assert (result.entities, result.figurant_rules, result.figurant_model) == (4, 0, 3)
    assert (result.mentioned, result.unclear, result.asked_now, result.cached) == (1, 0, 4, 0)


def test_the_same_quotes_are_answered_from_the_cache(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    FigurantFinder(session_factory, classifier=FakeClassifier(KINDS)).run()
    again = FakeClassifier(KINDS)

    result = FigurantFinder(session_factory, classifier=again).run()

    assert again.asked == []
    assert (result.asked_now, result.cached, result.figurant_model) == (0, 4, 3)
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
    assert (failed.failures, failed.unclear, failed.figurant_model) == (4, 3, 0)
    assert failed.figurant_rules == 1
    assert petrov == ("figurant", None, "article")
    assert unclear == ("unclear", None, "model")
    assert len(retry.asked) == 4


def test_without_a_model_only_the_rules_decide(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    result = FigurantFinder(session_factory).run()

    assert (result.figurant_rules, result.unclear) == (1, 3)
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
        ("foreign", "mentioned"),
        ("historical", "mentioned"),
        ("support", "mentioned"),
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
        seed = DatabaseSeeder(session)
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


def _list(session_factory: sessionmaker[Session]) -> dict[str, tuple[str, str, bool]]:
    """The officials list: name → (category, external id, active)."""
    with session_factory() as session:
        return {
            row.full_name: (row.category, row.external_id, row.active)
            for row in session.scalars(select(ExcludedPersonRecord))
        }


def test_officials_named_by_title_are_put_on_the_list_once(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_officials(session_factory)
    classifier = FakeClassifier({"Иван Иванов": "accused"})

    first = FigurantFinder(session_factory, classifier=classifier).run()
    second = FigurantFinder(session_factory, classifier=classifier).run()

    # The reliable sign — a title before the name — puts them on the list, with the
    # category the title tells and the entity's key as the row's id; the accused is not.
    assert _list(session_factory) == {
        "Александр Бастрыкин": ("police", "auto:александр бастрыкин", True),
        "Ольга Минакова": ("judge", "auto:ольга минакова", True),
    }
    assert (first.officials_listed, second.officials_listed) == (2, 0)
    with session_factory() as session:
        reason = session.scalar(
            select(ExcludedPersonRecord.reason).where(
                ExcludedPersonRecord.full_name == "Ольга Минакова"
            )
        )
    assert reason == "автоматически: в текстах «судья» перед именем"


def test_a_person_switched_off_by_hand_is_not_put_back_on_the_list(
    session_factory: sessionmaker[Session],
) -> None:
    """«We looked and she is not an official» is an answer that must outlive the run."""
    _seed_officials(session_factory)
    with session_factory.begin() as session:
        session.add(
            ExcludedPersonRecord(
                external_id="console:минакова ольга",
                full_name="Минакова Ольга",
                normalized_name="минакова ольга",
                category="judge",
                active=False,
            )
        )

    result = FigurantFinder(
        session_factory, classifier=FakeClassifier({"Иван Иванов": "accused"})
    ).run()

    assert result.officials_listed == 1
    assert _list(session_factory) == {
        "Минакова Ольга": ("judge", "console:минакова ольга", False),
        "Александр Бастрыкин": ("police", "auto:александр бастрыкин", True),
    }


def test_a_person_with_a_mark_of_their_own_is_not_put_on_the_list(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_officials(session_factory)
    with session_factory.begin() as session:
        session.add(EntityOfficialMarkRecord(key="ольга минакова", official=False))
        session.add(EntityOfficialMarkRecord(key="александр бастрыкин", official=True))
    classifier = FakeClassifier({"Иван Иванов": "accused", "Ольга Минакова": "judge"})

    result = FigurantFinder(session_factory, classifier=classifier).run()

    assert result.officials_listed == 0
    assert _list(session_factory) == {}


def test_a_listed_judge_stays_an_official_when_the_title_is_gone(
    session_factory: sessionmaker[Session],
) -> None:
    """The list is what remembers: an article that names her without «судья» no longer
    shows a title, and she is still not asked about as a possible figurant."""
    _seed_officials(session_factory)
    classifier = FakeClassifier({"Иван Иванов": "accused"})
    FigurantFinder(session_factory, classifier=classifier).run()
    with session_factory.begin() as session:
        # Same length: the mentions' offsets still point at the names.
        session.execute(text("UPDATE parsed_articles SET text = replace(text, 'Судья', 'Мадам')"))

    FigurantFinder(session_factory, classifier=classifier).run()

    assert _roles(session_factory)["Ольга Минакова"] == ("mentioned", "judge", "official")
    with session_factory() as session:
        reason = session.scalar(
            select(EntityGroupRoleRecord.reason)
            .join(EntityGroupRecord, EntityGroupRecord.id == EntityGroupRoleRecord.group_id)
            .where(EntityGroupRecord.name == "Ольга Минакова")
        )
    assert reason is not None and reason.startswith("должностное лицо — в списке исключений")


def test_a_name_on_the_exclusion_list_is_never_a_target_figurant(
    session_factory: sessionmaker[Session],
) -> None:
    """The list a person keeps in Airtable: Иванов is charged, and on the list, so the
    model is never asked and he cannot become a figurant."""
    _seed_officials(session_factory, titles=False)
    with session_factory.begin() as session:
        session.add(
            ExcludedPersonRecord(
                external_id="rec1",
                full_name="Иванов Иван Иванович",
                normalized_name="Иванов Иван Иванович",
                category="lawyer",
                reason="защитник",
                active=True,
            )
        )
    classifier = FakeClassifier({"Александр Бастрыкин": "official", "Иван Иванов": "accused"})

    result = FigurantFinder(session_factory, classifier=classifier).run()

    assert [item.name for item in classifier.asked] == ["Александр Бастрыкин"]
    # `lawyer` is not one of the model's official kinds, so the entity falls back to
    # the general one; either way he is named in the case, not charged by it.
    assert _roles(session_factory)["Иван Иванов"] == ("mentioned", "official", "official")
    assert result.officials == 2


def test_a_deactivated_exclusion_goes_back_to_the_model(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_officials(session_factory, titles=False)
    with session_factory.begin() as session:
        session.add(
            ExcludedPersonRecord(
                external_id="rec1",
                full_name="Иванов Иван Иванович",
                normalized_name="Иванов Иван Иванович",
                category="judge",
                reason=None,
                active=False,
            )
        )
    classifier = FakeClassifier({"Иван Иванов": "accused", "Александр Бастрыкин": "official"})

    FigurantFinder(session_factory, classifier=classifier).run()

    assert _roles(session_factory)["Иван Иванов"] == ("figurant", "accused", "model")


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


def test_a_person_s_no_outranks_a_row_of_the_list(
    session_factory: sessionmaker[Session],
) -> None:
    """The order is the person's mark, then the list, then the texts: a row written by
    hand or by step 4 does not turn a person who said «not an official» into one."""
    _seed_officials(session_factory, titles=False)
    with session_factory.begin() as session:
        session.add(
            ExcludedPersonRecord(
                external_id="console:иван иванов",
                full_name="Иван Иванов",
                normalized_name="иван иванов",
                category="lawyer",
                active=True,
            )
        )
        session.add(EntityOfficialMarkRecord(key="иван иванов", official=False))
    classifier = FakeClassifier({"Александр Бастрыкин": "official", "Иван Иванов": "accused"})

    FigurantFinder(session_factory, classifier=classifier).run()

    assert _roles(session_factory)["Иван Иванов"] == ("figurant", "accused", "model")


def test_a_surname_alone_is_read_as_anyone_is(session_factory: sessionmaker[Session]) -> None:
    """Whether a case is opened does not hang on the name: who a political case is of,
    step 5 asks a person. Sent to a person here, a bribe-taker waited in the queue unread."""
    _seed(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE entity_groups SET name = 'Беда' WHERE name = 'Александр Беда'")
        )
    classifier = FakeClassifier({**KINDS, "Беда": "accused"})

    FigurantFinder(session_factory, classifier=classifier).run()

    assert "Беда" in [item.name for item in classifier.asked]
    assert _roles(session_factory)["Беда"] == ("figurant", "accused", "model")


def test_a_rebuild_that_changes_the_keys_asks_nothing_again(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    FigurantFinder(session_factory, classifier=FakeClassifier(KINDS)).run()
    with session_factory.begin() as session:
        session.execute(text("UPDATE entity_groups SET key = key || ' · новый ключ'"))
    again = FakeClassifier(KINDS)

    result = FigurantFinder(session_factory, classifier=again).run()

    # The question is the same: its hash finds the answer, whatever the key.
    assert again.asked == []
    assert result.cached == 4


def test_an_accused_sticks_when_the_quotes_change_the_rest_is_asked_again(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    FigurantFinder(session_factory, classifier=FakeClassifier(KINDS)).run()
    # New publications: every question changed.
    with session_factory.begin() as session:
        session.execute(text("UPDATE entity_role_answers SET input_hash = md5(input_hash)"))
    again = FakeClassifier(KINDS)

    FigurantFinder(session_factory, classifier=again).run()

    assert [item.name for item in again.asked] == ["Фёдор Сирош"]
    assert _roles(session_factory)["Александр Беда"] == ("figurant", "accused", "model")


def test_an_earlier_prompt_s_answer_holds_unless_it_is_accused(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    FigurantFinder(session_factory, classifier=FakeClassifier(KINDS)).run()
    with session_factory.begin() as session:
        session.execute(text("UPDATE entity_role_answers SET prompt_version = 'roles-v4'"))
    # v5 asks every «accused» again: Беда's case turns out to be abroad.
    again = FakeClassifier({**KINDS, "Александр Беда": "foreign"})

    FigurantFinder(session_factory, classifier=again).run()

    assert sorted(item.name for item in again.asked) == [
        "Александр Беда",
        "Иван Петров",
        "Олег Иванович Орлов",
    ]
    assert _roles(session_factory)["Александр Беда"] == ("mentioned", "foreign", "model")
    assert _roles(session_factory)["Иван Петров"][0] == "figurant"


def test_past_the_budget_the_rest_is_unasked_and_counted(
    session_factory: sessionmaker[Session],
) -> None:
    from entities.llm import Spend

    _seed(session_factory)
    spent = FakeClassifier(KINDS)
    spent.spend = Spend(budget_usd=0.0)  # type: ignore[attr-defined]

    result = FigurantFinder(session_factory, classifier=spent).run()

    assert spent.asked == []
    assert (result.unasked, result.asked_now) == (4, 0)
    # Unasked, the rules' charge still makes Петров a figurant.
    assert _roles(session_factory)["Иван Петров"] == ("figurant", None, "article")


def test_another_model_is_compared_on_what_the_steps_answered(
    session_factory: sessionmaker[Session],
) -> None:
    from entities.compare import compare_roles

    _seed(session_factory)
    FigurantFinder(session_factory, classifier=FakeClassifier(KINDS)).run()
    # The other model takes the lawyer for the accused.
    other = FakeClassifier({**KINDS, "Фёдор Сирош": "accused"})
    other.model = "local-model"  # type: ignore[misc]

    result = compare_roles(session_factory, other, size=10, seed=1)

    report = result.report()
    assert (report["sample"], report["answered"], report["agree"]) == (4, 4, 3)
    assert report["agreement"] == 0.75
    assert report["confusion"]["mentioned → figurant"] == 1
    assert report["disagreements"][0]["name"] == "Фёдор Сирош"


def test_a_person_s_word_on_a_role_survives_the_next_run(
    session_factory: sessionmaker[Session],
) -> None:
    """«Неясная роль» and a person's decision on it: step 4 rewrites the roles, so the
    decision is kept by the key and written again over the model's next answer."""
    _seed(session_factory)
    FigurantFinder(session_factory, classifier=FakeClassifier(KINDS)).run()
    with session_factory.begin() as session:
        sirosh = session.scalar(
            select(EntityGroupRecord).where(EntityGroupRecord.name == "Фёдор Сирош")
        )
        assert sirosh is not None
        decide_role(session, sirosh, FIGURANT)

    again = FakeClassifier(KINDS)
    FigurantFinder(session_factory, classifier=again).run()

    # The model is not asked again because the answer is cached under the same input hash,
    # not because the person decided anything. The decision is what wins over the model's
    # answer on the way into the table; the two are separate mechanisms.
    assert again.asked == [], "cached answer, not a decision: see the comment"
    assert _roles(session_factory)["Фёдор Сирош"] == ("figurant", None, "manual")
