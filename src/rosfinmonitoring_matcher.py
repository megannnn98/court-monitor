"""Rule-based Person ↔ Rosfinmonitoring matcher implementation."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from orm_models import (
    PersonAliasRecord,
    PersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from rosfinmonitoring_matcher_models import (
    RosfinCandidateEntry,
    RosfinMatchResult,
    RosfinMatchStatus,
    RosfinmonitoringMatcher,
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Below this many words, a normalized_name (e.g. a bare surname) is too thin
# to reliably rule a person in or out of a snapshot — zero retrieved
# candidates then means the check was insufficient, not that the person is
# confirmed absent.
MIN_NAME_WORDS_FOR_RELIABLE_CHECK = 2

# Absence-of-evidence is not evidence of absence: even a reliable check that
# finds zero candidates only supports NOT_MATCHED probabilistically, not with
# certainty (a spelling variant or non-Cyrillic name never asked for, birth
# dates not on file, etc.).
NOT_MATCHED_CONFIDENCE = 0.8


class RuleBasedRosfinmonitoringMatcher(RosfinmonitoringMatcher):
    """Rule-based matcher for persons against Rosfinmonitoring entries."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def match_person(
        self,
        person_id: int,
        snapshot_id: int,
    ) -> RosfinMatchResult:
        """Match a person against Rosfinmonitoring entries in a snapshot."""
        with self._session_factory() as session:
            person = session.scalar(select(PersonRecord).where(PersonRecord.id == person_id))
            if person is None:
                raise ValueError(f"Person {person_id} not found")
            snapshot = session.get(RosfinmonitoringSnapshotRecord, snapshot_id)
            if snapshot is None:
                raise ValueError(f"Rosfinmonitoring snapshot {snapshot_id} not found")

            aliases = session.scalars(
                select(PersonAliasRecord).where(PersonAliasRecord.person_id == person_id)
            ).all()
            alias_keys = {alias.matching_key for alias in aliases}
            alias_names = {alias.normalized_text for alias in aliases}
            alias_keys.add(person.matching_key)
            alias_names.add(person.normalized_name)
            # Individual name words too, so a reordered/partial name (not an
            # exact matching_key/normalized_name match) is still fetched for
            # the Jaccard similarity scoring below instead of being excluded
            # before it ever runs.
            alias_words = {
                word for name in alias_names for word in name.lower().split() if len(word) >= 3
            }

            rf_entries = []
            if alias_keys or alias_names or alias_words:
                word_conditions = [
                    RosfinmonitoringEntryRecord.normalized_name.ilike(
                        f"%{_escape_like(word)}%",
                    )
                    for word in alias_words
                ]
                rf_entries = list(
                    session.scalars(
                        select(RosfinmonitoringEntryRecord)
                        .where(
                            RosfinmonitoringEntryRecord.snapshot_id == snapshot_id,
                            or_(
                                RosfinmonitoringEntryRecord.matching_key.in_(alias_keys),
                                RosfinmonitoringEntryRecord.normalized_name.in_(alias_names),
                                *word_conditions,
                            ),
                        )
                        .order_by(RosfinmonitoringEntryRecord.id)
                    ).all()
                )

            candidates: list[RosfinCandidateEntry] = []

            for alias in aliases:
                for entry in rf_entries:
                    similarity, reasons = self._compute_similarity(
                        alias.normalized_text,
                        alias.matching_key,
                        entry.normalized_name,
                        entry.matching_key,
                        entry.birth_date,
                    )

                    if similarity > 0.5:
                        candidates.append(
                            RosfinCandidateEntry(
                                entry_id=entry.id,
                                full_name=entry.full_name,
                                normalized_name=entry.normalized_name,
                                matching_key=entry.matching_key,
                                birth_date=entry.birth_date,
                                similarity_score=similarity,
                                reasons=reasons,
                            )
                        )

            candidates.sort(key=lambda c: c.similarity_score, reverse=True)

            if not candidates:
                if len(person.normalized_name.split()) < MIN_NAME_WORDS_FOR_RELIABLE_CHECK:
                    return RosfinMatchResult(
                        person_id=person_id,
                        snapshot_id=snapshot_id,
                        status=RosfinMatchStatus.INSUFFICIENT_DATA,
                        confidence=0.0,
                        reasons=["Person's normalized_name has too few words to search reliably"],
                        matched_at=datetime.now(UTC),
                    )
                return RosfinMatchResult(
                    person_id=person_id,
                    snapshot_id=snapshot_id,
                    status=RosfinMatchStatus.NOT_MATCHED,
                    confidence=NOT_MATCHED_CONFIDENCE,
                    reasons=["No matching Rosfinmonitoring entries found"],
                    matched_at=datetime.now(UTC),
                )

            top_candidate = candidates[0]

            if top_candidate.similarity_score >= 0.95:
                return RosfinMatchResult(
                    person_id=person_id,
                    snapshot_id=snapshot_id,
                    status=RosfinMatchStatus.MATCHED,
                    confidence=top_candidate.similarity_score,
                    matched_entry_id=top_candidate.entry_id,
                    matched_entry_name=top_candidate.full_name,
                    candidate_entries=candidates[:5],
                    reasons=top_candidate.reasons,
                    matched_at=datetime.now(UTC),
                )

            if (
                len(candidates) > 1
                and candidates[0].similarity_score - candidates[1].similarity_score < 0.1
            ):
                return RosfinMatchResult(
                    person_id=person_id,
                    snapshot_id=snapshot_id,
                    status=RosfinMatchStatus.AMBIGUOUS,
                    confidence=candidates[0].similarity_score,
                    candidate_entries=candidates[:5],
                    reasons=["Multiple candidates with similar scores"],
                    matched_at=datetime.now(UTC),
                )

            return RosfinMatchResult(
                person_id=person_id,
                snapshot_id=snapshot_id,
                status=RosfinMatchStatus.NEEDS_REVIEW,
                confidence=top_candidate.similarity_score,
                candidate_entries=candidates[:5],
                reasons=["Match confidence below threshold"],
                matched_at=datetime.now(UTC),
            )

    def match_all_persons(
        self,
        snapshot_id: int,
        limit: int | None = None,
    ) -> list[RosfinMatchResult]:
        """Match all persons against Rosfinmonitoring entries in a snapshot."""
        with self._session_factory() as session:
            query = select(PersonRecord.id).where(PersonRecord.merged_into_id.is_(None))
            if limit is not None:
                query = query.limit(limit)
            person_ids = session.scalars(query).all()

        results: list[RosfinMatchResult] = []
        for person_id in person_ids:
            result = self.match_person(person_id, snapshot_id)
            results.append(result)

        return results

    def _compute_similarity(
        self,
        person_name: str,
        person_key: str,
        rf_name: str,
        rf_key: str,
        rf_birth_date: datetime | None,
    ) -> tuple[float, list[str]]:
        """Compute similarity between person alias and RF entry."""
        reasons: list[str] = []
        score = 0.0

        if person_key == rf_key:
            score += 0.8
            reasons.append("Exact matching_key match")

        name_similarity = self._name_similarity(person_name, rf_name)
        if name_similarity > 0.8:
            score += 0.2 * name_similarity
            reasons.append(f"High name similarity: {name_similarity:.2f}")

        if score > 0.0:
            score = min(score, 1.0)

        return score, reasons

    def _name_similarity(self, name1: str, name2: str) -> float:
        """Compute name similarity using Jaccard index on words."""
        words1 = set(name1.lower().split())
        words2 = set(name2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union)
