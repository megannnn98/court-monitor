"""Service for finding politically persecuted persons absent from Rosfinmonitoring."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from analysis import read as analysis_read
from analysis.rosfinmonitoring import read as rf_read
from persons import read as person_read
from results.candidates.models import (
    DEFAULT_MIN_PERSECUTION_CONFIDENCE,
    CandidateQueryResult,
    PoliticalPersecutionCandidate,
    RosfinmonitoringStatus,
    resolve_rosfinmonitoring_status,
)

# A person only counts as "absent from Rosfinmonitoring" when a match was
# actually run and came back NOT_MATCHED. NO_MATCH_RECORD (never checked),
# AMBIGUOUS, NEEDS_REVIEW, INSUFFICIENT_DATA and MATCHED_PROBABLE (in the
# operator's Airtable list, not in the published one) are not confirmed
# absences and must not be included by default.
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
        if rf_read.snapshot(session, snapshot_id) is None:
            raise ValueError(f"Rosfinmonitoring snapshot {snapshot_id} not found")
        # Every active person with a political persecution classification, in id order:
        # `limit` pages must be stable between calls.
        classifications = analysis_read.political_classifications(
            session, min_confidence=min_persecution_confidence
        )
        people = person_read.persons(session, [item.person_id for item in classifications])
        persecution_results = sorted(
            (
                item
                for item in classifications
                if item.person_id in people and people[item.person_id].status == "active"
            ),
            key=lambda item: item.person_id,
        )
        person_ids = [row.person_id for row in persecution_results]

        # One query per concern, not per person (was 3 queries per candidate).
        matches = analysis_read.person_matches(session, snapshot_id, person_ids)

        included = []
        for row in persecution_results:
            match = matches.get(row.person_id)
            # No match record means matching was never run for this person —
            # that is NOT a confirmed absence, so it is excluded by default just
            # like AMBIGUOUS/NEEDS_REVIEW/INSUFFICIENT_DATA.
            rf_status = resolve_rosfinmonitoring_status(None if match is None else match.status)
            if rf_status not in include_rf_statuses:
                continue
            included.append((row, rf_status, None if match is None else match.confidence))
            if limit is not None and len(included) >= limit:
                break

        stats = person_read.person_stats(session, [row.person_id for row, _, _ in included])

        candidates = [
            PoliticalPersecutionCandidate(
                person_id=row.person_id,
                canonical_name=people[row.person_id].canonical_name,
                normalized_name=people[row.person_id].normalized_name,
                persecution_status=row.status,
                persecution_confidence=row.confidence,
                persecution_reasons=row.reasons or [],
                rosfinmonitoring_status=rf_status,
                rosfinmonitoring_match_confidence=rf_confidence,
                event_count=stats[row.person_id].event_count,
                alias_count=stats[row.person_id].alias_count,
                last_event_date=stats[row.person_id].last_event_date,
            )
            for row, rf_status, rf_confidence in included
        ]

        return CandidateQueryResult(
            snapshot_id=snapshot_id,
            candidates=candidates,
            total_count=len(candidates),
        )
