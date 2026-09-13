from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orm_models import PersonAliasRecord, PersonMergeRecord, PersonRecord
from person_models import AliasOrigin, MergeStatus, PersonStatus


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
            person = PersonRecord(
                canonical_name=canonical_name,
                normalized_name=normalized_name,
                matching_key=matching_key,
                status=PersonStatus.ACTIVE.value,
            )
            session.add(person)
            session.flush()
            return person.id

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

    def find_person_by_matching_key(self, matching_key: str) -> int | None:
        with self._session_factory() as session:
            person = session.scalar(
                select(PersonRecord).where(
                    PersonRecord.matching_key == matching_key,
                    PersonRecord.status == PersonStatus.ACTIVE.value,
                )
            )
            return person.id if person else None

    def merge_persons(
        self,
        *,
        source_person_id: int,
        target_person_id: int,
        reason: str | None = None,
    ) -> int:
        with self._session_factory.begin() as session:
            source = session.scalar(select(PersonRecord).where(PersonRecord.id == source_person_id))
            if source is None:
                raise ValueError(f"Source person {source_person_id} not found")

            target = session.scalar(select(PersonRecord).where(PersonRecord.id == target_person_id))
            if target is None:
                raise ValueError(f"Target person {target_person_id} not found")

            source.status = PersonStatus.MERGED.value
            source.merged_into_id = target_person_id
            source.updated_at = datetime.now(UTC)

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
