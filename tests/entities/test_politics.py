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
    «Мемориал» card. Сирош: a lawyer. Орлов: on the list."""
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
        # A charge by a Criminal Code article, but not a political one: the model decides.
        text_ = "Суд осудил Александра Беду по ч. 1 ст. 105 УК РФ за убийство."
        _, run = seed.article(source, external_id="beda", title="Приговор", text=text_)
        beda = _person(session, seed, run, "Александра Беду", "Александр Беда")
        murder = seed.mention(
            run, "ч. 1 ст. 105 УК РФ", person_id=None, entity_type="legal_reference"
        )
        session.get_one(EntityMentionRecord, murder).normalized_data = {
            "code": "УК РФ",
            "article": "105",
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
                    "внёс человека в реестр преследуемых: «Антивоенное дело»."
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


VERDICTS = {"Александр Беда": "criminal", "Анна Смирнова": "political"}


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

    # Петров by the rules; Сирош is no figurant; Орлов is on the list.
    assert sorted(item.name for item in classifier.asked) == ["Александр Беда", "Анна Смирнова"]
    smirnova = next(item for item in classifier.asked if item.name == "Анна Смирнова")
    beda = next(item for item in classifier.asked if item.name == "Александр Беда")
    assert beda.articles == ("105",)
    assert smirnova.memorial == "Антивоенное дело"
    assert _verdicts(session_factory) == {
        "Иван Петров": ("political", "article"),
        "Александр Беда": ("criminal", "model"),
        "Анна Смирнова": ("political", "model"),
    }
    assert (result.figurants, result.political_rules, result.political_model) == (3, 1, 1)
    assert (result.criminal, result.unclear, result.asked_now) == (1, 0, 2)


def test_the_same_input_is_answered_from_the_cache(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS)).run()
    again = FakeClassifier(VERDICTS)

    result = PoliticsFinder(session_factory, classifier=again).run()

    assert again.asked == []
    assert (result.asked_now, result.cached, result.political_model) == (0, 2, 1)


def test_a_failed_model_leaves_unclear_never_political(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    result = PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS, fail=True)).run()

    assert (result.failures, result.unclear, result.political_model) == (2, 2, 0)
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
