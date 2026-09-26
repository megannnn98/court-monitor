"""Which figurants' cases are political persecution, on PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import (
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityMentionRecord,
)
from entities.collector import EntityCollector
from entities.politics import (
    PoliticsAnswer,
    PoliticsClassifierError,
    PoliticsFinder,
    PoliticsItem,
    matched_answers,
    verdict_of,
)
from entities.rf_check import EntityRfCheck

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
    """Петров: a political article. Беда: a sentence the rules cannot read. Смирнова: a
    «Мемориал» card. Сирош: a lawyer. Орлов: on the list, judged all the same."""
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
        # A charge under an article no rule settles (318: often the protests): the model
        # decides.
        text_ = "Суд осудил Александра Беду по ч. 1 ст. 318 УК РФ за удар полицейского."
        _, run = seed.article(source, external_id="beda", title="Приговор", text=text_)
        beda = _person(session, seed, run, "Александра Беду", "Александр Беда")
        murder = seed.mention(
            run, "ч. 1 ст. 318 УК РФ", person_id=None, entity_type="legal_reference"
        )
        session.get_one(EntityMentionRecord, murder).normalized_data = {
            "code": "УК РФ",
            "article": "318",
            "part": "1",
            "clause": None,
        }
        seed.event(
            run,
            text_,
            event_type="sentence",
            event_date=None,
            links=[],
            entity_links=[(beda, "target"), (murder, "legal_basis")],
        )
        for external_id, text_, surface, name in (
            ("sirosh", "Адвокат Фёдор Сирош обжалует приговор.", "Фёдор Сирош", "Фёдор Сирош"),
            ("orlov", "Суд арестовал Олега Орлова.", "Олега Орлова", "Олег Орлов"),
            (
                "card",
                (
                    "Анна Смирнова обвиняется. Проект «Поддержка политзаключённых. Мемориал» "
                    "внёс человека в реестр преследуемых: «Другие жертвы политических репрессий»."
                ),
                "Анна Смирнова",
                "Анна Смирнова",
            ),
        ):
            _, run = seed.article(source, external_id=external_id, title=external_id, text=text_)
            _person(session, seed, run, surface, name)
            seed.event(run, text_[:10], event_type="sentence", event_date=None, links=[])
        session.commit()
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE entity_groups SET name = 'Олег Иванович Орлов' WHERE name = 'Олег Орлов'")
        )
        for group_id, name in session.execute(select(EntityGroupRecord.id, EntityGroupRecord.name)):
            role = "mentioned" if name == "Фёдор Сирош" else "figurant"
            session.add(
                EntityGroupRoleRecord(
                    group_id=group_id, role=role, method="model", reason="", quote=""
                )
            )
    EntityRfCheck(session_factory, download=lambda: RF_PAGE).run()


class FakeClassifier:
    model = "fake-model"

    def __init__(self, verdicts: dict[str, str], *, fail: bool = False) -> None:
        self.verdicts = verdicts
        self.fail = fail
        self.asked: list[PoliticsItem] = []

    def classify(self, items: Sequence[PoliticsItem]) -> dict[int, PoliticsAnswer]:
        self.asked += items
        if self.fail:
            raise PoliticsClassifierError("provider down")
        return {
            item.id: PoliticsAnswer(
                id=item.id,
                source=item.name,
                verdict=self.verdicts[item.name],  # type: ignore[arg-type]
                explanation=f"так про {item.name}",
            )
            for item in items
        }


VERDICTS = {
    "Александр Беда": "criminal",
    "Анна Смирнова": "political",
    # On the list, and judged all the same.
    "Олег Иванович Орлов": "political",
}


def _verdicts(session_factory: sessionmaker[Session]) -> dict[str, tuple[str, str]]:
    with session_factory() as session:
        rows = session.execute(
            select(
                EntityGroupRecord.name,
                EntityGroupPoliticsRecord.verdict,
                EntityGroupPoliticsRecord.method,
            ).join(EntityGroupRecord, EntityGroupRecord.id == EntityGroupPoliticsRecord.group_id)
        ).all()
    return {name: (verdict, method) for name, verdict, method in rows}


def test_a_political_article_settles_it_and_a_model_reads_the_rest(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    classifier = FakeClassifier(VERDICTS)

    result = PoliticsFinder(session_factory, classifier=classifier).run()

    # Петров by the rules; Сирош is no figurant; Орлов on the list is judged too: the list
    # confirms who a person is, not which case.
    assert sorted(item.name for item in classifier.asked) == [
        "Александр Беда",
        "Анна Смирнова",
        "Олег Иванович Орлов",
    ]
    smirnova = next(item for item in classifier.asked if item.name == "Анна Смирнова")
    beda = next(item for item in classifier.asked if item.name == "Александр Беда")
    assert beda.articles == ("318",)
    assert smirnova.memorial == "Другие жертвы политических репрессий"
    assert _verdicts(session_factory) == {
        "Иван Петров": ("political", "article"),
        "Александр Беда": ("criminal", "model"),
        "Анна Смирнова": ("political", "model"),
        "Олег Иванович Орлов": ("political", "model"),
    }
    assert (result.figurants, result.political_rules, result.political_model) == (4, 1, 2)
    assert (result.criminal, result.unclear, result.asked_now) == (1, 0, 3)


def test_the_same_input_is_answered_from_the_cache(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS)).run()
    again = FakeClassifier(VERDICTS)

    result = PoliticsFinder(session_factory, classifier=again).run()

    assert again.asked == []
    assert (result.asked_now, result.cached, result.political_model) == (0, 3, 2)


def test_a_failed_model_leaves_unclear_never_political(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    result = PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS, fail=True)).run()

    assert (result.failures, result.unclear, result.political_model) == (3, 3, 0)
    assert _verdicts(session_factory)["Анна Смирнова"] == ("unclear", "model")


def test_an_answer_that_names_someone_else_is_dropped() -> None:
    items = [PoliticsItem(1, "Анна Смирнова", (), (), None)]
    answers = [PoliticsAnswer(id=1, source="Иван Петров", verdict="political", explanation="—")]

    assert matched_answers(items, answers) == {}


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [("political", "political"), ("criminal", "criminal"), ("unknown", "unclear")],
)
def test_an_answer_maps_to_a_verdict(answer: str, verdict: str) -> None:
    assert verdict_of(answer) == verdict


def test_a_political_verdict_sticks_when_the_input_changes(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS)).run()
    with session_factory.begin() as session:
        session.execute(text("UPDATE entity_politics_answers SET input_hash = md5(input_hash)"))
    again = FakeClassifier(VERDICTS)

    PoliticsFinder(session_factory, classifier=again).run()

    # Смирнова stays political unasked; Беда's «criminal» is asked again.
    assert [item.name for item in again.asked] == ["Александр Беда"]


def test_the_rules_settle_common_crime_and_the_memorial_s_sure_categories(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with session_factory.begin() as session:
        # Беда charged with murder alone; Смирнова on the «Антивоенное дело» list.
        session.execute(
            text("UPDATE entity_group_charges SET article = '105' WHERE article = '318'")
        )
        session.execute(
            text(
                "UPDATE parsed_articles SET text = replace(text, "
                "'«Другие жертвы политических репрессий»', '«Антивоенное дело»')"
            )
        )
    classifier = FakeClassifier(VERDICTS)

    result = PoliticsFinder(session_factory, classifier=classifier).run()

    # Only Орлов, with neither article nor category, is left to the model.
    assert [item.name for item in classifier.asked] == ["Олег Иванович Орлов"]
    assert _verdicts(session_factory) == {
        "Иван Петров": ("political", "article"),
        "Александр Беда": ("criminal", "article"),
        "Анна Смирнова": ("political", "memorial"),
        "Олег Иванович Орлов": ("political", "model"),
    }
    assert (result.political_memorial, result.criminal_rules, result.criminal) == (1, 1, 1)


def test_hooliganism_is_no_common_crime_for_the_rules(
    session_factory: sessionmaker[Session],
) -> None:
    """ст. 213: Pussy Riot's article; the model reads it."""
    _seed(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE entity_group_charges SET article = '213' WHERE article = '318'")
        )
    classifier = FakeClassifier(VERDICTS)

    PoliticsFinder(session_factory, classifier=classifier).run()

    assert "Александр Беда" in [item.name for item in classifier.asked]
