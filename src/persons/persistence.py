from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityMentionRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonMergeRecord,
    PersonRecord,
)
from persons.models import AliasOrigin, MergeStatus, PersonStatus


class PersonMergeConflictError(ValueError):
    """A merge participant is no longer active (e.g. merged by a concurrent reviewer)."""


class SqlAlchemyPersonPersistence:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_person(
        self,
        *,
        canonical_name: str,
        normalized_name: str,
        matching_key: str,
    ) -> int:
        with self._session_factory.begin() as session:
            return self.create_person_in_session(
                session,
                canonical_name=canonical_name,
                normalized_name=normalized_name,
                matching_key=matching_key,
            )

    def create_person_in_session(
        self,
        session: Session,
        *,
        canonical_name: str,
        normalized_name: str,
        matching_key: str,
    ) -> int:
        person = PersonRecord(
            canonical_name=canonical_name,
            normalized_name=normalized_name,
            matching_key=matching_key,
            status=PersonStatus.ACTIVE.value,
        )
        session.add(person)
        session.flush()
        return person.id

    def create_person_with_alias(
        self,
        *,
        canonical_name: str,
        normalized_name: str,
        matching_key: str,
        surface_text: str,
        alias_normalized_text: str,
        origin: AliasOrigin,
        confidence: float,
        source_mention_id: int | None = None,
    ) -> int:
        with self._session_factory.begin() as session:
            person_id = self.create_person_in_session(
                session,
                canonical_name=canonical_name,
                normalized_name=normalized_name,
                matching_key=matching_key,
            )
            self.create_alias_in_session(
                session,
                person_id=person_id,
                surface_text=surface_text,
                normalized_text=alias_normalized_text,
                matching_key=matching_key,
                origin=origin,
                confidence=confidence,
                source_mention_id=source_mention_id,
            )
            return person_id

    def create_alias(
        self,
        *,
        person_id: int,
        surface_text: str,
        normalized_text: str,
        matching_key: str,
        origin: AliasOrigin,
        confidence: float,
        source_mention_id: int | None = None,
    ) -> int:
        with self._session_factory.begin() as session:
            return self.create_alias_in_session(
                session,
                person_id=person_id,
                surface_text=surface_text,
                normalized_text=normalized_text,
                matching_key=matching_key,
                origin=origin,
                confidence=confidence,
                source_mention_id=source_mention_id,
            )

    def create_alias_in_session(
        self,
        session: Session,
        *,
        person_id: int,
        surface_text: str,
        normalized_text: str,
        matching_key: str,
        origin: AliasOrigin,
        confidence: float,
        source_mention_id: int | None = None,
    ) -> int:
        alias = PersonAliasRecord(
            person_id=person_id,
            surface_text=surface_text,
            normalized_text=normalized_text,
            matching_key=matching_key,
            origin=origin.value,
            confidence=confidence,
            source_mention_id=source_mention_id,
        )
        session.add(alias)
        session.flush()
        return alias.id

    def find_persons_by_matching_key_in_session(
        self,
        session: Session,
        matching_key: str,
    ) -> list[int]:
        """Active persons with this key, by id: zero, one or several namesakes.

        A lookup for candidate generation, not an identity decision (ADR 0012).
        """
        return list(
            session.scalars(
                select(PersonRecord.id)
                .where(
                    PersonRecord.matching_key == matching_key,
                    PersonRecord.status == PersonStatus.ACTIVE.value,
                )
                .order_by(PersonRecord.id)
            ).all()
        )

    def add_alias_if_not_exists_in_session(
        self,
        session: Session,
        *,
        person_id: int,
        surface_text: str,
        normalized_text: str,
        matching_key: str,
        origin: AliasOrigin,
        confidence: float,
        source_mention_id: int | None = None,
    ) -> bool:
        """Add an alias unless the person already has this surface form; True if added."""
        try:
            # A SAVEPOINT so a duplicate-alias IntegrityError only rolls back this
            # insert, not the caller's whole transaction.
            with session.begin_nested():
                self.create_alias_in_session(
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
            return False
        return True

    def merge_persons(
        self,
        *,
        source_person_id: int,
        target_person_id: int,
        reason: str | None = None,
    ) -> int:
        with self._session_factory.begin() as session:
            return self.merge_persons_in_session(
                session,
                source_person_id=source_person_id,
                target_person_id=target_person_id,
                reason=reason,
            )

    def merge_persons_in_session(
        self,
        session: Session,
        *,
        source_person_id: int,
        target_person_id: int,
        reason: str | None = None,
    ) -> int:
        """Merge `source` into `target` inside the caller's transaction (audited).

        Both rows are locked in id order, so concurrent merges of the same person
        serialize (the loser sees it is no longer active) and A→B vs B→A cannot deadlock.
        """
        if source_person_id == target_person_id:
            raise PersonMergeConflictError(f"cannot merge person {source_person_id} into itself")
        locked = {
            person.id: person
            for person in session.scalars(
                select(PersonRecord)
                .where(PersonRecord.id.in_((source_person_id, target_person_id)))
                .order_by(PersonRecord.id)
                .with_for_update()
            ).all()
        }
        source = locked.get(source_person_id)
        if source is None:
            raise ValueError(f"Source person {source_person_id} not found")

        target = locked.get(target_person_id)
        if target is None:
            raise ValueError(f"Target person {target_person_id} not found")

        for person in (source, target):
            if person.status != PersonStatus.ACTIVE.value:
                raise PersonMergeConflictError(
                    f"person {person.id} is {person.status}, not active; cannot merge"
                )

        source.status = PersonStatus.MERGED.value
        source.merged_into_id = target_person_id
        source.updated_at = datetime.now(UTC)

        session.execute(
            update(EntityMentionRecord)
            .where(EntityMentionRecord.person_id == source_person_id)
            .values(person_id=target_person_id)
        )

        source_links = list(
            session.scalars(
                select(PersonEventLinkRecord).where(
                    PersonEventLinkRecord.person_id == source_person_id
                )
            ).all()
        )
        for link in source_links:
            existing_link = session.scalar(
                select(PersonEventLinkRecord).where(
                    PersonEventLinkRecord.person_id == target_person_id,
                    PersonEventLinkRecord.event_id == link.event_id,
                    PersonEventLinkRecord.role == link.role,
                )
            )
            if existing_link is None:
                link.person_id = target_person_id
            else:
                session.delete(link)

        source_aliases = list(
            session.scalars(
                select(PersonAliasRecord).where(PersonAliasRecord.person_id == source_person_id)
            ).all()
        )
        for alias in source_aliases:
            existing_alias = session.scalar(
                select(PersonAliasRecord).where(
                    PersonAliasRecord.person_id == target_person_id,
                    PersonAliasRecord.surface_text == alias.surface_text,
                )
            )
            if existing_alias is None:
                alias.person_id = target_person_id
            else:
                session.execute(delete(PersonAliasRecord).where(PersonAliasRecord.id == alias.id))

        merge_record = PersonMergeRecord(
            source_person_id=source_person_id,
            target_person_id=target_person_id,
            status=MergeStatus.APPLIED.value,
            reason=reason,
            applied_at=datetime.now(UTC),
        )
        session.add(merge_record)
        session.flush()
        return merge_record.id

    def list_aliases_for_person(self, person_id: int) -> list[PersonAliasRecord]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(PersonAliasRecord).where(PersonAliasRecord.person_id == person_id)
                ).all()
            )

    def get_person(self, person_id: int) -> PersonRecord | None:
        with self._session_factory() as session:
            return session.scalar(select(PersonRecord).where(PersonRecord.id == person_id))

    def list_active_persons(self, limit: int = 100) -> list[PersonRecord]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(PersonRecord)
                    .where(PersonRecord.status == PersonStatus.ACTIVE.value)
                    .order_by(PersonRecord.id)
                    .limit(limit)
                ).all()
            )
