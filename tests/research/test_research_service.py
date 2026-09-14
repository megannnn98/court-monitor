"""ResearchService orchestration with in-memory fakes (no database)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from candidates.models import (
    DEFAULT_MIN_PERSECUTION_CONFIDENCE,
    CandidateQueryResult,
    PoliticalPersecutionCandidate,
    RosfinmonitoringStatus,
)
from persecution.models import PersecutionClassification, PersecutionClassificationStatus
from persons.models import Person
from research.models import (
    PersonResearchCriteria,
    ResearchObjectType,
    ResearchRequest,
    ResearchRosfinmonitoring,
    ResearchWarningCode,
)
from research.service import (
    PersonResearchDetails,
    ResearchCandidatesRequiredError,
    ResearchService,
    ResearchSnapshotNotFoundError,
)

POLITICAL = PersecutionClassificationStatus.POLITICAL
NON_POLITICAL = PersecutionClassificationStatus.NON_POLITICAL
UNCERTAIN = PersecutionClassificationStatus.UNCERTAIN


@dataclass
class FakeRepository:
    person_ids: list[int]
    classifications: dict[int, PersecutionClassification] = field(default_factory=dict)
    rf_statuses: dict[int, RosfinmonitoringStatus] = field(default_factory=dict)
    snapshots: set[int] = field(default_factory=lambda: {3})
    seen_criteria: list[PersonResearchCriteria] = field(default_factory=list)
    detail_requests: list[list[int]] = field(default_factory=list)
    restrictions: list[list[int] | None] = field(default_factory=list)

    def snapshot_exists(self, snapshot_id: int) -> bool:
        return snapshot_id in self.snapshots

    def find_person_ids(
        self,
        criteria: PersonResearchCriteria,
        *,
        restrict_to: Sequence[int] | None = None,
    ) -> list[int]:
        self.seen_criteria.append(criteria)
        self.restrictions.append(None if restrict_to is None else list(restrict_to))
        return [pid for pid in self.person_ids if restrict_to is None or pid in restrict_to]

    def get_latest_classifications(
        self, person_ids: Sequence[int]
    ) -> dict[int, PersecutionClassification]:
        return {pid: self.classifications[pid] for pid in person_ids if pid in self.classifications}

    def get_rosfinmonitoring(
        self, person_ids: Sequence[int], snapshot_id: int
    ) -> dict[int, ResearchRosfinmonitoring]:
        return {
            pid: ResearchRosfinmonitoring(
                snapshot_id=snapshot_id,
                status=self.rf_statuses.get(pid, RosfinmonitoringStatus.NO_MATCH_RECORD),
            )
            for pid in person_ids
        }

    def get_person_details(self, person_ids: Sequence[int]) -> dict[int, PersonResearchDetails]:
        self.detail_requests.append(list(person_ids))
        return {
            pid: PersonResearchDetails(
                person=Person(
                    id=pid,
                    canonical_name=f"Person {pid}",
                    normalized_name=f"person {pid}",
                    matching_key=f"person|{pid}",
                )
            )
            for pid in person_ids
        }


@dataclass
class FakeCandidateQuery:
    candidate_ids: list[int]
    calls: list[dict[str, object]] = field(default_factory=list)

    def get_candidates(
        self,
        snapshot_id: int,
        *,
        min_persecution_confidence: float = DEFAULT_MIN_PERSECUTION_CONFIDENCE,
        limit: int | None = 100,
        include_rf_statuses: frozenset[RosfinmonitoringStatus] = frozenset(
            {RosfinmonitoringStatus.NOT_MATCHED}
        ),
    ) -> CandidateQueryResult:
        self.calls.append(
            {
                "snapshot_id": snapshot_id,
                "min_persecution_confidence": min_persecution_confidence,
                "limit": limit,
                "include_rf_statuses": include_rf_statuses,
            }
        )
        return CandidateQueryResult(
            snapshot_id=snapshot_id,
            candidates=[
                PoliticalPersecutionCandidate(
                    person_id=pid,
                    canonical_name=f"Person {pid}",
                    normalized_name=f"person {pid}",
                    persecution_status="political",
                    persecution_confidence=0.9,
                    rosfinmonitoring_status=next(iter(include_rf_statuses)),
                )
                for pid in self.candidate_ids
            ],
        )


def _classification(
    person_id: int, status: PersecutionClassificationStatus, confidence: float = 0.9
) -> PersecutionClassification:
    return PersecutionClassification(
        person_id=person_id,
        status=status,
        confidence=confidence,
        classifier_name="rule-based",
        classifier_version="1.0.0",
    )


def _request(limit: int = 20, **criteria: object) -> ResearchRequest:
    return ResearchRequest(
        object_type=ResearchObjectType.PERSON,
        criteria=PersonResearchCriteria.model_validate(criteria),
        limit=limit,
    )


def _ids(service: ResearchService, request: ResearchRequest) -> list[int]:
    return [result.person.id or 0 for result in service.execute(request).results]


def test_political_not_matched_delegates_to_candidate_query() -> None:
    repository = FakeRepository(
        person_ids=[1, 2, 3, 4],
        classifications={pid: _classification(pid, POLITICAL) for pid in [1, 2, 3, 4]},
        rf_statuses={
            1: RosfinmonitoringStatus.MATCHED,
            2: RosfinmonitoringStatus.NOT_MATCHED,
            3: RosfinmonitoringStatus.NOT_MATCHED,
        },
    )
    # The candidate query is the single source of truth for this business
    # question: whatever it returns is what research returns.
    candidate_query = FakeCandidateQuery(candidate_ids=[3])
    service = ResearchService(repository=repository, candidate_query=candidate_query)

    ids = _ids(
        service,
        _request(
            persecution_status="political", rosfinmonitoring_status="not_matched", snapshot_id=3
        ),
    )

    assert ids == [3]
    assert candidate_query.calls == [
        {
            "snapshot_id": 3,
            "min_persecution_confidence": DEFAULT_MIN_PERSECUTION_CONFIDENCE,
            "limit": None,
            "include_rf_statuses": frozenset({RosfinmonitoringStatus.NOT_MATCHED}),
        }
    ]


def test_candidate_query_result_is_intersected_with_other_criteria() -> None:
    repository = FakeRepository(person_ids=[2, 5])
    candidate_query = FakeCandidateQuery(candidate_ids=[1, 2, 3])
    service = ResearchService(repository=repository, candidate_query=candidate_query)

    ids = _ids(
        service,
        _request(
            name="Person",
            persecution_status="political",
            persecution_min_confidence=0.4,
            rosfinmonitoring_status="ambiguous",
            snapshot_id=3,
        ),
    )

    assert ids == [2]
    assert candidate_query.calls[0]["min_persecution_confidence"] == 0.4
    assert candidate_query.calls[0]["include_rf_statuses"] == frozenset(
        {RosfinmonitoringStatus.AMBIGUOUS}
    )
    assert repository.seen_criteria[0].name == "Person"


def test_non_political_status_filter_does_not_use_candidate_query() -> None:
    repository = FakeRepository(
        person_ids=[1, 2, 3],
        classifications={
            1: _classification(1, POLITICAL),
            2: _classification(2, NON_POLITICAL),
        },
        rf_statuses={2: RosfinmonitoringStatus.NOT_MATCHED},
    )
    candidate_query = FakeCandidateQuery(candidate_ids=[])
    service = ResearchService(repository=repository, candidate_query=candidate_query)

    ids = _ids(
        service,
        _request(
            persecution_status="non_political", rosfinmonitoring_status="not_matched", snapshot_id=3
        ),
    )

    assert ids == [2]
    assert candidate_query.calls == []


def test_political_without_rf_filter_uses_default_threshold() -> None:
    repository = FakeRepository(
        person_ids=[1, 2, 3],
        classifications={
            1: _classification(1, POLITICAL, confidence=0.95),
            2: _classification(2, POLITICAL, confidence=0.5),
            3: _classification(3, UNCERTAIN, confidence=0.99),
        },
    )
    service = ResearchService(repository=repository, candidate_query=FakeCandidateQuery([]))

    assert _ids(service, _request(persecution_status="political")) == [1]
    assert _ids(
        service, _request(persecution_status="political", persecution_min_confidence=0.5)
    ) == [1, 2]


@pytest.mark.parametrize(
    "unclear_status",
    [
        RosfinmonitoringStatus.AMBIGUOUS,
        RosfinmonitoringStatus.NEEDS_REVIEW,
        RosfinmonitoringStatus.INSUFFICIENT_DATA,
        RosfinmonitoringStatus.NO_MATCH_RECORD,
    ],
)
def test_unclear_rf_status_is_not_treated_as_not_matched(
    unclear_status: RosfinmonitoringStatus,
) -> None:
    repository = FakeRepository(
        person_ids=[1],
        classifications={1: _classification(1, NON_POLITICAL)},
        rf_statuses={1: unclear_status},
    )
    service = ResearchService(repository=repository, candidate_query=FakeCandidateQuery([]))

    ids = _ids(
        service,
        _request(
            persecution_status="non_political", rosfinmonitoring_status="not_matched", snapshot_id=3
        ),
    )

    assert ids == []


def test_limit_truncates_results_but_reports_total_matched() -> None:
    repository = FakeRepository(person_ids=[1, 2, 3, 4, 5])
    service = ResearchService(repository=repository, candidate_query=FakeCandidateQuery([]))

    response = service.execute(_request(limit=2))

    assert [r.person.id for r in response.results] == [1, 2]
    assert response.total_matched == 5
    # Details are loaded only for the returned page.
    assert repository.detail_requests == [[1, 2]]


def test_result_carries_persecution_rf_status_and_review_warnings() -> None:
    repository = FakeRepository(
        person_ids=[1],
        classifications={1: _classification(1, UNCERTAIN)},
        rf_statuses={1: RosfinmonitoringStatus.AMBIGUOUS},
    )
    service = ResearchService(repository=repository, candidate_query=FakeCandidateQuery([]))

    (result,) = service.execute(_request(snapshot_id=3)).results

    assert result.persecution is not None
    assert result.persecution.status is UNCERTAIN
    assert result.rosfinmonitoring is not None
    assert result.rosfinmonitoring.status is RosfinmonitoringStatus.AMBIGUOUS
    assert [w.code for w in result.warnings] == [
        ResearchWarningCode.PERSECUTION_UNCERTAIN,
        ResearchWarningCode.ROSFIN_AMBIGUOUS,
    ]
    assert result.review_required is True


def test_without_snapshot_result_has_no_rosfinmonitoring_section() -> None:
    repository = FakeRepository(person_ids=[1], classifications={1: _classification(1, POLITICAL)})
    service = ResearchService(repository=repository, candidate_query=FakeCandidateQuery([]))

    (result,) = service.execute(_request()).results

    assert result.rosfinmonitoring is None
    assert result.warnings == []
    assert result.review_required is False


def test_unknown_snapshot_is_an_error() -> None:
    service = ResearchService(
        repository=FakeRepository(person_ids=[1]), candidate_query=FakeCandidateQuery([1])
    )

    with pytest.raises(ResearchSnapshotNotFoundError, match="99"):
        service.execute(_request(snapshot_id=99))


def test_response_echoes_request_and_object_type() -> None:
    service = ResearchService(
        repository=FakeRepository(person_ids=[]), candidate_query=FakeCandidateQuery([])
    )
    request = _request()

    response = service.execute(request)

    assert response.object_type is ResearchObjectType.PERSON
    assert response.request == request
    assert response.results == []
    assert response.total_matched == 0


class VanishingRepository(FakeRepository):
    """A person matched by the filters is gone by the time details are loaded."""

    def get_person_details(self, person_ids: Sequence[int]) -> dict[int, PersonResearchDetails]:
        details = super().get_person_details(person_ids)
        details.pop(1, None)
        return details


def test_person_removed_between_queries_is_skipped_not_crashing() -> None:
    service = ResearchService(
        repository=VanishingRepository(person_ids=[1, 2]),
        candidate_query=FakeCandidateQuery([]),
    )

    response = service.execute(_request())

    assert [r.person.id for r in response.results] == [2]
    # total_matched counts matches at filtering time, before details were loaded.
    assert response.total_matched == 2


# --- semantic candidate pool --------------------------------------------------------


def test_semantic_query_without_candidates_is_rejected_not_ignored() -> None:
    service = ResearchService(
        repository=FakeRepository(person_ids=[1, 2]), candidate_query=FakeCandidateQuery([])
    )

    with pytest.raises(ResearchCandidatesRequiredError):
        service.execute(_request(semantic_query="антивоенные публикации"))


def test_candidates_restrict_the_population_and_keep_retrieval_order() -> None:
    repository = FakeRepository(
        person_ids=[1, 2, 3, 4],
        classifications={pid: _classification(pid, POLITICAL) for pid in [1, 2, 3, 4]},
    )
    service = ResearchService(repository=repository, candidate_query=FakeCandidateQuery([]))

    response = service.execute(
        _request(semantic_query="антивоенные публикации", persecution_status="political"),
        candidate_person_ids=[4, 9, 2, 4],
    )

    assert [result.person.id for result in response.results] == [4, 2]
    assert response.total_matched == 2
    assert repository.restrictions == [[4, 9, 2]]


def test_status_criteria_still_apply_to_semantic_candidates() -> None:
    # A top semantic candidate that is not POLITICAL must not appear.
    repository = FakeRepository(
        person_ids=[1, 2, 3],
        classifications={
            1: _classification(1, UNCERTAIN),
            2: _classification(2, POLITICAL),
            3: _classification(3, POLITICAL, confidence=0.5),
        },
    )
    service = ResearchService(repository=repository, candidate_query=FakeCandidateQuery([]))

    response = service.execute(
        _request(semantic_query="пикеты", persecution_status="political"),
        candidate_person_ids=[1, 3, 2],
    )

    assert [result.person.id for result in response.results] == [2]


def test_political_not_matched_product_query_applies_inside_the_pool() -> None:
    repository = FakeRepository(person_ids=[1, 2, 3])
    candidate_query = FakeCandidateQuery([1, 3])
    service = ResearchService(repository=repository, candidate_query=candidate_query)

    response = service.execute(
        _request(
            semantic_query="пикеты",
            persecution_status="political",
            rosfinmonitoring_status="not_matched",
            snapshot_id=3,
        ),
        candidate_person_ids=[3, 2],
    )

    assert [result.person.id for result in response.results] == [3]
    assert len(candidate_query.calls) == 1


def test_empty_candidate_pool_returns_no_results_without_queries() -> None:
    repository = FakeRepository(person_ids=[1])
    service = ResearchService(repository=repository, candidate_query=FakeCandidateQuery([]))

    response = service.execute(_request(semantic_query="x"), candidate_person_ids=[])

    assert (response.results, response.total_matched, repository.restrictions) == ([], 0, [])


def test_structured_execute_without_candidates_is_unchanged() -> None:
    repository = FakeRepository(person_ids=[2, 1])
    service = ResearchService(repository=repository, candidate_query=FakeCandidateQuery([]))

    assert [r.person.id for r in service.execute(_request()).results] == [2, 1]
    assert repository.restrictions == [None]
