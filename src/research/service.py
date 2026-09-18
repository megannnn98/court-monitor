"""Deterministic research over canonical persons.

`ResearchService` is the stable backend entry point for structured research
requests. It knows nothing about transport (CLI/HTTP), natural language or
LLMs: adapters build a `ResearchRequest` and serialize the `ResearchResponse`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from candidates.models import (
    DEFAULT_MIN_PERSECUTION_CONFIDENCE,
    CandidateQueryResult,
    RosfinmonitoringStatus,
)
from persecution.models import PersecutionClassification, PersecutionClassificationStatus
from persons.models import Person, PersonAlias
from research.mapping import build_warnings
from research.models import (
    PersonResearchCriteria,
    PersonResearchResult,
    ResearchEvent,
    ResearchEvidence,
    ResearchRequest,
    ResearchResponse,
    ResearchRosfinmonitoring,
    ResearchSource,
)

if TYPE_CHECKING:
    from research.unit_of_work import ResearchReaders, ResearchUnitOfWork


class ResearchCandidatesRequiredError(ValueError):
    """`semantic_query` needs candidate ids from semantic retrieval.

    Raised instead of silently ignoring the criterion and returning a broader
    result than the user asked for.
    """

    def __init__(self) -> None:
        super().__init__(
            "criteria.semantic_query requires candidate_person_ids from semantic retrieval"
        )


class ResearchSnapshotNotFoundError(LookupError):
    def __init__(self, snapshot_id: int) -> None:
        super().__init__(f"Rosfinmonitoring snapshot {snapshot_id} not found")
        self.snapshot_id = snapshot_id


class PersonResearchDetails(BaseModel):
    """Everything about one person that is independent of the snapshot."""

    person: Person
    aliases: list[PersonAlias] = Field(default_factory=list)
    events: list[ResearchEvent] = Field(default_factory=list)
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    # More mentions/events exist than the per-person evidence bound returned.
    evidence_truncated: bool = False


class PersonResearchRepository(Protocol):
    def snapshot_exists(self, snapshot_id: int) -> bool: ...

    def find_person_ids(
        self,
        criteria: PersonResearchCriteria,
        *,
        restrict_to: Sequence[int] | None = None,
    ) -> list[int]:
        """Active person ids ordered by id matching person_id, name,
        event_types/date range and source, optionally only among `restrict_to`
        (a bounded candidate pool). Persecution, Rosfinmonitoring and
        semantic criteria are NOT applied here — the service owns those rules.
        """
        ...

    def get_latest_classifications(
        self, person_ids: Sequence[int]
    ) -> dict[int, PersecutionClassification]: ...

    def get_rosfinmonitoring(
        self, person_ids: Sequence[int], snapshot_id: int
    ) -> dict[int, ResearchRosfinmonitoring]:
        """Status for every requested id (NO_MATCH_RECORD when never matched)."""
        ...

    def get_person_details(self, person_ids: Sequence[int]) -> dict[int, PersonResearchDetails]: ...


class CandidateQuery(Protocol):
    """The existing POLITICAL-and-RF-status product query (CandidateQueryService)."""

    def get_candidates(
        self,
        snapshot_id: int,
        *,
        min_persecution_confidence: float = ...,
        limit: int | None = ...,
        include_rf_statuses: frozenset[RosfinmonitoringStatus] = ...,
    ) -> CandidateQueryResult: ...


class ResearchService:
    """Deterministic research. Each `execute` reads through one unit of work: with
    `SqlAlchemyResearchUnitOfWork`, one read-only REPEATABLE READ transaction, so the
    answer never mixes two moments of the database.

    `repository` and `candidate_query` given directly are used as they are (test
    doubles); production callers pass `unit_of_work`.
    """

    def __init__(
        self,
        *,
        unit_of_work: ResearchUnitOfWork | None = None,
        repository: PersonResearchRepository | None = None,
        candidate_query: CandidateQuery | None = None,
    ) -> None:
        from research.unit_of_work import FixedResearchReaders, ResearchReaders

        if unit_of_work is None:
            if repository is None or candidate_query is None:
                raise ValueError("pass unit_of_work, or both repository and candidate_query")
            unit_of_work = FixedResearchReaders(ResearchReaders(repository, candidate_query))
        elif repository is not None or candidate_query is not None:
            raise ValueError("pass unit_of_work or repository/candidate_query, not both")
        self._unit_of_work = unit_of_work

    def execute(
        self,
        request: ResearchRequest,
        *,
        candidate_person_ids: Sequence[int] | None = None,
    ) -> ResearchResponse:
        """Run the request. With `candidate_person_ids` (semantic retrieval
        output, best first) only those persons are considered, every
        criterion is still applied from PostgreSQL, and results keep the
        candidate order. `total_matched` then counts matches inside the pool.
        """
        criteria = request.criteria
        if criteria.semantic_query is not None and candidate_person_ids is None:
            raise ResearchCandidatesRequiredError()
        with self._unit_of_work.read() as readers:
            # The snapshot id may come from before this transaction (the workflow
            # resolves "the latest snapshot" first); its existence is checked here.
            if criteria.snapshot_id is not None and not readers.repository.snapshot_exists(
                criteria.snapshot_id
            ):
                raise ResearchSnapshotNotFoundError(criteria.snapshot_id)

            person_ids = self._filter_person_ids(readers, criteria, candidate_person_ids)
            page = person_ids[: request.limit]

            details = readers.repository.get_person_details(page)
            classifications = readers.repository.get_latest_classifications(page)
            rosfinmonitoring = (
                readers.repository.get_rosfinmonitoring(page, criteria.snapshot_id)
                if criteria.snapshot_id is not None
                else {}
            )

        results: list[PersonResearchResult] = []
        for person_id in page:
            person_details = details.get(person_id)
            if person_details is None:
                # Only with readers outside one snapshot (a person removed between
                # filtering and loading); skip rather than fail the whole request.
                continue
            persecution = classifications.get(person_id)
            rf = rosfinmonitoring.get(person_id)
            results.append(
                PersonResearchResult(
                    person=person_details.person,
                    aliases=person_details.aliases,
                    persecution=persecution,
                    rosfinmonitoring=rf,
                    events=person_details.events,
                    evidence=person_details.evidence,
                    sources=person_details.sources,
                    warnings=build_warnings(
                        persecution, rf, evidence_truncated=person_details.evidence_truncated
                    ),
                )
            )

        return ResearchResponse(
            object_type=request.object_type,
            request=request,
            results=results,
            total_matched=len(person_ids),
        )

    def _filter_person_ids(
        self,
        readers: ResearchReaders,
        criteria: PersonResearchCriteria,
        candidate_person_ids: Sequence[int] | None,
    ) -> list[int]:
        if candidate_person_ids is None:
            return self._apply_status_filters(
                readers, criteria, readers.repository.find_person_ids(criteria)
            )
        pool = list(dict.fromkeys(candidate_person_ids))
        if not pool:
            return []
        allowed = set(
            self._apply_status_filters(
                readers, criteria, readers.repository.find_person_ids(criteria, restrict_to=pool)
            )
        )
        return [person_id for person_id in pool if person_id in allowed]

    def _apply_status_filters(
        self, readers: ResearchReaders, criteria: PersonResearchCriteria, person_ids: list[int]
    ) -> list[int]:
        if (
            criteria.persecution_status is PersecutionClassificationStatus.POLITICAL
            and criteria.rosfinmonitoring_status is not None
            and criteria.snapshot_id is not None
        ):
            # "Politically persecuted AND <RF status>" is the existing product
            # query; reuse it instead of re-deriving its rules here.
            candidates = readers.candidate_query.get_candidates(
                criteria.snapshot_id,
                min_persecution_confidence=(
                    criteria.persecution_min_confidence
                    if criteria.persecution_min_confidence is not None
                    else DEFAULT_MIN_PERSECUTION_CONFIDENCE
                ),
                limit=None,
                include_rf_statuses=frozenset({criteria.rosfinmonitoring_status}),
            )
            allowed = {candidate.person_id for candidate in candidates.candidates}
            return [person_id for person_id in person_ids if person_id in allowed]

        if criteria.persecution_status is not None:
            classifications = readers.repository.get_latest_classifications(person_ids)
            threshold = criteria.effective_persecution_min_confidence
            person_ids = [
                person_id
                for person_id in person_ids
                if (classification := classifications.get(person_id)) is not None
                and classification.status is criteria.persecution_status
                and (threshold is None or classification.confidence >= threshold)
            ]

        if criteria.rosfinmonitoring_status is not None and criteria.snapshot_id is not None:
            statuses = readers.repository.get_rosfinmonitoring(person_ids, criteria.snapshot_id)
            person_ids = [
                person_id
                for person_id in person_ids
                if statuses[person_id].status is criteria.rosfinmonitoring_status
            ]

        return person_ids
