from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from persons.models import (
    AliasOrigin,
    ResolutionContext,
    ResolutionResult,
    ResolutionStatus,
)
from persons.persistence import SqlAlchemyPersonPersistence


class RuleBasedPersonResolver:
    def __init__(self, persistence: SqlAlchemyPersonPersistence) -> None:
        self._persistence = persistence

    def resolve(
        self,
        *,
        normalized_text: str,
        matching_key: str,
        surface_text: str,
        context: ResolutionContext | None = None,
        session: Session | None = None,
    ) -> ResolutionResult:
        """Resolve a mention to a canonical person by exact `matching_key`
        lookup — this is an exact deterministic baseline, not fuzzy entity
        resolution (see docs/adr/0005-entity-resolution-strategy.md).

        `surface_text` and `context` are accepted (and required by the
        `PersonResolver` protocol other resolvers may implement) but unused
        here: this baseline only needs `matching_key` to decide MATCHED vs.
        NEW_PERSON. `context` (article/city/court/event_types) is a ready
        extension point for a future context-aware resolver — e.g. two
        namesakes disambiguated by which court or city they're associated
        with — not a currently-used signal.
        """
        if session is None:
            existing_person_id = self._persistence.find_person_by_matching_key(matching_key)
        else:
            existing_person_id = self._persistence.find_person_by_matching_key_in_session(
                session,
                matching_key,
            )

        if existing_person_id is not None:
            return ResolutionResult(
                person_id=existing_person_id,
                status=ResolutionStatus.MATCHED,
                confidence=1.0,
                reasons=["matching_key match"],
            )

        return ResolutionResult(
            person_id=None,
            status=ResolutionStatus.NEW_PERSON,
            confidence=1.0,
            reasons=["no existing person found"],
        )

    def resolve_and_create(
        self,
        *,
        normalized_text: str,
        matching_key: str,
        surface_text: str,
        origin: AliasOrigin,
        confidence: float,
        context: ResolutionContext | None = None,
        source_mention_id: int | None = None,
        session: Session | None = None,
    ) -> ResolutionResult:
        resolution = self.resolve(
            normalized_text=normalized_text,
            matching_key=matching_key,
            surface_text=surface_text,
            context=context,
            session=session,
        )

        if resolution.status is ResolutionStatus.MATCHED:
            assert resolution.person_id is not None
            self._add_alias_if_not_exists(
                person_id=resolution.person_id,
                surface_text=surface_text,
                normalized_text=normalized_text,
                matching_key=matching_key,
                origin=origin,
                confidence=confidence,
                source_mention_id=source_mention_id,
                session=session,
            )
            return resolution

        person_id = self._create_person_and_alias_racing_safe(
            normalized_text=normalized_text,
            matching_key=matching_key,
            surface_text=surface_text,
            origin=origin,
            confidence=confidence,
            source_mention_id=source_mention_id,
            session=session,
        )

        return ResolutionResult(
            person_id=person_id,
            status=ResolutionStatus.NEW_PERSON,
            confidence=1.0,
            reasons=["created new person"],
        )

    def _create_person_and_alias_racing_safe(
        self,
        *,
        normalized_text: str,
        matching_key: str,
        surface_text: str,
        origin: AliasOrigin,
        confidence: float,
        source_mention_id: int | None,
        session: Session | None,
    ) -> int:
        """Create a new person + alias for a matching_key that `resolve()`
        just reported as unseen.

        `uq_persons_matching_key_active` can still reject the insert if a
        concurrent resolver created the same person in the meantime; in
        that case we back off to the winner instead of raising or leaving
        a duplicate canonical person behind.
        """
        if session is None:
            try:
                return self._persistence.create_person_with_alias(
                    canonical_name=normalized_text,
                    normalized_name=normalized_text,
                    matching_key=matching_key,
                    surface_text=surface_text,
                    alias_normalized_text=normalized_text,
                    origin=origin,
                    confidence=confidence,
                    source_mention_id=source_mention_id,
                )
            except IntegrityError:
                winner_id = self._persistence.find_person_by_matching_key(matching_key)
                if winner_id is None:
                    raise
        else:
            try:
                with session.begin_nested():
                    person_id = self._persistence.create_person_in_session(
                        session,
                        canonical_name=normalized_text,
                        normalized_name=normalized_text,
                        matching_key=matching_key,
                    )
                    self._persistence.create_alias_in_session(
                        session,
                        person_id=person_id,
                        surface_text=surface_text,
                        normalized_text=normalized_text,
                        matching_key=matching_key,
                        origin=origin,
                        confidence=confidence,
                        source_mention_id=source_mention_id,
                    )
                return person_id
            except IntegrityError:
                winner_id = self._persistence.find_person_by_matching_key_in_session(
                    session,
                    matching_key,
                )
                if winner_id is None:
                    raise

        self._add_alias_if_not_exists(
            person_id=winner_id,
            surface_text=surface_text,
            normalized_text=normalized_text,
            matching_key=matching_key,
            origin=origin,
            confidence=confidence,
            source_mention_id=source_mention_id,
            session=session,
        )
        return winner_id

    def _add_alias_if_not_exists(
        self,
        *,
        person_id: int,
        surface_text: str,
        normalized_text: str,
        matching_key: str,
        origin: AliasOrigin,
        confidence: float,
        source_mention_id: int | None = None,
        session: Session | None = None,
    ) -> None:
        try:
            if session is None:
                self._persistence.create_alias(
                    person_id=person_id,
                    surface_text=surface_text,
                    normalized_text=normalized_text,
                    matching_key=matching_key,
                    origin=origin,
                    confidence=confidence,
                    source_mention_id=source_mention_id,
                )
            else:
                # A SAVEPOINT so a duplicate-alias IntegrityError only rolls
                # back this insert, not the caller's whole ambient
                # transaction (Postgres aborts the entire transaction on an
                # uncaught error otherwise).
                with session.begin_nested():
                    self._persistence.create_alias_in_session(
                        session,
                        person_id=person_id,
                        surface_text=surface_text,
                        normalized_text=normalized_text,
                        matching_key=matching_key,
                        origin=origin,
                        confidence=confidence,
                        source_mention_id=source_mention_id,
                    )
        except IntegrityError:
            pass
