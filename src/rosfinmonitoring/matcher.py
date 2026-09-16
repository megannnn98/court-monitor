"""Rule-based Person ↔ Rosfinmonitoring matcher implementation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import NamedTuple

from rapidfuzz.distance import Levenshtein
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    PersonAliasRecord,
    PersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from rosfinmonitoring.matcher_models import (
    RosfinCandidateEntry,
    RosfinMatchResult,
    RosfinMatchStatus,
    RosfinmonitoringMatcher,
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class _MatchIdentity(NamedTuple):
    """One name/key pair to score against RF entries — the canonical
    person's own identity, or one of their aliases."""

    normalized_text: str
    matching_key: str


# Below this many full words (initials do not count), a normalized_name (e.g. a
# bare surname, «Виталий Л.») is too thin
# to reliably rule a person in or out of a snapshot — zero retrieved
# candidates then means the check was insufficient, not that the person is
# confirmed absent.
MIN_NAME_WORDS_FOR_RELIABLE_CHECK = 2

# Absence-of-evidence is not evidence of absence: even a reliable check that
# finds zero candidates only supports NOT_MATCHED probabilistically, not with
# certainty (a spelling variant or non-Cyrillic name never asked for, birth
# dates not on file, etc.).
NOT_MATCHED_CONFIDENCE = 0.8

# Scoring weights: an exact matching_key match alone is strong evidence;
# high name-word overlap (Jaccard) adds a smaller amount on top, and only
# once it's already fairly high itself.
EXACT_KEY_MATCH_SCORE = 0.8
NAME_SIMILARITY_THRESHOLD = 0.8
NAME_SIMILARITY_WEIGHT = 0.2

# A candidate needs at least this combined score to be considered at all.
CANDIDATE_SCORE_THRESHOLD = 0.5

# At or above this score, a single unambiguous top candidate is MATCHED.
MATCHED_SCORE_THRESHOLD = 0.95

# Two candidates within this margin of each other are too close to call —
# AMBIGUOUS, not an arbitrary pick of whichever sorted first.
AMBIGUITY_SCORE_MARGIN = 0.1

# A matching birth date on top of an already-strong name/key match makes the
# match more certain; a known, differing birth date on the same name means
# it's more likely a namesake than the same person, so it must not reach
# MATCHED_SCORE_THRESHOLD (above) on name alone.
BIRTH_DATE_MATCH_BONUS = 0.05
BIRTH_DATE_MISMATCH_SCORE_CAP = 0.5


def _same_word_stem(first: str, second: str) -> bool:
    """Equal up to a Russian case ending (at most two trailing letters differ), or
    up to a fleeting vowel/soft sign: «лев»/«льва», «орел»/«орла»."""
    first, second = (
        first.replace("ь", "").replace("ъ", ""),
        second.replace("ь", "").replace("ъ", ""),
    )
    common = 0
    for left, right in zip(first, second, strict=False):
        if left != right:
            break
        common += 1
    if common >= max(3, min(len(first), len(second)) - 2):
        return True
    # Errs towards NEEDS_REVIEW: a similar first name next to the same surname is
    # reviewed rather than confirmed absent.
    return common >= 1 and Levenshtein.distance(first, second) <= 2


def _name_words(name: str) -> list[str]:
    """Lowercase words; a hyphenated surname declines in each part."""
    return name.lower().replace("ё", "е").replace("-", " ").split()


def _full_word_count(name: str) -> int:
    """Name words that are not initials: «виталий л.» has one."""
    return sum(1 for word in name.split() if len(word.strip(".")) > 1)


def _is_name_variant(person_name: str, entry_name: str) -> bool:
    """Every word of the shorter name matches a word of the other up to its ending."""
    person_words = _name_words(person_name)
    entry_words = _name_words(entry_name)
    if len(person_words) < MIN_NAME_WORDS_FOR_RELIABLE_CHECK or len(entry_words) < 2:
        return False
    shorter, longer = sorted((person_words, entry_words), key=len)
    return all(any(_same_word_stem(word, other) for other in longer) for word in shorter)


class RuleBasedRosfinmonitoringMatcher(RosfinmonitoringMatcher):
    """Rule-based matcher for persons against Rosfinmonitoring entries."""

    # Reported by evaluations; bump when matching rules change.
    matcher_name = "rule-based-rosfinmonitoring-matcher"
    # 1.1.0: an inflected/reordered name variant is NEEDS_REVIEW, never NOT_MATCHED.
    # 1.2.0: fleeting vowels/soft signs («Лев»/«Льва») and hyphenated surnames are variants.
    # 1.3.0: an initial is not a name word for the "too few words" rule («Виталий Л.»).
    matcher_version = "1.3.0"

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def match_person(
        self,
        person_id: int,
        snapshot_id: int,
        *,
        person_birth_date: datetime | None = None,
    ) -> RosfinMatchResult:
        """Match a person against Rosfinmonitoring entries in a snapshot.

        `person_birth_date` is an optional signal the caller can supply when
        it has one. `PersonRecord` does not carry a birth date today (nothing
        in extraction/normalization currently produces one), so real
        pipeline calls always pass `None` here; this parameter exists so the
        conservative birth-date rules below are ready the moment that
        changes, without another signature change.
        """
        result = self._match_person(person_id, snapshot_id, person_birth_date=person_birth_date)
        # Persisted with the result: a rule change recomputes older results.
        return result.model_copy(
            update={"matcher_name": self.matcher_name, "matcher_version": self.matcher_version}
        )

    def _match_person(
        self,
        person_id: int,
        snapshot_id: int,
        *,
        person_birth_date: datetime | None,
    ) -> RosfinMatchResult:
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
            # The canonical person is always at least one identity to score
            # against, whether or not they have any aliases yet — aliases
            # extend this set, they aren't required for it to be non-empty.
            identities = [
                _MatchIdentity(
                    normalized_text=person.normalized_name, matching_key=person.matching_key
                )
            ]
            identities.extend(
                _MatchIdentity(
                    normalized_text=alias.normalized_text, matching_key=alias.matching_key
                )
                for alias in aliases
            )
            identity_keys = {identity.matching_key for identity in identities}
            identity_names = {identity.normalized_text for identity in identities}
            # Individual name words too, so a reordered/partial name (not an
            # exact matching_key/normalized_name match) is still fetched for
            # the Jaccard similarity scoring below instead of being excluded
            # before it ever runs.
            identity_words = {
                word for name in identity_names for word in _name_words(name) if len(word) >= 3
            }

            rf_entries = []
            if identity_keys or identity_names or identity_words:
                # Stems, not whole words: «кузнецова» must also fetch «кузнецов».
                word_conditions = [
                    RosfinmonitoringEntryRecord.normalized_name.ilike(
                        f"%{_escape_like(word[: max(3, len(word) - 2)])}%",
                    )
                    for word in identity_words
                ]
                rf_entries = list(
                    session.scalars(
                        select(RosfinmonitoringEntryRecord)
                        .where(
                            RosfinmonitoringEntryRecord.snapshot_id == snapshot_id,
                            or_(
                                RosfinmonitoringEntryRecord.matching_key.in_(identity_keys),
                                RosfinmonitoringEntryRecord.normalized_name.in_(identity_names),
                                *word_conditions,
                            ),
                        )
                        .order_by(RosfinmonitoringEntryRecord.id)
                    ).all()
                )

            candidates: list[RosfinCandidateEntry] = []

            for identity in identities:
                for entry in rf_entries:
                    similarity, reasons = self._compute_similarity(
                        identity.normalized_text,
                        identity.matching_key,
                        entry.normalized_name,
                        entry.matching_key,
                        entry.birth_date,
                        person_birth_date,
                    )

                    if similarity > CANDIDATE_SCORE_THRESHOLD:
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

            # Multiple identities (canonical + aliases) can each score
            # against the same RF entry — keep only the best-scoring
            # candidate per entry_id, so one real entry never counts as two
            # "different" candidates and falsely triggers the AMBIGUOUS
            # tie-break below.
            best_by_entry_id: dict[int, RosfinCandidateEntry] = {}
            for candidate in candidates:
                existing = best_by_entry_id.get(candidate.entry_id)
                if existing is None or candidate.similarity_score > existing.similarity_score:
                    best_by_entry_id[candidate.entry_id] = candidate
            candidates = list(best_by_entry_id.values())

            candidates.sort(key=lambda c: c.similarity_score, reverse=True)

            if not candidates:
                if _full_word_count(person.normalized_name) < MIN_NAME_WORDS_FOR_RELIABLE_CHECK:
                    return RosfinMatchResult(
                        person_id=person_id,
                        snapshot_id=snapshot_id,
                        status=RosfinMatchStatus.INSUFFICIENT_DATA,
                        confidence=0.0,
                        reasons=[
                            "Person's normalized_name has too few full (non-initial) words to search"
                        ],
                        matched_at=datetime.now(UTC),
                    )
                variants = [
                    entry
                    for entry in rf_entries
                    if any(
                        _is_name_variant(identity.normalized_text, entry.normalized_name)
                        for identity in identities
                    )
                ]
                if variants:
                    # Every name word matches an entry up to a case ending or word
                    # order (e.g. an extraction normalizer left «Андрея Кузнецов»):
                    # scoring cannot confirm the match, but absence is not proven
                    # either. "Not in the list" must never be claimed here.
                    return RosfinMatchResult(
                        person_id=person_id,
                        snapshot_id=snapshot_id,
                        status=RosfinMatchStatus.NEEDS_REVIEW,
                        confidence=0.0,
                        candidate_entries=[
                            RosfinCandidateEntry(
                                entry_id=entry.id,
                                full_name=entry.full_name,
                                normalized_name=entry.normalized_name,
                                matching_key=entry.matching_key,
                                birth_date=entry.birth_date,
                                similarity_score=self._name_similarity(
                                    person.normalized_name, entry.normalized_name
                                ),
                                reasons=["Name variant (case endings or word order differ)"],
                            )
                            for entry in variants[:5]
                        ],
                        reasons=[
                            (
                                "Rosfinmonitoring entry is a name variant; "
                                "absence from the list cannot be confirmed"
                            )
                        ],
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

            # Checked before the MATCHED threshold: two distinct entries tied
            # (or near-tied) at a high score are namesakes to disambiguate,
            # not an arbitrary pick of "whichever sorted first".
            if (
                len(candidates) > 1
                and candidates[0].similarity_score - candidates[1].similarity_score
                < AMBIGUITY_SCORE_MARGIN
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

            if top_candidate.similarity_score >= MATCHED_SCORE_THRESHOLD:
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
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[RosfinMatchResult]:
        """Match all persons against Rosfinmonitoring entries in a snapshot.

        `on_progress` is called as `(done, total)` after each person, for a caller that
        reports progress; matching itself does not depend on it.
        """
        with self._session_factory() as session:
            query = select(PersonRecord.id).where(PersonRecord.merged_into_id.is_(None))
            if limit is not None:
                query = query.limit(limit)
            person_ids = session.scalars(query).all()

        results: list[RosfinMatchResult] = []
        for person_id in person_ids:
            result = self.match_person(person_id, snapshot_id)
            results.append(result)
            if on_progress is not None:
                on_progress(len(results), len(person_ids))

        return results

    def _compute_similarity(
        self,
        person_name: str,
        person_key: str,
        rf_name: str,
        rf_key: str,
        rf_birth_date: datetime | None,
        person_birth_date: datetime | None = None,
    ) -> tuple[float, list[str]]:
        """Compute similarity between person alias and RF entry.

        Conservative birth-date handling (only applied when both dates are
        known — see `match_person`'s docstring on why `person_birth_date` is
        usually `None` today):
        - same name + same birth date -> small confidence boost
        - same name + different known birth date -> score capped well below
          the auto-MATCHED threshold (likely a namesake, not a match)
        - birth date unknown on either side -> behavior unchanged
        """
        reasons: list[str] = []
        score = 0.0

        if person_key == rf_key:
            score += EXACT_KEY_MATCH_SCORE
            reasons.append("Exact matching_key match")

        name_similarity = self._name_similarity(person_name, rf_name)
        if name_similarity > NAME_SIMILARITY_THRESHOLD:
            score += NAME_SIMILARITY_WEIGHT * name_similarity
            reasons.append(f"High name similarity: {name_similarity:.2f}")

        if score > 0.0:
            score = min(score, 1.0)

        if person_birth_date is not None and rf_birth_date is not None and score > 0.0:
            if person_birth_date.date() == rf_birth_date.date():
                score = min(1.0, score + BIRTH_DATE_MATCH_BONUS)
                reasons.append("Birth date matches")
            else:
                score = min(score, BIRTH_DATE_MISMATCH_SCORE_CAP)
                reasons.append("Birth date differs — likely a namesake, not a match")

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
