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
    ExtractedEventRecord,
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    RosfinMatchRecord,
    RosfinmonitoringSnapshotRecord,
)

# A person only counts as "absent from Rosfinmonitoring" when a match was
# actually run and came back NOT_MATCHED. NO_MATCH_RECORD (never checked),
# AMBIGUOUS, NEEDS_REVIEW and INSUFFICIENT_DATA are not confirmed absences
# and must not be included by default.
DEFAULT_INCLUDED_RF_STATUSES: frozenset[RosfinmonitoringStatus] = frozenset(
    {RosfinmonitoringStatus.NOT_MATCHED}
)

_MATCH_RECORD_STATUS_TO_RF_STATUS: dict[str, RosfinmonitoringStatus] = {
    "matched": RosfinmonitoringStatus.MATCHED,
    "not_matched": RosfinmonitoringStatus.NOT_MATCHED,
    "ambiguous": RosfinmonitoringStatus.AMBIGUOUS,
    "needs_review": RosfinmonitoringStatus.NEEDS_REVIEW,
    "insufficient_data": RosfinmonitoringStatus.INSUFFICIENT_DATA,
}


class CandidateQueryService:
    """Service for the main product query."""

    def __init__(self, session_factory_or_session: sessionmaker[Session] | Session) -> None:
        self._session_factory: sessionmaker[Session] | None = None
        self._session: Session | None = None

        if isinstance(session_factory_or_session, Session):
            self._session = session_factory_or_session
        else:
            self._session_factory = session_factory_or_session

    def get_candidates(
        self,
        snapshot_id: int,
        *,
        min_persecution_confidence: float = 0.7,
        limit: int = 100,
        include_rf_statuses: frozenset[RosfinmonitoringStatus] = DEFAULT_INCLUDED_RF_STATUSES,
        session: Session | None = None,
    ) -> CandidateQueryResult:
        """Get politically persecuted persons absent from Rosfinmonitoring.

        Args:
            snapshot_id: Rosfinmonitoring snapshot to check against
            min_persecution_confidence: Minimum confidence for persecution classification
            limit: Maximum number of candidates to return
            include_rf_statuses: Which Rosfinmonitoring statuses count as "absent" for
                this query. Defaults to NOT_MATCHED only — a confirmed absence. Widen
                this explicitly (e.g. to also review AMBIGUOUS/NEEDS_REVIEW cases) rather
                than treating "never checked" or "unclear" as "absent".
            session: Optional existing session to use (for API contexts)

        Returns:
            CandidateQueryResult with list of candidates
        """
        # Use provided session, or instance session, or create from factory
        if session is not None:
            return self._get_candidates_with_session(
                session, snapshot_id, min_persecution_confidence, limit, include_rf_statuses
            )
        elif self._session is not None:
            return self._get_candidates_with_session(
                self._session, snapshot_id, min_persecution_confidence, limit, include_rf_statuses
            )
        elif self._session_factory is not None:
            with self._session_factory() as new_session:
                return self._get_candidates_with_session(
                    new_session,
                    snapshot_id,
                    min_persecution_confidence,
                    limit,
                    include_rf_statuses,
                )
        else:
            raise ValueError("No session or session_factory available")

    def _get_candidates_with_session(
        self,
        session: Session,
        snapshot_id: int,
        min_persecution_confidence: float,
        limit: int,
        include_rf_statuses: frozenset[RosfinmonitoringStatus],
    ) -> CandidateQueryResult:
        """Internal implementation that works with an existing session."""
        snapshot = session.get(RosfinmonitoringSnapshotRecord, snapshot_id)
        if snapshot is None:
            raise ValueError(f"Rosfinmonitoring snapshot {snapshot_id} not found")

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

            # Determine Rosfinmonitoring status. No match record means
            # matching was never run for this person — that is NOT a
            # confirmed absence, so it is excluded by default just like
            # AMBIGUOUS/NEEDS_REVIEW/INSUFFICIENT_DATA.
            if match_record is None:
                rf_status = RosfinmonitoringStatus.NO_MATCH_RECORD
                rf_confidence = None
            else:
                rf_status = _MATCH_RECORD_STATUS_TO_RF_STATUS.get(
                    match_record.status,
                    RosfinmonitoringStatus.NEEDS_REVIEW,
                )
                rf_confidence = match_record.confidence

            if rf_status not in include_rf_statuses:
                continue

            # Get event count and last event date
            event_stats = session.execute(
                select(
                    func.count(PersonEventLinkRecord.id).label("event_count"),
                    func.max(ExtractedEventRecord.event_date).label("last_event_date"),
                )
                .join(
                    ExtractedEventRecord,
                    PersonEventLinkRecord.event_id == ExtractedEventRecord.id,
                )
                .where(PersonEventLinkRecord.person_id == person_id)
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
