"""Service for finding politically persecuted persons absent from Rosfinmonitoring."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from candidate_query_models import (
    CandidateQueryResult,
    PoliticalPersecutionCandidate,
    RosfinmonitoringStatus,
)
from orm_models import (
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    RosfinMatchRecord,
)


class CandidateQueryService:
    """Service for the main product query."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_candidates(
        self,
        snapshot_id: int,
        *,
        min_persecution_confidence: float = 0.7,
        limit: int = 100,
    ) -> CandidateQueryResult:
        """Get politically persecuted persons absent from Rosfinmonitoring.

        Args:
            snapshot_id: Rosfinmonitoring snapshot to check against
            min_persecution_confidence: Minimum confidence for persecution classification
            limit: Maximum number of candidates to return

        Returns:
            CandidateQueryResult with list of candidates
        """
        with self._session_factory() as session:
            # Get all persons with political persecution classification
            persecution_query = (
                select(
                    PersonRecord.id.label("person_id"),
                    PersonRecord.canonical_name,
                    PersonRecord.normalized_name,
                    PersecutionClassificationRecord.status.label("persecution_status"),
                    PersecutionClassificationRecord.confidence.label("persecution_confidence"),
                    PersecutionClassificationRecord.reasons.label("persecution_reasons"),
                )
                .join(
                    PersecutionClassificationRecord,
                    PersonRecord.id == PersecutionClassificationRecord.person_id,
                )
                .where(
                    PersecutionClassificationRecord.status == "political",
                    PersecutionClassificationRecord.confidence >= min_persecution_confidence,
                    PersonRecord.status == "active",
                )
            )

            persecution_results = session.execute(persecution_query).all()

            candidates: list[PoliticalPersecutionCandidate] = []

            for row in persecution_results:
                person_id = row.person_id

                # Get Rosfinmonitoring match status
                match_record = session.scalar(
                    select(RosfinMatchRecord).where(
                        RosfinMatchRecord.person_id == person_id,
                        RosfinMatchRecord.snapshot_id == snapshot_id,
                    )
                )

                # Determine Rosfinmonitoring status
                if match_record is None:
                    rf_status = RosfinmonitoringStatus.NO_MATCH_RECORD
                    rf_confidence = None
                elif match_record.status == "matched":
                    rf_status = RosfinmonitoringStatus.MATCHED
                    rf_confidence = match_record.confidence
                elif match_record.status == "ambiguous":
                    rf_status = RosfinmonitoringStatus.AMBIGUOUS
                    rf_confidence = match_record.confidence
                elif match_record.status == "needs_review":
                    rf_status = RosfinmonitoringStatus.NEEDS_REVIEW
                    rf_confidence = match_record.confidence
                else:
                    rf_status = RosfinmonitoringStatus.NOT_IN_LIST
                    rf_confidence = match_record.confidence

                # Skip if matched to Rosfinmonitoring
                if rf_status == RosfinmonitoringStatus.MATCHED:
                    continue

                # Get event count and last event date
                event_stats = session.execute(
                    select(
                        func.count(PersonEventLinkRecord.id).label("event_count"),
                        func.max(PersonEventLinkRecord.created_at).label("last_event_date"),
                    ).where(PersonEventLinkRecord.person_id == person_id)
                ).one()

                # Get alias count
                alias_count = session.scalar(
                    select(func.count(PersonAliasRecord.id)).where(
                        PersonAliasRecord.person_id == person_id
                    )
                )

                # Parse persecution reasons
                persecution_reasons = row.persecution_reasons or []

                candidate = PoliticalPersecutionCandidate(
                    person_id=person_id,
                    canonical_name=row.canonical_name,
                    normalized_name=row.normalized_name,
                    persecution_status=row.persecution_status,
                    persecution_confidence=row.persecution_confidence,
                    persecution_reasons=persecution_reasons,
                    rosfinmonitoring_status=rf_status,
                    rosfinmonitoring_match_confidence=rf_confidence,
                    event_count=event_stats.event_count or 0,
                    alias_count=alias_count or 0,
                    last_event_date=event_stats.last_event_date,
                )

                candidates.append(candidate)

                if len(candidates) >= limit:
                    break

            return CandidateQueryResult(
                snapshot_id=snapshot_id,
                candidates=candidates,
                total_count=len(candidates),
            )
