"""The Criminal Code articles the entity rebuild ties to each entity, on PostgreSQL."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import EntityGroupChargeRecord, EntityGroupRecord, EntityMentionRecord
from entities.collector import EntityCollector


def _person(session: Session, seed: ResearchSeeder, run: int, surface: str, name: str) -> int:
    first, last = name.split()
    mention_id = seed.mention(run, surface, person_id=None)
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": None,
    }
    return mention_id


def _law(
    session: Session,
    seed: ResearchSeeder,
    run: int,
    surface: str,
    code: str,
    article: str,
    part: str | None = None,
) -> int:
    mention_id = seed.mention(run, surface, person_id=None, entity_type="legal_reference")
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "code": code,
        "article": article,
        "part": part,
        "clause": None,
    }
    return mention_id


def _seed(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        text = (
            "Суд арестовал Ивана Петрова по ч. 2 ст. 205.2 УК РФ, по той же ч. 2 ст. 205.2 "
            "УК РФ и ст. 20.3 КоАП РФ; Иван Петров вину не признал."
        )
        _, arrest = seed.article(source, external_id="arrest", title="Арест", text=text)
        petrov = _person(session, seed, arrest, "Ивана Петрова", "Иван Петров")
        petrov_again = _person(session, seed, arrest, "Иван Петров", "Иван Петров")
        seed.event(
            arrest,
            text,
            event_type="arrest",
            event_date=None,
            links=[],
            entity_links=[
                (petrov, "target"),
                # The same person twice is one target, not a shared charge.
                (petrov_again, "target"),
                (
                    _law(session, seed, arrest, "ч. 2 ст. 205.2 УК РФ", "УК РФ", "205.2", "2"),
                    "legal_basis",
                ),
                # The same article named again in the event is one charge.
                (
                    _law(
                        session,
                        seed,
                        arrest,
                        "той же ч. 2 ст. 205.2 УК РФ",
                        "УК РФ",
                        "205.2",
                        "2",
                    ),
                    "legal_basis",
                ),
                # КоАП is not a criminal case.
                (_law(session, seed, arrest, "ст. 20.3 КоАП РФ", "КоАП РФ", "20.3"), "legal_basis"),
            ],
        )
        sentence_text = "Суд приговорил Анну Смирнову и адвоката Олега Орлова по ст. 207.3 УК."
        _, sentence = seed.article(
            source, external_id="sentence", title="Приговор", text=sentence_text
        )
        seed.event(
            sentence,
            sentence_text,
            event_type="sentence",
            event_date=None,
            links=[],
            entity_links=[
                (_person(session, seed, sentence, "Анну Смирнову", "Анна Смирнова"), "target"),
                (_person(session, seed, sentence, "Олега Орлова", "Олег Орлов"), "target"),
                (_law(session, seed, sentence, "ст. 207.3 УК", "УК", "207.3"), "legal_basis"),
            ],
        )
        session.commit()


def _charges(session_factory: sessionmaker[Session]) -> dict[str, list[tuple[object, ...]]]:
    with session_factory() as session:
        rows = session.execute(
            select(
                EntityGroupRecord.name,
                EntityGroupChargeRecord.article,
                EntityGroupChargeRecord.part,
                EntityGroupChargeRecord.event_type,
                EntityGroupChargeRecord.other_targets,
                EntityGroupChargeRecord.quote,
            )
            .join(EntityGroupRecord, EntityGroupRecord.id == EntityGroupChargeRecord.group_id)
            .order_by(EntityGroupRecord.name)
        ).all()
    charges: dict[str, list[tuple[object, ...]]] = {}
    for name, *charge in rows:
        charges.setdefault(name, []).append(tuple(charge))
    return charges


def test_an_event_ties_its_criminal_code_article_to_its_target(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    result = EntityCollector(session_factory).run()
    charges = _charges(session_factory)
    again = EntityCollector(session_factory).run()

    assert (result.charges, result.charged_entities) == (3, 3)
    [(article, part, event_type, others, quote)] = charges["Иван Петров"]
    assert (article, part, event_type, others) == ("205.2", "2", "arrest", 0)
    assert quote.startswith("Суд арестовал Ивана Петрова")
    # Two people of one sentence: the article is theirs, marked shared.
    assert charges["Анна Смирнова"] == [
        (
            "207.3",
            None,
            "sentence",
            1,
            "Суд приговорил Анну Смирнову и адвоката Олега Орлова по ст. 207.3 УК.",
        )
    ]
    assert [charge[:4] for charge in charges["Олег Орлов"]] == [("207.3", None, "sentence", 1)]
    # A rebuild writes the charges anew, not on top.
    assert (again.charges, again.charged_entities) == (3, 3)
    assert len(_charges(session_factory)["Иван Петров"]) == 1
