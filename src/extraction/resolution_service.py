from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
    PersonEventLinkRecord,
)
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.models import PersonResolutionAction
from persons.resolution.service import (
    PersonResolutionService,
    identity_from_mention,
)
from persons.resolver import RuleBasedPersonResolver


@dataclass(frozen=True)
class ResolutionStats:
    mentions_processed: int = 0
    mentions_resolved: int = 0
    new_persons_created: int = 0
    events_linked: int = 0
    # Mentions left unlinked until a human review decision (ER v2 REVIEW).
    reviews_pending: int = 0


class ExtractionResolutionService:
    def __init__(
        self,
        *,
        persistence: SqlAlchemyPersonPersistence,
        resolver: RuleBasedPersonResolver,
        session_factory: sessionmaker[Session],
        person_resolution: PersonResolutionService | None = None,
    ) -> None:
        self._persistence = persistence
        self._resolver = resolver
        self._session_factory = session_factory
        if person_resolution is None:
            from persons.resolution.factory import build_person_resolution_service

            person_resolution = build_person_resolution_service(
                session_factory, persistence=persistence, resolver=resolver
            )
        self._person_resolution = person_resolution

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
            # extraction → identity normalization → exact fast-path → ER v2 → decision
            identities = [
                identity
                for mention in person_mentions
                if (identity := identity_from_mention(mention)) is not None
            ]
            self._person_resolution.lock_identity_blocks(session, identities)

            for mention in person_mentions:
                outcome = self._person_resolution.resolve_mention(session, mention)
                if outcome is None:
                    continue
                if outcome.person_id is not None:
                    mention_id_to_person_id[mention.id] = outcome.person_id
                stats = ResolutionStats(
                    mentions_processed=stats.mentions_processed,
                    mentions_resolved=stats.mentions_resolved
                    + (1 if outcome.person_id is not None else 0),
                    new_persons_created=stats.new_persons_created
                    + (1 if outcome.created_person else 0),
                    events_linked=stats.events_linked,
                    reviews_pending=stats.reviews_pending
                    + (
                        1
                        if outcome.action is PersonResolutionAction.REVIEW
                        and outcome.person_id is None
                        else 0
                    ),
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
                reviews_pending=stats.reviews_pending,
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
