"""PostgreSQL research repository: person-scoped evidence, events and filters."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from candidates.models import RosfinmonitoringStatus
from extraction.models import EventEntityRole, EventType
from persecution.models import PersecutionClassificationStatus
from research.models import PersonResearchCriteria, ResearchEvidenceType, ResearchSource
from research.repository import SqlAlchemyPersonResearchRepository

ARTICLE_TEXT = (
    "Суд арестовал Ивана Иванова по делу о фейках. "
    "Петра Петрова оштрафовали за пикет. "
    "Упоминается также Сидор Сидоров."
)


@pytest.fixture
def repository(session_factory: sessionmaker[Session]) -> SqlAlchemyPersonResearchRepository:
    return SqlAlchemyPersonResearchRepository(session_factory)


def _criteria(**values: object) -> PersonResearchCriteria:
    return PersonResearchCriteria.model_validate(values)


def test_details_contain_only_this_persons_evidence_and_events(
    session_factory: sessionmaker[Session],
    repository: SqlAlchemyPersonResearchRepository,
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("ОВД-Инфо", "https://ovd.info")
        article_id, run_id = seed.article(
            source_id, external_id="a1", title="Два дела", text=ARTICLE_TEXT
        )
        ivanov = seed.person("Иван Иванов")
        petrov = seed.person("Пётр Петров")
        ivanov_mention = seed.mention(run_id, "Ивана Иванова", person_id=ivanov)
        seed.mention(run_id, "Петра Петрова", person_id=petrov)
        # Resolved to nobody: must not leak onto anyone in the same article.
        seed.mention(run_id, "Сидор Сидоров", person_id=None)
        arrest = seed.event(
            run_id,
            "Суд арестовал Ивана Иванова",
            event_type="arrest",
            event_date=datetime(2024, 2, 1, tzinfo=UTC),
            links=[(ivanov, "subject"), (ivanov, "target")],
        )
        seed.event(
            run_id,
            "Петра Петрова оштрафовали",
            event_type="fine",
            event_date=None,
            links=[(petrov, "subject")],
        )
        session.commit()

    details = repository.get_person_details([ivanov])[ivanov]

    assert details.person.canonical_name == "Иван Иванов"
    assert [(e.event_id, e.event_type, e.roles) for e in details.events] == [
        (arrest, EventType.ARREST, [EventEntityRole.SUBJECT, EventEntityRole.TARGET])
    ]
    assert [(e.evidence_type, e.mention_id, e.event_id, e.text) for e in details.evidence] == [
        (ResearchEvidenceType.PERSON_MENTION, ivanov_mention, None, "Ивана Иванова"),
        (ResearchEvidenceType.EVENT, None, arrest, "Суд арестовал Ивана Иванова"),
    ]
    for evidence in details.evidence:
        assert ARTICLE_TEXT[evidence.start_offset : evidence.end_offset] == evidence.text
        assert (evidence.article_id, evidence.extraction_run_id) == (article_id, run_id)
    assert details.sources == [
        ResearchSource(
            article_id=article_id,
            article_title="Два дела",
            source_name="ОВД-Инфо",
            url="https://example.test/a1",
            published_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    ]


def test_sources_span_every_article_with_person_evidence(
    session_factory: sessionmaker[Session],
    repository: SqlAlchemyPersonResearchRepository,
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        ovd = seed.source("ОВД-Инфо", "https://ovd.info")
        sota = seed.source("SOTA", "https://sotavision.world")
        ivanov = seed.person("Иван Иванов")
        _, run_a = seed.article(ovd, external_id="a1", title="A", text=ARTICLE_TEXT)
        article_b, run_b = seed.article(sota, external_id="b1", title="B", text=ARTICLE_TEXT)
        _, run_c = seed.article(ovd, external_id="c1", title="C", text=ARTICLE_TEXT)
        seed.mention(run_a, "Ивана Иванова", person_id=ivanov)
        seed.event(
            run_b,
            "Суд арестовал",
            event_type="arrest",
            event_date=None,
            links=[(ivanov, "subject")],
        )
        # Article C mentions a different person only.
        seed.mention(run_c, "Петра Петрова", person_id=seed.person("Пётр Петров"))
        session.commit()

    details = repository.get_person_details([ivanov])[ivanov]

    assert [(s.article_title, s.source_name) for s in details.sources] == [
        ("A", "ОВД-Инфо"),
        ("B", "SOTA"),
    ]
    assert [e.article_id for e in details.events] == [article_b]


def test_find_by_name_matches_aliases_case_insensitively_and_escapes_wildcards(
    session_factory: sessionmaker[Session],
    repository: SqlAlchemyPersonResearchRepository,
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        ivanov = seed.person("Иван Иванов")
        seed.alias(ivanov, "Ваня Иванов-Младший")
        petrov = seed.person("Пётр Петров")
        seed.person("Иван Иванович", status="merged")
        session.commit()

    assert repository.find_person_ids(_criteria(name="МЛАДШИЙ")) == [ivanov]
    assert repository.find_person_ids(_criteria(name="иван")) == [ivanov]
    assert repository.find_person_ids(_criteria(name="%")) == []
    assert repository.find_person_ids(_criteria()) == [ivanov, petrov]
    assert repository.find_person_ids(_criteria(person_id=petrov)) == [petrov]


def test_find_by_event_type_and_dates_requires_one_event_satisfying_both(
    session_factory: sessionmaker[Session],
    repository: SqlAlchemyPersonResearchRepository,
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("ОВД-Инфо", "https://ovd.info")
        _, run_id = seed.article(source_id, external_id="a1", title="A", text=ARTICLE_TEXT)
        split = seed.person("Разделённый")
        exact = seed.person("Точный")
        undated = seed.person("Без даты")
        # `split` has an arrest outside the range and a fine inside it.
        seed.event(
            run_id,
            "Суд",
            event_type="arrest",
            event_date=datetime(2023, 6, 1, tzinfo=UTC),
            links=[(split, "subject")],
        )
        seed.event(
            run_id,
            "оштрафовали",
            event_type="fine",
            event_date=datetime(2024, 3, 10, tzinfo=UTC),
            links=[(split, "subject")],
        )
        seed.event(
            run_id,
            "арестовал",
            event_type="arrest",
            event_date=datetime(2024, 3, 31, 23, 30, tzinfo=UTC),
            links=[(exact, "subject")],
        )
        seed.event(
            run_id,
            "пикет",
            event_type="arrest",
            event_date=None,
            links=[(undated, "subject")],
        )
        session.commit()

    march = {"date_from": date(2024, 3, 1), "date_to": date(2024, 3, 31)}

    assert repository.find_person_ids(_criteria(event_types=["arrest"], **march)) == [exact]
    assert repository.find_person_ids(_criteria(event_types=["arrest"])) == [split, exact, undated]
    assert repository.find_person_ids(_criteria(**march)) == [split, exact]


def test_find_by_source_uses_person_evidence_not_article_cooccurrence(
    session_factory: sessionmaker[Session],
    repository: SqlAlchemyPersonResearchRepository,
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        ovd = seed.source("ОВД-Инфо", "https://ovd.info")
        sota = seed.source("SOTA", "https://sotavision.world")
        _, ovd_run = seed.article(ovd, external_id="a1", title="A", text=ARTICLE_TEXT)
        _, sota_run = seed.article(sota, external_id="b1", title="B", text=ARTICLE_TEXT)
        mentioned = seed.person("Иван Иванов")
        event_only = seed.person("Пётр Петров")
        sota_only = seed.person("Сидор Сидоров")
        seed.mention(ovd_run, "Ивана Иванова", person_id=mentioned)
        seed.event(
            ovd_run,
            "оштрафовали",
            event_type="fine",
            event_date=None,
            links=[(event_only, "subject")],
        )
        seed.mention(sota_run, "Сидор Сидоров", person_id=sota_only)
        session.commit()

    assert repository.find_person_ids(_criteria(source="ОВД-Инфо")) == [mentioned, event_only]
    assert repository.find_person_ids(_criteria(source="SOTA")) == [sota_only]


def test_latest_classification_and_rosfinmonitoring_per_snapshot(
    session_factory: sessionmaker[Session],
    repository: SqlAlchemyPersonResearchRepository,
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person = seed.person("Иван Иванов")
        unmatched = seed.person("Пётр Петров")
        seed.classification(
            person,
            "uncertain",
            0.5,
            classifier_version="1.0.0",
            classified_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        seed.classification(
            person,
            "political",
            0.9,
            classifier_version="2.0.0",
            classified_at=datetime(2024, 5, 1, tzinfo=UTC),
        )
        first = seed.snapshot("first")
        second = seed.snapshot("second")
        seed.match(person, first, "matched", 0.95)
        session.commit()

    classifications = repository.get_latest_classifications([person, unmatched])
    assert list(classifications) == [person]
    assert classifications[person].status is PersecutionClassificationStatus.POLITICAL

    statuses = repository.get_rosfinmonitoring([person, unmatched], first)
    assert statuses[person].status is RosfinmonitoringStatus.MATCHED
    assert statuses[unmatched].status is RosfinmonitoringStatus.NO_MATCH_RECORD
    assert repository.get_rosfinmonitoring([person], second)[person].status is (
        RosfinmonitoringStatus.NO_MATCH_RECORD
    )
    assert repository.snapshot_exists(first) is True
    assert repository.snapshot_exists(second + 100) is False


def test_event_span_after_non_bmp_character_matches_python_offsets(
    session_factory: sessionmaker[Session],
    repository: SqlAlchemyPersonResearchRepository,
) -> None:
    text = "Акция 😀🇷🇺 у суда. Суд арестовал Ивана Иванова."
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("ОВД-Инфо", "https://ovd.info")
        _, run_id = seed.article(source_id, external_id="emoji", title="Emoji", text=text)
        ivanov = seed.person("Иван Иванов")
        seed.mention(run_id, "Ивана Иванова", person_id=ivanov)
        seed.event(
            run_id,
            "Суд арестовал Ивана Иванова",
            event_type="arrest",
            event_date=None,
            links=[(ivanov, "subject")],
        )
        session.commit()

    evidence = repository.get_person_details([ivanov])[ivanov].evidence

    assert [e.text for e in evidence] == ["Ивана Иванова", "Суд арестовал Ивана Иванова"]
    for item in evidence:
        assert text[item.start_offset : item.end_offset] == item.text


def test_find_person_ids_can_be_restricted_to_a_candidate_pool(
    session_factory: sessionmaker[Session], repository: SqlAlchemyPersonResearchRepository
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        ivanov = seed.person("Иван Иванов")
        ivanova = seed.person("Мария Иванова")
        petrov = seed.person("Пётр Петров")
        merged = seed.person("Иван Иванов-старший", status="merged")
        session.commit()

    assert repository.find_person_ids(_criteria(), restrict_to=[petrov, ivanov, merged]) == [
        ivanov,
        petrov,
    ]
    assert repository.find_person_ids(_criteria(name="Иванов"), restrict_to=[ivanova, petrov]) == [
        ivanova
    ]
    assert repository.find_person_ids(_criteria(), restrict_to=[]) == []
