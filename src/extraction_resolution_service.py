from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orm_models import (
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
    PersonEventLinkRecord,
)
from person_models import AliasOrigin, ResolutionStatus
from person_persistence import SqlAlchemyPersonPersistence
from person_resolver import RuleBasedPersonResolver


@dataclass(frozen=True)
class ResolutionStats:
    mentions_processed: int = 0
    mentions_resolved: int = 0
    new_persons_created: int = 0
    events_linked: int = 0


class ExtractionResolutionService:
    def __init__(
        self,
        *,
        persistence: SqlAlchemyPersonPersistence,
        resolver: RuleBasedPersonResolver,
        session_factory: sessionmaker[Session],
    ) -> None:
        self._persistence = persistence
        self._resolver = resolver
        self._session_factory = session_factory

    def resolve_extraction_run(self, extraction_run_id: int) -> ResolutionStats:
        with self._session_factory.begin() as session:
            person_mentions = list(
                session.scalars(
                    select(EntityMentionRecord).where(
                        EntityMentionRecord.extraction_run_id == extraction_run_id,
                        EntityMentionRecord.entity_type == "person",
                    )
                ).all()
            )

            stats = ResolutionStats(
                mentions_processed=len(person_mentions),
            )

            mention_id_to_person_id: dict[int, int] = {}

            for mention in person_mentions:
                normalized_data = mention.normalized_data
                matching_key_raw = normalized_data.get("matching_key", "")
                if not matching_key_raw or not isinstance(matching_key_raw, str):
                    continue

                matching_key = matching_key_raw
                full_name_raw = normalized_data.get("full_name", mention.normalized_text)
                normalized_text = (
                    full_name_raw if isinstance(full_name_raw, str) else mention.normalized_text
                )

                resolution = self._resolver.resolve_and_create(
                    normalized_text=normalized_text,
                    matching_key=matching_key,
                    surface_text=mention.surface_text,
                    origin=AliasOrigin.EXTRACTION,
                    confidence=mention.confidence,
                    source_mention_id=mention.id,
                    session=session,
                )

                if resolution.person_id is not None:
                    mention.person_id = resolution.person_id
                    mention_id_to_person_id[mention.id] = resolution.person_id
                    stats = ResolutionStats(
                        mentions_processed=stats.mentions_processed,
                        mentions_resolved=stats.mentions_resolved + 1,
                        new_persons_created=stats.new_persons_created
                        + (1 if resolution.status is ResolutionStatus.NEW_PERSON else 0),
                        events_linked=stats.events_linked,
                    )

            events = list(
                session.scalars(
                    select(ExtractedEventRecord).where(
                        ExtractedEventRecord.extraction_run_id == extraction_run_id,
                    )
                ).all()
            )

            events_linked = stats.events_linked
            for event in events:
                entity_links = list(
                    session.scalars(
                        select(EventEntityMentionRecord).where(
                            EventEntityMentionRecord.event_id == event.id,
                        )
                    ).all()
                )

                for entity_link in entity_links:
                    person_id = mention_id_to_person_id.get(entity_link.mention_id)
                    if person_id is None:
                        mention_record = session.scalar(
                            select(EntityMentionRecord).where(
                                EntityMentionRecord.id == entity_link.mention_id,
                            )
                        )
                        if mention_record is not None and mention_record.person_id is not None:
                            person_id = mention_record.person_id

                    if person_id is not None:
                        existing = session.scalar(
                            select(PersonEventLinkRecord).where(
                                PersonEventLinkRecord.person_id == person_id,
                                PersonEventLinkRecord.event_id == event.id,
                                PersonEventLinkRecord.role == entity_link.role,
                            )
                        )
                        if existing is None:
                            session.add(
                                PersonEventLinkRecord(
                                    person_id=person_id,
                                    event_id=event.id,
                                    role=entity_link.role,
                                    confidence=event.confidence,
                                )
                            )
                            events_linked += 1

            stats = ResolutionStats(
                mentions_processed=stats.mentions_processed,
                mentions_resolved=stats.mentions_resolved,
                new_persons_created=stats.new_persons_created,
                events_linked=events_linked,
            )

            return stats

    def get_person_events(
        self,
        person_id: int,
        *,
        limit: int = 100,
    ) -> list[ExtractedEventRecord]:
        with self._session_factory() as session:
            event_ids = session.scalars(
                select(PersonEventLinkRecord.event_id).where(
                    PersonEventLinkRecord.person_id == person_id,
                )
            ).all()

            if not event_ids:
                return []

            return list(
                session.scalars(
                    select(ExtractedEventRecord)
                    .where(ExtractedEventRecord.id.in_(event_ids))
                    .order_by(ExtractedEventRecord.event_date.nulls_last())
                    .limit(limit)
                ).all()
            )

    def get_person_mentions(
        self,
        person_id: int,
        *,
        limit: int = 200,
    ) -> list[EntityMentionRecord]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(EntityMentionRecord)
                    .where(EntityMentionRecord.person_id == person_id)
                    .order_by(EntityMentionRecord.id)
                    .limit(limit)
                ).all()
            )
