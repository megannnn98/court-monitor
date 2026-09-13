from __future__ import annotations

from person_models import (
    AliasOrigin,
    ResolutionContext,
    ResolutionResult,
    ResolutionStatus,
)
from person_persistence import SqlAlchemyPersonPersistence


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
    ) -> ResolutionResult:
        existing_person_id = self._persistence.find_person_by_matching_key(matching_key)

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
    ) -> ResolutionResult:
        resolution = self.resolve(
            normalized_text=normalized_text,
            matching_key=matching_key,
            surface_text=surface_text,
            context=context,
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
            )
            return resolution

        person_id = self._persistence.create_person(
            canonical_name=normalized_text,
            normalized_name=normalized_text,
            matching_key=matching_key,
        )

        self._persistence.create_alias(
            person_id=person_id,
            surface_text=surface_text,
            normalized_text=normalized_text,
            matching_key=matching_key,
            origin=origin,
            confidence=confidence,
            source_mention_id=source_mention_id,
        )

        return ResolutionResult(
            person_id=person_id,
            status=ResolutionStatus.NEW_PERSON,
            confidence=1.0,
            reasons=["created new person"],
        )

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
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        try:
            self._persistence.create_alias(
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
