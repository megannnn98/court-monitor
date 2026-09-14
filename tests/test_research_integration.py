"""End-to-end research over PostgreSQL: fixtures -> ResearchRequest -> ResearchService.

Business scenario: who is politically persecuted and confirmed absent from a
Rosfinmonitoring snapshot?

    A  POLITICAL      MATCHED            excluded (in the list)
    B  NON_POLITICAL  NOT_MATCHED        excluded (not political)
    C  POLITICAL      NOT_MATCHED        included
    D  POLITICAL      AMBIGUOUS          excluded, review_required in broader query
    E  POLITICAL      INSUFFICIENT_DATA  excluded, never treated as NOT_MATCHED
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from research_db_fixtures import ResearchSeeder
from sqlalchemy.orm import Session, sessionmaker

from candidate_query_service import CandidateQueryService
from research_models import PersonResearchCriteria, ResearchObjectType, ResearchRequest
from research_repository import SqlAlchemyPersonResearchRepository
from research_service import ResearchService

TEXT = (
    "Мосгорсуд арестовал Анну Алексееву. "
    "Бориса Борисова оштрафовали за хулиганство. "
    "Суд арестовал Виктора Викторова по делу о фейках об армии. "
    "Дарью Дмитриеву задержали на акции. "
    "Задержан Евгений."
)


@dataclass(frozen=True)
class Scenario:
    snapshot_id: int
    article_id: int
    run_id: int
    person_ids: dict[str, int]
    c_mention_id: int
    c_event_id: int
    c_alias_id: int


@pytest.fixture
def scenario(session_factory: sessionmaker[Session]) -> Scenario:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("ОВД-Инфо", "https://ovd.info")
        article_id, run_id = seed.article(
            source_id, external_id="case-1", title="Хроника преследований", text=TEXT
        )
        snapshot_id = seed.snapshot()

        a = seed.person("Анна Алексеева")
        b = seed.person("Борис Борисов")
        c = seed.person("Виктор Викторов")
        d = seed.person("Дарья Дмитриева")
        e = seed.person("Евгений")

        c_alias_id = seed.alias(c, "Виктора Викторова")
        for person_id, surface in [
            (a, "Анну Алексееву"),
            (b, "Бориса Борисова"),
            (d, "Дарью Дмитриеву"),
            (e, "Евгений"),
        ]:
            seed.mention(run_id, surface, person_id=person_id)
        c_mention_id = seed.mention(run_id, "Виктора Викторова", person_id=c)

        seed.event(
            run_id,
            "Мосгорсуд арестовал Анну Алексееву",
            event_type="arrest",
            event_date=datetime(2024, 2, 1, tzinfo=UTC),
            links=[(a, "subject")],
        )
        c_event_id = seed.event(
            run_id,
            "Суд арестовал Виктора Викторова",
            event_type="arrest",
            event_date=datetime(2024, 3, 5, tzinfo=UTC),
            links=[(c, "subject")],
            attributes={"charge": "ст. 207.3 УК РФ"},
        )

        seed.classification(a, "political", 0.9)
        seed.classification(b, "non_political", 0.9)
        seed.classification(c, "political", 0.85, reasons=["Political charge: 207.3"])
        seed.classification(d, "political", 0.9)
        seed.classification(e, "political", 0.9)

        a_entry = seed.entry(snapshot_id, "АЛЕКСЕЕВА АННА")
        seed.match(a, snapshot_id, "matched", 0.95, matched_entry_id=a_entry)
        seed.match(b, snapshot_id, "not_matched", 0.9)
        seed.match(c, snapshot_id, "not_matched", 0.9, reasons=["No candidate above threshold"])
        seed.match(d, snapshot_id, "ambiguous", 0.6)
        seed.match(e, snapshot_id, "insufficient_data", 0.0)
        session.commit()

    return Scenario(
        snapshot_id=snapshot_id,
        article_id=article_id,
        run_id=run_id,
        person_ids={"A": a, "B": b, "C": c, "D": d, "E": e},
        c_mention_id=c_mention_id,
        c_event_id=c_event_id,
        c_alias_id=c_alias_id,
    )


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> ResearchService:
    return ResearchService(
        repository=SqlAlchemyPersonResearchRepository(session_factory),
        candidate_query=CandidateQueryService(session_factory),
    )


def _request(**criteria: object) -> ResearchRequest:
    return ResearchRequest(
        object_type=ResearchObjectType.PERSON,
        criteria=PersonResearchCriteria.model_validate(criteria),
    )


def _names(scenario: Scenario, person_ids: list[int | None]) -> list[str]:
    by_id = {person_id: name for name, person_id in scenario.person_ids.items()}
    return [by_id[person_id] for person_id in person_ids if person_id is not None]


def test_political_not_matched_returns_exact_person_result(
    scenario: Scenario, service: ResearchService
) -> None:
    response = service.execute(
        _request(
            persecution_status="political",
            rosfinmonitoring_status="not_matched",
            snapshot_id=scenario.snapshot_id,
        )
    )

    assert response.total_matched == 1
    (result,) = response.results
    c = scenario.person_ids["C"]
    assert result.model_dump(
        mode="json",
        exclude={
            "person": {"created_at", "updated_at"},
            "aliases": {"__all__": {"created_at"}},
            "persecution": {"id"},
        },
    ) == {
        "object_type": "person",
        "person": {
            "id": c,
            "canonical_name": "Виктор Викторов",
            "normalized_name": "виктор викторов",
            "matching_key": "виктор|викторов",
            "status": "active",
            "merged_into_id": None,
        },
        "aliases": [
            {
                "id": scenario.c_alias_id,
                "person_id": c,
                "surface_text": "Виктора Викторова",
                "normalized_text": "виктора викторова",
                "matching_key": "виктора|викторова",
                "origin": "extraction",
                "confidence": 0.9,
                "source_mention_id": None,
            }
        ],
        "persecution": {
            "person_id": c,
            "status": "political",
            "confidence": 0.85,
            "reasons": ["Political charge: 207.3"],
            "evidence_types": [],
            "classifier_name": "rule-based",
            "classifier_version": "1.0.0",
            "classified_at": "2024-01-01T00:00:00Z",
        },
        "rosfinmonitoring": {
            "snapshot_id": scenario.snapshot_id,
            "status": "not_matched",
            "confidence": 0.9,
            "matched_entry_id": None,
            "matched_entry_name": None,
            "candidate_entries": [],
            "reasons": ["No candidate above threshold"],
            "matched_at": "2024-01-01T00:00:00Z",
        },
        "events": [
            {
                "event_id": scenario.c_event_id,
                "event_type": "arrest",
                "event_date": "2024-03-05T00:00:00Z",
                "roles": ["subject"],
                "confidence": 0.8,
                "attributes": {"charge": "ст. 207.3 УК РФ"},
                "article_id": scenario.article_id,
            }
        ],
        "evidence": [
            {
                "evidence_type": "person_mention",
                "article_id": scenario.article_id,
                "extraction_run_id": scenario.run_id,
                "start_offset": TEXT.index("Виктора Викторова"),
                "end_offset": TEXT.index("Виктора Викторова") + len("Виктора Викторова"),
                "text": "Виктора Викторова",
                "mention_id": scenario.c_mention_id,
                "event_id": None,
            },
            {
                "evidence_type": "event",
                "article_id": scenario.article_id,
                "extraction_run_id": scenario.run_id,
                "start_offset": TEXT.index("Суд арестовал Виктора"),
                "end_offset": TEXT.index("Суд арестовал Виктора")
                + len("Суд арестовал Виктора Викторова"),
                "text": "Суд арестовал Виктора Викторова",
                "mention_id": None,
                "event_id": scenario.c_event_id,
            },
        ],
        "sources": [
            {
                "article_id": scenario.article_id,
                "article_title": "Хроника преследований",
                "source_name": "ОВД-Инфо",
                "url": "https://example.test/case-1",
                "published_at": "2024-01-01T00:00:00Z",
            }
        ],
        "warnings": [],
        "review_required": False,
    }


def test_broader_political_query_flags_unclear_rf_statuses_for_review(
    scenario: Scenario, service: ResearchService
) -> None:
    response = service.execute(
        _request(persecution_status="political", snapshot_id=scenario.snapshot_id)
    )

    summary = {
        _names(scenario, [r.person.id])[0]: (
            r.rosfinmonitoring.status.value if r.rosfinmonitoring else None,
            r.review_required,
            [w.code.value for w in r.warnings],
        )
        for r in response.results
    }
    assert summary == {
        "A": ("matched", False, []),
        "C": ("not_matched", False, []),
        "D": ("ambiguous", True, ["rosfin_ambiguous"]),
        "E": ("insufficient_data", True, ["rosfin_insufficient_data"]),
    }


@pytest.mark.parametrize(
    ("rf_status", "expected"),
    [
        ("matched", ["A"]),
        ("ambiguous", ["D"]),
        ("insufficient_data", ["E"]),
        ("no_match_record", []),
    ],
)
def test_political_with_other_rf_statuses_goes_through_candidate_query(
    scenario: Scenario, service: ResearchService, rf_status: str, expected: list[str]
) -> None:
    response = service.execute(
        _request(
            persecution_status="political",
            rosfinmonitoring_status=rf_status,
            snapshot_id=scenario.snapshot_id,
        )
    )

    assert _names(scenario, [r.person.id for r in response.results]) == expected


def test_non_political_not_matched_is_separate_from_political_query(
    scenario: Scenario, service: ResearchService
) -> None:
    response = service.execute(
        _request(
            persecution_status="non_political",
            rosfinmonitoring_status="not_matched",
            snapshot_id=scenario.snapshot_id,
        )
    )

    assert _names(scenario, [r.person.id for r in response.results]) == ["B"]


def test_research_agrees_with_candidate_query_for_the_product_question(
    scenario: Scenario,
    service: ResearchService,
    session_factory: sessionmaker[Session],
) -> None:
    candidates = CandidateQueryService(session_factory).get_candidates(scenario.snapshot_id)
    response = service.execute(
        _request(
            persecution_status="political",
            rosfinmonitoring_status="not_matched",
            snapshot_id=scenario.snapshot_id,
        )
    )

    assert [r.person.id for r in response.results] == [c.person_id for c in candidates.candidates]
