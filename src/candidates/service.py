"""Service for finding politically persecuted persons absent from Rosfinmonitoring."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from candidates.models import (
    DEFAULT_MIN_PERSECUTION_CONFIDENCE,
    CandidateQueryResult,
    PoliticalPersecutionCandidate,
    RosfinmonitoringStatus,
    resolve_rosfinmonitoring_status,
)
from db.orm_models import (
    ExtractedEventRecord,
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    RosfinMatchRecord,
    RosfinmonitoringSnapshotRecord,
)
from persecution.queries import latest_persecution_classification_ids

# A person only counts as "absent from Rosfinmonitoring" when a match was
# actually run and came back NOT_MATCHED. NO_MATCH_RECORD (never checked),
# AMBIGUOUS, NEEDS_REVIEW and INSUFFICIENT_DATA are not confirmed absences
# and must not be included by default.
DEFAULT_INCLUDED_RF_STATUSES: frozenset[RosfinmonitoringStatus] = frozenset(
    {RosfinmonitoringStatus.NOT_MATCHED}
)


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
        min_persecution_confidence: float = DEFAULT_MIN_PERSECUTION_CONFIDENCE,
        limit: int | None = 100,
        include_rf_statuses: frozenset[RosfinmonitoringStatus] = DEFAULT_INCLUDED_RF_STATUSES,
        session: Session | None = None,
    ) -> CandidateQueryResult:
        """Get politically persecuted persons absent from Rosfinmonitoring.

        Args:
            snapshot_id: Rosfinmonitoring snapshot to check against
            min_persecution_confidence: Minimum confidence for persecution classification
            limit: Maximum number of candidates to return (None = no limit)
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
        limit: int | None,
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
                PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()),
                PersecutionClassificationRecord.status == "political",
                PersecutionClassificationRecord.confidence >= min_persecution_confidence,
                PersonRecord.status == "active",
            )
        )

        # Ordered: `limit` pages must be stable between calls.
        persecution_results = session.execute(persecution_query.order_by(PersonRecord.id)).all()
        person_ids = [row.person_id for row in persecution_results]

        # One query per concern, not per person (was 3 queries per candidate).
        matches = (
            {
                person_id: (status, confidence)
                for person_id, status, confidence in session.execute(
                    select(
                        RosfinMatchRecord.person_id,
                        RosfinMatchRecord.status,
                        RosfinMatchRecord.confidence,
                    ).where(
                        RosfinMatchRecord.snapshot_id == snapshot_id,
                        RosfinMatchRecord.person_id.in_(person_ids),
                    )
                ).all()
            }
            if person_ids
            else {}
        )

        included = []
        for row in persecution_results:
            match = matches.get(row.person_id)
            # No match record means matching was never run for this person —
            # that is NOT a confirmed absence, so it is excluded by default just
            # like AMBIGUOUS/NEEDS_REVIEW/INSUFFICIENT_DATA.
            rf_status = resolve_rosfinmonitoring_status(None if match is None else match[0])
            if rf_status not in include_rf_statuses:
                continue
            included.append((row, rf_status, None if match is None else match[1]))
            if limit is not None and len(included) >= limit:
                break

        included_ids = [row.person_id for row, _, _ in included]
        event_stats = (
            {
                person_id: (event_count, last_event_date)
                for person_id, event_count, last_event_date in session.execute(
                    select(
                        PersonEventLinkRecord.person_id,
                        func.count(PersonEventLinkRecord.id),
                        func.max(ExtractedEventRecord.event_date),
                    )
                    .join(
                        ExtractedEventRecord,
                        PersonEventLinkRecord.event_id == ExtractedEventRecord.id,
                    )
                    .where(PersonEventLinkRecord.person_id.in_(included_ids))
                    .group_by(PersonEventLinkRecord.person_id)
                ).all()
            }
            if included_ids
            else {}
        )
        alias_counts = (
            dict(
                session.execute(
                    select(PersonAliasRecord.person_id, func.count(PersonAliasRecord.id))
                    .where(PersonAliasRecord.person_id.in_(included_ids))
                    .group_by(PersonAliasRecord.person_id)
                )
                .tuples()
                .all()
            )
            if included_ids
            else {}
        )

        candidates = [
            PoliticalPersecutionCandidate(
                person_id=row.person_id,
                canonical_name=row.canonical_name,
                normalized_name=row.normalized_name,
                persecution_status=row.persecution_status,
                persecution_confidence=row.persecution_confidence,
                persecution_reasons=row.persecution_reasons or [],
                rosfinmonitoring_status=rf_status,
                rosfinmonitoring_match_confidence=rf_confidence,
                event_count=event_stats.get(row.person_id, (0, None))[0],
                alias_count=alias_counts.get(row.person_id, 0),
                last_event_date=event_stats.get(row.person_id, (0, None))[1],
            )
            for row, rf_status, rf_confidence in included
        ]

        return CandidateQueryResult(
            snapshot_id=snapshot_id,
            candidates=candidates,
            total_count=len(candidates),
        )
